"""Layout building blocks shared by every template.

A template never writes coordinates by hand. It decides which units go
where (a column, a corner, a row) and asks this module to size and place
them. Sizing works on one global scale `s`, applied to everything relative to
the source, so the type hierarchy survives for free; templates search for the
largest `s` that fits.

Two ways to resize a unit:

- rigid: images, buttons, badges, stickers. Every member box scales about
  the unit's visible rect by the same factor, which keeps image proportions
  and keeps a label centred in its pill.
- reflow: a free text element. The font scales with `s` and the box can get
  wider than a plain scale would allow, so a headline rewraps into fewer
  lines when the target has room sideways.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .formats import classify
from .measure import measure_text
from .parse import Design, Rect, Unit, box, overlap_area, rotated_box, union
from .roles import BACKDROP_ROLES, RoleInfo, infer_roles

Patch = dict[str, dict[str, Any]]  # element id -> fields to overwrite

# Smallest font a role may be set at, in px. Taken from score.TYPE_FLOORS so
# the layout never produces something the scorer calls unreadable.
FLOOR_PX = {"headline": 14, "cta": 12, "subhead": 12, "badge": 10, "sticker": 10, "fineprint": 8}
DEFAULT_FLOOR = 10

# Units that sit in a corner rather than in the flow of a layout.
CORNER_ROLES = {"sticker", "misc_image"}


@dataclass
class Ctx:
    """The source design plus everything a template needs about the target."""
    src: Design
    W: int
    H: int
    roles: dict[str, RoleInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.roles:
            self.roles = infer_roles(self.src)
        short = min(self.W, self.H)
        self.margin = max(4.0, 0.05 * short)
        self.min_gap = max(2.0, 0.02 * short)
        self.cls = classify(self.W, self.H)
        # Content may grow with the canvas, but only so far: past this a
        # poster turned into a story would get a headline as wide as a wall.
        area_ratio = (self.W * self.H) / (self.src.width * self.src.height)
        self.s_cap = 1.15 * math.sqrt(area_ratio)
        # A logo shrunk to a few pixels is the brand gone, whatever the scale says.
        self.logo_min_h = max(14.0, 0.06 * short)
        # Source gaps are capped before scaling. A gap that held the hero image
        # is meaningless once the hero moves to its own column.
        self.src_gap_cap = 0.06 * min(self.src.width, self.src.height)
        # Per-role multipliers on FLOOR_PX, set by fit_stack when nothing fits.
        self.floor_relax: dict[str, float] = {}

    def role(self, u: Unit) -> str:
        return self.roles[u.uid].role

    def units_with(self, roles: set[str]) -> list[Unit]:
        return [u for u in self.src.units if self.role(u) in roles]

    def flow_units(self, exclude: set[str] = frozenset()) -> list[Unit]:
        skip = BACKDROP_ROLES | CORNER_ROLES | exclude
        return [u for u in self.src.units if self.role(u) not in skip]

    @property
    def canvas(self) -> Rect:
        return (0.0, 0.0, float(self.W), float(self.H))

    @property
    def inner(self) -> Rect:
        m = self.margin
        return (m, m, self.W - m, self.H - m)


@dataclass
class Block:
    """Something sized and ready to put down with its top-left at (x, y)."""
    w: float
    h: float
    place: Callable[[float, float], Patch]


# ------------------------------------------------------------------ units

def free_text_id(ctx: Ctx, u: Unit) -> Optional[str]:
    if len(u.members) == 1 and ctx.src.els[u.members[0]]["type"] == "text":
        return u.members[0]
    return None


def floor_for(ctx: Ctx, u: Unit) -> int:
    role = ctx.role(u)
    return max(1, round(FLOOR_PX.get(role, DEFAULT_FLOOR) * ctx.floor_relax.get(role, 1.0)))


# When a layout cannot fit at full floors, the least important text gives
# way first. Headlines go last and never below 80%.
FLOOR_RELAX_STEPS = [
    {},
    {"fineprint": 0.75, "badge": 0.8, "sticker": 0.8},
    {"fineprint": 0.6, "badge": 0.7, "sticker": 0.7, "subhead": 0.8, "cta": 0.85, "headline": 0.9},
    {"fineprint": 0.5, "badge": 0.6, "sticker": 0.6, "subhead": 0.7, "cta": 0.75, "headline": 0.8},
]


def rigid_patch(ctx: Ctx, u: Unit, x: float, y: float, k: float) -> Patch:
    """Scale every member of `u` by k about its visible rect, landing that
    rect's top-left at (x, y)."""
    d = ctx.src
    rx, ry = u.rect[0], u.rect[1]
    patch: Patch = {}
    for m in u.members:
        el = d.els[m]
        p: dict[str, Any] = {
            "x": x + (el["x"] - rx) * k,
            "y": y + (el["y"] - ry) * k,
            "width": el["width"] * k,
            "height": el["height"] * k,
        }
        if el["type"] == "text":
            p["font_size"] = _label_font(el, el["font_size"] * k, p["width"])
            if el.get("letter_spacing"):
                p["letter_spacing"] = el["letter_spacing"] * k
        elif el["type"] == "shape":
            for f in ("corner_radius", "stroke_width"):
                if el.get(f):
                    p[f] = el[f] * k
        patch[m] = p
    return patch


def _label_font(el: dict[str, Any], size: float, width: float) -> int:
    """Whole-number size at or under `size` whose wrap is no worse than the
    source's. Rounding a label down can otherwise still leave a line one
    glyph too wide, or wrap a one-line button into two."""
    want_lines = measure_text(el).n_lines
    font = max(1, int(size))
    while font > 1:
        block = measure_text(el, font, width)
        if block.width <= width + 0.5 and block.n_lines <= want_lines:
            break
        font -= 1
    return font


def size_rigid(ctx: Ctx, u: Unit, s: float, max_w: float, max_h: float = math.inf) -> Block:
    w0, h0 = u.rect[2] - u.rect[0], u.rect[3] - u.rect[1]
    k = s
    for m in u.members:  # a label must not drop under its readability floor
        el = ctx.src.els[m]
        if el["type"] == "text":
            k = max(k, floor_for(ctx, u) / el["font_size"])
    if ctx.role(u) == "logo":
        k = max(k, ctx.logo_min_h / max(h0, 1e-6))
    # The raw boxes, padding and all, must still fit the canvas, or the clamp
    # in compose() shrinks the unit after the layout already counted on it.
    F = _footprint(ctx, u)
    k = min(k, max_w / max(w0, 1e-6), max_h / max(h0, 1e-6),
            ctx.W / max(F[2] - F[0], 1e-6), ctx.H / max(F[3] - F[1], 1e-6))
    b = Block(w0 * k, h0 * k, lambda x, y: rigid_patch(ctx, u, x, y, k))
    b.k = k  # type: ignore[attr-defined]  # strip needs it to work out padding
    return b


def size_text(ctx: Ctx, u: Unit, s: float, max_w: float, widen: float = 1.0,
              align: Optional[str] = None) -> Block:
    eid = free_text_id(ctx, u)
    el = ctx.src.els[eid]
    floor = floor_for(ctx, u)
    font = max(floor, int(el["font_size"] * s))
    # When the floor holds the font up, the box must grow with the font, not
    # with s, or small scales wrap into ever more lines and the stack gets
    # taller as s shrinks, which breaks the bisection in fit_stack.
    k = max(s, font / el["font_size"])
    width = max(1.0, min(max_w, el["width"] * k * widen))
    block = measure_text(el, font, width)
    # a single word wider than the box only goes away by getting smaller
    while block.width > width + 0.5 and font > floor:
        font -= 1
        block = measure_text(el, font, width)
    height = math.ceil(block.height) + 1

    def place(x: float, y: float) -> Patch:
        p = {"x": x, "y": y, "width": width, "height": height, "font_size": font}
        if align:
            p["text_align"] = align
        return {eid: p}

    return Block(width, height, place)


def size_unit(ctx: Ctx, u: Unit, s: float, max_w: float, widen: float = 1.0,
              align: Optional[str] = None) -> Block:
    if free_text_id(ctx, u):
        return size_text(ctx, u, s, max_w, widen, align)
    return size_rigid(ctx, u, s, max_w)


# --------------------------------------------------------- rows and stacks

@dataclass
class Row:
    """Units that shared a horizontal band in the source, as clusters of
    neighbours (a badge set is one cluster) with an anchor each."""
    clusters: list[list[Unit]]
    anchors: list[str]  # "left" | "center" | "right"
    src_rect: Rect


def _spanning(units: list[Unit]) -> list[Unit]:
    """Units that stand beside a column of others, like the hero in a
    landscape layout: its height covers two or more units that sit next to
    it and are stacked one above the other. Left in the row grouping, such a
    unit would glue the whole column into one row."""
    out = []
    for u in units:
        beside = [v for v in units if v is not u
                  and (v.rect[2] <= u.rect[0] or v.rect[0] >= u.rect[2])  # horizontally apart
                  and v.rect[1] >= u.rect[1] - 0.1 * (u.rect[3] - u.rect[1])
                  and v.rect[3] <= u.rect[3] + 0.1 * (u.rect[3] - u.rect[1])]
        stacked = any(a.rect[3] <= b.rect[1] or b.rect[3] <= a.rect[1]
                      for i, a in enumerate(beside) for b in beside[i + 1:])
        if len(beside) >= 2 and stacked:
            out.append(u)
    return out


def source_rows(ctx: Ctx, units: list[Unit]) -> list[Row]:
    """Group units into reading rows, top to bottom, as the source laid them out.

    A unit standing beside a column (see _spanning) gets a row of its own:
    above the rest if it was on the left, below if it was on the right, which
    is the order a reader meets it in."""
    spanning = _spanning(units)
    rest = [u for u in units if u not in spanning]
    rows = _band_rows(ctx, rest) if rest else []
    for u in spanning:
        r = Row([[u]], ["center"], u.rect)
        others = union([v.rect for v in rest]) if rest else u.rect
        if (u.rect[0] + u.rect[2]) / 2 < (others[0] + others[2]) / 2:
            rows.insert(0, r)
        else:
            rows.append(r)
    return rows


def _band_rows(ctx: Ctx, units: list[Unit]) -> list[Row]:
    d = ctx.src
    order = sorted(units, key=lambda u: (u.rect[1] + u.rect[3]) / 2)
    bands: list[list[Unit]] = []
    for u in order:
        for band in bands:
            o = union([b.rect for b in band])
            ov = min(u.rect[3], o[3]) - max(u.rect[1], o[1])
            if ov > 0.5 * min(u.rect[3] - u.rect[1], o[3] - o[1]):
                band.append(u)
                break
        else:
            bands.append([u])

    rows = []
    for band in bands:
        band.sort(key=lambda u: u.rect[0])
        clusters: list[list[Unit]] = [[band[0]]]
        for u in band[1:]:
            prev = clusters[-1][-1]
            same_set = (ctx.roles[u.uid].set_id is not None
                        and ctx.roles[u.uid].set_id == ctx.roles[prev.uid].set_id)
            if same_set or u.rect[0] - prev.rect[2] < 0.08 * d.width:
                clusters[-1].append(u)
            else:
                clusters.append([u])
        anchors = [_anchor(d, union([u.rect for u in c])) for c in clusters]
        rows.append(Row(clusters, anchors, union([u.rect for u in band])))
    return rows


def _anchor(d: Design, r: Rect) -> str:
    left, right = r[0], d.width - r[2]
    if abs(left - right) <= 0.08 * d.width:
        return "center"
    return "left" if left < right else "right"


def size_cluster(ctx: Ctx, units: list[Unit], s: float, max_w: float, widen: float,
                 align: Optional[str], line_align: str) -> Block:
    """Lay a cluster out left to right, wrapping into more lines when it
    runs out of width (a badge row in a narrow column becomes a grid)."""
    gaps = [max(ctx.min_gap, (b.rect[0] - a.rect[2]) * s) for a, b in zip(units, units[1:])]
    gap = min(gaps) if gaps else ctx.min_gap
    blocks = [size_unit(ctx, u, s, max_w, widen, align) for u in units]

    lines: list[list[Block]] = [[]]
    width = 0.0
    for b in blocks:
        need = b.w if not lines[-1] else width + gap + b.w
        if lines[-1] and need > max_w:
            lines.append([b])
            width = b.w
        else:
            lines[-1].append(b)
            width = need
    line_w = [sum(b.w for b in ln) + gap * (len(ln) - 1) for ln in lines]
    line_h = [max(b.h for b in ln) for ln in lines]
    w = max(line_w)
    h = sum(line_h) + gap * (len(lines) - 1)

    def place(x: float, y: float) -> Patch:
        patch: Patch = {}
        cy = y
        for ln, lw, lh in zip(lines, line_w, line_h):
            cx = x + (w - lw) / 2 if line_align == "center" else (x + w - lw if line_align == "right" else x)
            for b in ln:
                patch.update(b.place(cx, cy + (lh - b.h) / 2))
                cx += b.w + gap
            cy += lh + gap
        return patch

    return Block(w, h, place)


def size_row(ctx: Ctx, row: Row, s: float, region_w: float, widen: float = 1.0,
             align: Optional[str] = None, anchor: Optional[str] = None) -> Optional[Block]:
    """A row spans the region's width. Clusters go to their anchor; if two
    anchored clusters would collide we pack them side by side instead, and if
    even that is too wide we stack them, which is what a designer does when a
    wide layout goes narrow."""
    anchors = [anchor or a for a in row.anchors]
    blocks = [size_cluster(ctx, c, s, region_w, widen, align, a) for c, a in zip(row.clusters, anchors)]
    gap = max(ctx.min_gap, 0.04 * region_w)

    xs = []
    for b, a in zip(blocks, anchors):
        xs.append(0.0 if a == "left" else (region_w - b.w if a == "right" else (region_w - b.w) / 2))
    spans = sorted(zip(xs, blocks), key=lambda t: t[0])
    clash = any(x2 < x1 + b1.w + gap for (x1, b1), (x2, _b2) in zip(spans, spans[1:]))
    if clash or any(x < -0.5 or x + b.w > region_w + 0.5 for x, b in zip(xs, blocks)):
        total = sum(b.w for b in blocks) + gap * (len(blocks) - 1)
        if total > region_w + 0.5:
            return _stacked_row(blocks, anchors, region_w, max(ctx.min_gap, 0.6 * gap))
        start = 0.0 if (anchor or "center") == "left" else (region_w - total) / 2
        xs, cur = [], start
        for b in blocks:
            xs.append(cur)
            cur += b.w + gap

    h = max(b.h for b in blocks)

    def place(x: float, y: float) -> Patch:
        patch: Patch = {}
        for bx, b in zip(xs, blocks):
            patch.update(b.place(x + bx, y + (h - b.h) / 2))
        return patch

    return Block(region_w, h, place)


def _stacked_row(blocks: list[Block], anchors: list[str], region_w: float, gap: float) -> Block:
    h = sum(b.h for b in blocks) + gap * (len(blocks) - 1)

    def place(x: float, y: float) -> Patch:
        patch: Patch = {}
        for b, a in zip(blocks, anchors):
            bx = 0.0 if a == "left" else (region_w - b.w if a == "right" else (region_w - b.w) / 2)
            patch.update(b.place(x + bx, y))
            y += b.h + gap
        return patch

    return Block(region_w, h, place)


@dataclass
class Stack:
    blocks: list[Block]
    gaps: list[float]  # between consecutive blocks
    src_spaces: list[float]  # source space above, between and below the rows

    @property
    def height(self) -> float:
        return sum(b.h for b in self.blocks) + sum(self.gaps)


def size_stack(ctx: Ctx, rows: list[Row], s: float, region: Rect, widen: float = 1.0,
               align: Optional[str] = None, anchor: Optional[str] = None,
               gap_scale: float = 1.0) -> Optional[Stack]:
    rw = region[2] - region[0]
    blocks = []
    for row in rows:
        b = size_row(ctx, row, s, rw, widen, align, anchor)
        if b is None:
            return None
        blocks.append(b)
    raw = [b.src_rect[1] - a.src_rect[3] for a, b in zip(rows, rows[1:])]
    gaps = [max(ctx.min_gap, min(g, ctx.src_gap_cap) * s * gap_scale) for g in raw]
    spaces = [rows[0].src_rect[1], *raw, ctx.src.height - rows[-1].src_rect[3]]
    return Stack(blocks, gaps, [max(0.0, g) for g in spaces])


def fit_stack(ctx: Ctx, rows: list[Row], region: Rect, widen: float = 1.0,
              align: Optional[str] = None, anchor: Optional[str] = None,
              gap_scale: float = 1.0, s_max: Optional[float] = None) -> tuple[float, Stack]:
    """Largest scale whose stack fits the region, by bisection. If nothing
    fits even tiny, relax the type floors a step at a time and try again."""
    rh = region[3] - region[1]
    hi0 = s_max if s_max is not None else ctx.s_cap
    lo0 = 0.02

    def ok(s: float) -> Optional[Stack]:
        st = size_stack(ctx, rows, s, region, widen, align, anchor, gap_scale)
        return st if st is not None and st.height <= rh + 0.5 else None

    for relax in FLOOR_RELAX_STEPS:
        ctx.floor_relax = dict(relax)
        best = ok(hi0)
        if best is not None:
            return hi0, best
        if ok(lo0) is None:
            continue
        lo, hi, best_s = lo0, hi0, lo0
        best = ok(lo0)
        for _ in range(22):
            mid = (lo + hi) / 2
            st = ok(mid)
            if st is not None:
                best, best_s, lo = st, mid, mid
            else:
                hi = mid
        return best_s, best
    # Nothing fits at any floor. Hand back the smallest so it can still be
    # placed; the scorer will mark it down and another template may win.
    st = size_stack(ctx, rows, lo0, region, widen, align, anchor, gap_scale)
    if st is None:
        st = size_stack(ctx, rows, lo0, (region[0], region[1], region[0] + 1e9, region[3]),
                        widen, align, anchor, gap_scale)
    return lo0, st


def place_stack(st: Stack, region: Rect, mode: str = "even", spread: float = 2.0) -> Patch:
    """Put the stack down in the region and decide where leftover height goes.

    - "center": gaps stay as sized, the stack sits in the middle.
    - "even": gaps grow up to `spread` times, the rest splits above and below.
    - "proportional": leftover follows the source's own spacing, top margin
      and bottom margin included, so an airy poster stays airy in the same
      places instead of everything drifting to the middle.
    """
    rh = region[3] - region[1]
    extra = max(0.0, rh - st.height)
    gaps = list(st.gaps)
    if mode == "proportional" and sum(st.src_spaces) > 0:
        total = sum(st.src_spaces)
        shares = [extra * sp / total for sp in st.src_spaces]
        top = shares[0]
        gaps = [g + sh for g, sh in zip(gaps, shares[1:-1])]
    else:
        if mode == "even" and sum(gaps) > 0:
            grow = min(spread - 1.0, extra / sum(gaps))
            gaps = [g * (1 + max(0.0, grow)) for g in gaps]
        used = sum(b.h for b in st.blocks) + sum(gaps)
        top = max(0.0, (rh - used) / 2)
    y = region[1] + top
    patch: Patch = {}
    for i, b in enumerate(st.blocks):
        patch.update(b.place(region[0], y))
        y += b.h + (gaps[i] if i < len(gaps) else 0)
    return patch


# ------------------------------------------------------------ backdrop, corners

def place_backdrop(ctx: Ctx) -> Patch:
    """Background fills the canvas; decoration keeps its relative position
    and a uniform scale, so circles stay circles, clamped inside."""
    d = ctx.src
    patch: Patch = {}
    k = math.sqrt((ctx.W * ctx.H) / (d.width * d.height))
    for u in ctx.units_with(BACKDROP_ROLES):
        role = ctx.role(u)
        for m in u.members:
            el = d.els[m]
            if role == "background":
                patch[m] = {"x": 0.0, "y": 0.0, "width": float(ctx.W), "height": float(ctx.H)}
                continue
            if role == "panel":  # flat colour blocks can stretch
                sx, sy = ctx.W / d.width, ctx.H / d.height
                w, h = el["width"] * sx, el["height"] * sy
            else:
                kk = min(k, 0.9 * min(ctx.W, ctx.H) / max(el["width"], el["height"], 1e-6))
                w, h = el["width"] * kk, el["height"] * kk
            cx = (el["x"] + el["width"] / 2) / d.width * ctx.W
            cy = (el["y"] + el["height"] / 2) / d.height * ctx.H
            x = min(max(0.0, cx - w / 2), ctx.W - w)
            y = min(max(0.0, cy - h / 2), ctx.H - h)
            patch[m] = {"x": x, "y": y, "width": w, "height": h}
    return patch


def nearest_corner(d: Design, r: Rect) -> str:
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    return ("t" if cy < d.height / 2 else "b") + ("l" if cx < d.width / 2 else "r")


def place_corner(ctx: Ctx, u: Unit, k0: float, occupied: list[Rect], max_frac: float = 0.3) -> Patch:
    """Tuck a unit into a free corner: the one it came from if possible,
    shrinking before moving, and the smallest try at home if all else fails."""
    w0, h0 = u.rect[2] - u.rect[0], u.rect[3] - u.rect[1]
    k0 = min(k0, max_frac * ctx.W / w0, max_frac * 2 * ctx.H / h0)
    home = nearest_corner(ctx.src, u.rect)
    corners = [home] + [c for c in ("tr", "tl", "br", "bl") if c != home]
    m = ctx.margin / 2
    fallback = None
    for scale in (1.0, 0.8, 0.65, 0.5):
        k = k0 * scale
        w, h = w0 * k, h0 * k
        for c in corners:
            x = m if c[1] == "l" else ctx.W - m - w
            y = m if c[0] == "t" else ctx.H - m - h
            patch = rigid_patch(ctx, u, x, y, k)
            if fallback is None or c == home:
                fallback = patch
            r = (x, y, x + w, y + h)
            if not any(overlap_area(r, o) > 0 for o in occupied):
                return patch
    return fallback


def pads(ctx: Ctx, u: Unit, k: float) -> tuple[float, float, float, float]:
    """How far the raw boxes reach past the visible rect at scale k, as
    (left, top, right, bottom)."""
    R, F = u.rect, _footprint(ctx, u)
    return ((R[0] - F[0]) * k, (R[1] - F[1]) * k, (F[2] - R[2]) * k, (F[3] - R[3]) * k)


def _footprint(ctx: Ctx, u: Unit) -> Rect:
    """Union of a unit's raw and rotated boxes in source coordinates. This is
    what has to stay on canvas, and it is usually bigger than the visible
    rect because of transparent padding."""
    d = ctx.src
    return union([r for m in u.members for r in (box(d.els[m]), rotated_box(d.els[m]))])


def tuck(ctx: Ctx, u: Unit, k_max: float, occupied: list[Rect],
         k_min_frac: float = 0.1, pad: float = 2.0) -> tuple[Patch, Rect]:
    """Find room for a unit the layout had no slot for.

    Shrinks from k_max down to k_min_frac of it, and at each size scans the
    canvas for a spot whose visible rect clears everything already placed and
    whose raw boxes stay on canvas. Among free spots it takes the one nearest
    to where the unit sat in the source, so fine print still ends up low and a
    sticker still ends up in its corner. If nowhere is free it goes, at the
    smallest size, to that spot anyway; the scorer will count the collision.
    """
    d = ctx.src
    R = u.rect
    F = _footprint(ctx, u)
    w0, h0 = R[2] - R[0], R[3] - R[1]
    # where the unit "wants" to be: its source centre in target proportions
    want_x = (R[0] + R[2]) / 2 / d.width * ctx.W
    want_y = (R[1] + R[3]) / 2 / d.height * ctx.H
    edge = ctx.margin / 2

    def fits_canvas(x: float, y: float, k: float) -> bool:
        fx0, fy0 = x + (F[0] - R[0]) * k, y + (F[1] - R[1]) * k
        fx1, fy1 = x + (F[2] - R[0]) * k, y + (F[3] - R[1]) * k
        return fx0 >= 0 and fy0 >= 0 and fx1 <= ctx.W and fy1 <= ctx.H

    k = k_max
    last = None
    while k >= k_max * k_min_frac:
        w, h = w0 * k, h0 * k
        if w <= ctx.W - 2 * edge and h <= ctx.H - 2 * edge:
            xs = _scan(edge, ctx.W - edge - w, want_x - w / 2, 48)
            ys = _scan(edge, ctx.H - edge - h, want_y - h / 2, 24)
            best = None
            for y in ys:
                for x in xs:
                    if not fits_canvas(x, y, k):
                        continue
                    r = (x - pad, y - pad, x + w + pad, y + h + pad)
                    if any(overlap_area(r, o) > 0 for o in occupied):
                        continue
                    dist = (x + w / 2 - want_x) ** 2 + (y + h / 2 - want_y) ** 2
                    if best is None or dist < best[0]:
                        best = (dist, x, y)
            if best is not None:
                _, x, y = best
                return rigid_patch(ctx, u, x, y, k), (x, y, x + w, y + h)
            last = (k, w, h)
        k *= 0.85
    if last is None:
        k, w, h = k_max * k_min_frac, w0 * k_max * k_min_frac, h0 * k_max * k_min_frac
    else:
        k, w, h = last
    x = min(max(edge, want_x - w / 2), ctx.W - edge - w)
    y = min(max(edge, want_y - h / 2), ctx.H - edge - h)
    return rigid_patch(ctx, u, x, y, k), (x, y, x + w, y + h)


def _scan(lo: float, hi: float, want: float, n: int) -> list[float]:
    """n evenly spaced positions across [lo, hi] plus the wanted one, clamped."""
    if hi <= lo:
        return [lo]
    pts = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    pts.append(min(max(lo, want), hi))
    return pts


# ------------------------------------------------------------------ compose

def compose(ctx: Ctx, patch: Patch) -> dict[str, Any]:
    """Apply a patch to a copy of the source, keep every box on canvas, and
    round for output."""
    els = []
    for el in ctx.src.doc["elements"]:
        e = dict(el)
        e.update(patch.get(el["id"], {}))
        els.append(e)
    doc = {"canvas": {"width": ctx.W, "height": ctx.H}, "elements": els}
    _clamp_units(ctx, doc)
    for e in els:
        for f in ("x", "y", "width", "height"):
            e[f] = round(float(e[f]), 2)
        if e["type"] == "text":
            e["font_size"] = max(1, int(e["font_size"]))
    return doc


def _clamp_units(ctx: Ctx, doc: dict[str, Any]) -> None:
    """Shift each unit so every raw and rotated box is inside the canvas.
    Transparent padding around a logo can push its box past the edge even
    when the visible part sits neatly at the margin."""
    by_id = {e["id"]: e for e in doc["elements"]}
    for u in ctx.src.units:
        if ctx.role(u) == "background":
            continue
        rects = [r for m in u.members for r in (box(by_id[m]), rotated_box(by_id[m]))]
        b = union(rects)
        bw, bh = b[2] - b[0], b[3] - b[1]
        if bw > ctx.W or bh > ctx.H:  # too big to shift: shrink about its own corner
            k = min(ctx.W / bw, ctx.H / bh) * 0.999
            for m in u.members:
                e = by_id[m]
                e["x"] = b[0] + (e["x"] - b[0]) * k
                e["y"] = b[1] + (e["y"] - b[1]) * k
                e["width"] *= k
                e["height"] *= k
                if e["type"] == "text":
                    e["font_size"] = max(1, int(e["font_size"] * k))
            rects = [r for m in u.members for r in (box(by_id[m]), rotated_box(by_id[m]))]
            b = union(rects)
        dx = -b[0] if b[0] < 0 else (ctx.W - b[2] if b[2] > ctx.W else 0.0)
        dy = -b[1] if b[1] < 0 else (ctx.H - b[3] if b[3] > ctx.H else 0.0)
        if dx or dy:
            for m in u.members:
                by_id[m]["x"] += dx
                by_id[m]["y"] += dy


def visible_rects(ctx: Ctx, doc: dict[str, Any], units: list[Unit]) -> list[Rect]:
    """Visible rects of `units` in a composed output (for corner placement)."""
    d = Design(doc, ctx.src.assets)
    return [union([d.visible(m) for m in u.members]) for u in units]
