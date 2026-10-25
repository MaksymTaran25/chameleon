"""Layout families. Each takes a Ctx and returns a finished output design,
or None when it does not apply (Split needs a hero image to split around).

- stack: the source's reading rows, top to bottom, in one column. For tall,
  square and roughly-square targets.
- split: the hero image in one column, everything else stacked in the
  other. For anything clearly wider than it is tall.
- strip: one row, left to right: logo, hero, a text column, optionally the
  badge set, the button. Whatever has no slot is tucked into free space at
  the largest size that clears everything. For ultra-wide banners, and as a
  "band" (text column holding subhead and badges too) for very wide ones.
- letterbox: the whole design scaled down as one piece and centred. Never
  the best answer on a far ratio, but it is always valid and it is hard to
  beat on a near one, so it is always in the running.
"""

from typing import Any, Optional

from .layout import (CORNER_ROLES, FLOOR_PX, Block, Ctx, Patch, compose, fit_stack, place_backdrop,
                     free_text_id, pads, place_corner, place_stack, rigid_patch, size_cluster, size_rigid, source_rows,
                     tuck, visible_rects)
from .parse import Rect, union
from .roles import BACKDROP_ROLES

Doc = dict[str, Any]


def _widen(ctx: Ctx, region: Rect) -> float:
    """How much wider than a plain scale a text box may get: as much as the
    region is wider in proportion than the source, within reason."""
    rw, rh = region[2] - region[0], region[3] - region[1]
    ratio = (rw / rh) / (ctx.src.width / ctx.src.height)
    return min(1.6, max(1.0, ratio))


def _with_corners(ctx: Ctx, patch: Patch, k: float) -> Doc:
    """Compose, then fit the corner units around what is already placed."""
    doc = compose(ctx, patch)
    corner_units = ctx.units_with(CORNER_ROLES)
    if not corner_units:
        return doc
    occupied = visible_rects(ctx, doc, ctx.flow_units())
    for u in sorted(corner_units, key=lambda u: -ctx.roles[u.uid].importance):
        p = place_corner(ctx, u, k, occupied)
        patch.update(p)
        occupied += visible_rects(ctx, compose(ctx, patch), [u])
    return compose(ctx, patch)


def stack(ctx: Ctx, mode: str = "proportional", gap_scale: float = 1.0) -> Optional[Doc]:
    ctx.floor_relax = {}
    rows = source_rows(ctx, ctx.flow_units())
    if not rows:
        return None
    region = ctx.inner
    s, st = fit_stack(ctx, rows, region, widen=_widen(ctx, region), gap_scale=gap_scale)
    patch = place_backdrop(ctx)
    patch.update(place_stack(st, region, mode))
    return _with_corners(ctx, patch, s)


def split(ctx: Ctx, hero_side: str = "right", hero_frac: float = 0.45,
          align: str = "left", spread: float = 2.0) -> Optional[Doc]:
    heroes = ctx.units_with({"hero"})
    if not heroes:
        return None
    hero = max(heroes, key=lambda u: ctx.roles[u.uid].importance)
    ctx.floor_relax = {}
    x0, y0, x1, y1 = ctx.inner
    iw = x1 - x0
    gutter = 0.04 * ctx.W
    hero_w = hero_frac * iw
    if hero_side == "right":
        hero_col = (x1 - hero_w, y0, x1, y1)
        text_col = (x0, y0, x1 - hero_w - gutter, y1)
    else:
        hero_col = (x0, y0, x0 + hero_w, y1)
        text_col = (x0 + hero_w + gutter, y0, x1, y1)

    rows = source_rows(ctx, ctx.flow_units(exclude={"hero"}))
    patch = place_backdrop(ctx)
    s = ctx.s_cap
    if rows:
        text_align = "left" if align == "left" else None
        s, st = fit_stack(ctx, rows, text_col, widen=_widen(ctx, text_col),
                          align=text_align, anchor=align)
        patch.update(place_stack(st, text_col, "even", spread))

    cw, ch = hero_col[2] - hero_col[0], hero_col[3] - hero_col[1]
    hb = size_rigid(ctx, hero, 1e9, cw, ch)  # as big as the column allows
    patch.update(hb.place(hero_col[0] + (cw - hb.w) / 2, hero_col[1] + (ch - hb.h) / 2))
    return _with_corners(ctx, patch, s)


def strip(ctx: Ctx, text_roles: tuple[str, ...] = ("headline",), badges: str = "tuck",
          hero_pos: str = "left") -> Optional[Doc]:
    ctx.floor_relax = {}
    x0, y0, x1, y1 = ctx.inner
    rw, rh = x1 - x0, y1 - y0
    gap = max(ctx.min_gap, 0.025 * ctx.W)
    W = ctx.W

    def first(role: str):
        us = ctx.units_with({role})
        return max(us, key=lambda u: (u.rect[2] - u.rect[0]) * (u.rect[3] - u.rect[1])) if us else None

    logo, hero, cta = first("logo"), first("hero"), first("cta")
    text_units = [u for u in ctx.flow_units() if ctx.role(u) in text_roles]
    if cta is not None and free_text_id(ctx, cta):
        # A call to action set as plain text is a line of copy, not a button;
        # sized as a fixed block it would outgrow the headline.
        text_units.append(cta)
        cta = None
    if not any(ctx.role(u) == "headline" for u in text_units):
        return None
    badge_units = sorted(ctx.units_with({"badge"}), key=lambda u: u.rect[0])

    # Fixed-width segments, sized by the strip's height.
    def h0(u) -> float:
        return u.rect[3] - u.rect[1]

    segs: list[tuple[str, Any, Optional[Block]]] = []
    if logo is not None:
        # a designer keeps the logo a notch under the strip height so the headline leads
        segs.append(("logo", logo, size_rigid(ctx, logo, 0.6 * rh / h0(logo), 0.2 * W, rh)))
    hero_seg = ("hero", hero, size_rigid(ctx, hero, 1e9, 0.2 * W, rh)) if hero is not None else None
    if hero_seg and hero_pos == "left":
        segs.append(hero_seg)
    segs.append(("text", None, None))
    placed = {u.uid for u in text_units}
    if badges == "row" and badge_units and "badge" not in text_roles:
        pill_h = max(h0(u) for u in badge_units)
        bb = size_cluster(ctx, badge_units, 0.45 * rh / pill_h, 0.3 * W, 1.0, None, "center")
        if bb.h <= rh + 0.5:
            segs.append(("badges", badge_units, bb))
            placed |= {u.uid for u in badge_units}
    if cta is not None:
        segs.append(("cta", cta, size_rigid(ctx, cta, 0.8 * rh / h0(cta), 0.3 * W, rh)))
    if hero_seg and hero_pos == "right":
        segs.append(hero_seg)
    placed |= {u.uid for _k, u, _b in segs if u is not None and not isinstance(u, list)}

    # Transparent padding on the outermost segments may hang into the margin
    # but not off the canvas, so the row starts and ends where that allows.
    def seg_pads(seg):
        kind, u, b = seg
        if b is None or kind == "badges":
            return (0.0, 0.0, 0.0, 0.0)
        return pads(ctx, u, b.k)

    x0 = max(x0, seg_pads(segs[0])[0])
    x1 = min(x1, W - seg_pads(segs[-1])[2])
    rw = x1 - x0
    fixed = sum(b.w for _k, _u, b in segs if b is not None)
    text_w = rw - fixed - gap * (len(segs) - 1)
    if text_w < 0.18 * W:
        return None

    # The text column is a small stack of its own.
    rows = source_rows(ctx, text_units)
    region = (0.0, y0, text_w, y1)
    _s, st = fit_stack(ctx, rows, region, widen=_widen(ctx, region), align="left",
                       anchor="left", gap_scale=0.4)
    text_patch = place_stack(st, region, "center")
    probe = compose(ctx, text_patch)
    tr = union(visible_rects(ctx, probe, text_units))
    used_w = min(text_w, tr[2] - tr[0])

    # Spread what is left between segments, up to three gaps' worth each,
    # and centre the remainder.
    widths = [b.w if b is not None else used_w for _k, _u, b in segs]
    leftover = rw - sum(widths) - gap * (len(segs) - 1)
    g = gap + min(3 * gap, leftover / max(1, len(segs) - 1))
    x = x0 + max(0.0, (rw - sum(widths) - g * (len(segs) - 1)) / 2)

    patch = place_backdrop(ctx)
    for (kind, _u, b), w in zip(segs, widths):
        if kind == "text":
            dx = x - tr[0]
            for eid, p in text_patch.items():
                patch[eid] = {**p, "x": p["x"] + dx}
        else:
            pl, pt, pr, pb = seg_pads((kind, _u, b))
            y = y0 + (rh - b.h) / 2
            y = min(max(y, pt), ctx.H - pb - b.h)
            patch.update(b.place(x, y))
        x += w + g

    # Everything else goes wherever it fits, most important first.
    doc = compose(ctx, patch)
    placed_units = [u for u in ctx.src.units if u.uid in placed]
    occupied = visible_rects(ctx, doc, placed_units)
    rest = [u for u in ctx.src.units
            if u.uid not in placed and ctx.role(u) not in BACKDROP_ROLES]
    for u in sorted(rest, key=lambda u: -ctx.roles[u.uid].importance):
        p, r = tuck(ctx, u, _tuck_scale(ctx, u, rh), occupied)
        patch.update(p)
        occupied.append(r)
    return compose(ctx, patch)


def _tuck_scale(ctx: Ctx, u, rh: float) -> float:
    """Largest sensible size for a tucked unit: big enough that its text sits
    on its readability floor, never taller than the strip."""
    w0, h0 = u.rect[2] - u.rect[0], u.rect[3] - u.rect[1]
    k = 0.0
    for m in u.members:
        el = ctx.src.els[m]
        if el["type"] == "text":
            k = max(k, FLOOR_PX.get(ctx.role(u), 10) / el["font_size"])
    if k == 0.0:
        k = 0.6 * rh / h0
    return min(k, rh / h0, 0.35 * ctx.W / w0)


def letterbox(ctx: Ctx) -> Optional[Doc]:
    d = ctx.src
    k = min(ctx.W / d.width, ctx.H / d.height)
    ox, oy = (ctx.W - d.width * k) / 2, (ctx.H - d.height * k) / 2
    patch: Patch = {}
    for u in d.units:
        if ctx.role(u) == "background":
            continue
        patch.update(rigid_patch(ctx, u, ox + u.rect[0] * k, oy + u.rect[1] * k, k))
    patch.update({m: p for m, p in place_backdrop(ctx).items()
                  if ctx.role(d.unit_of[m]) == "background"})
    return compose(ctx, patch)


def candidates(ctx: Ctx) -> list[tuple[str, Any]]:
    """(name, thunk) pairs worth trying for this target class."""
    c = ctx.cls
    out: list[tuple[str, Any]] = []
    if c in ("ultra_tall", "tall", "square"):
        out += [("stack/proportional", lambda: stack(ctx, "proportional")),
                ("stack/compact", lambda: stack(ctx, "center", gap_scale=0.5))]
    else:
        fracs = (0.4, 0.5) if c == "wide" else (0.3, 0.4)
        for side in ("right", "left"):
            for frac in fracs:
                for align in ("left", "center"):
                    out.append((f"split/{side}/{frac}/{align}",
                                lambda side=side, frac=frac, align=align: split(ctx, side, frac, align)))
        out += [("stack/proportional", lambda: stack(ctx, "proportional")),
                ("stack/compact", lambda: stack(ctx, "center", gap_scale=0.4))]
    if c in ("very_wide", "ultra_wide"):
        groupings = [("headline",), ("headline", "subhead")]
        if c == "very_wide":
            groupings.append(("headline", "subhead", "badge"))  # the "band"
        for roles in groupings:
            for badges in (("tuck",) if "badge" in roles else ("tuck", "row")):
                for hero_pos in ("left", "right"):
                    name = f"strip/{'+'.join(roles)}/badges-{badges}/hero-{hero_pos}"
                    out.append((name, lambda roles=roles, badges=badges, hero_pos=hero_pos:
                                strip(ctx, roles, badges, hero_pos)))
    out.append(("letterbox", lambda: letterbox(ctx)))
    return out
