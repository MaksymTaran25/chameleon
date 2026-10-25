"""Our stand-in for the official scorer.

Two layers, same as the real one. Hard gates are the structural checks on the
JSON; failing any of them makes a candidate worthless however good it looks.
The soft score is a rough copy of the rubric (no unwanted collisions, readable,
on brand, intentional) built from geometry plus a cheap raster of everything
that is not text, so we can rank candidate layouts without a render each.

Roles are always inferred from the source and carried over by id, since the
output is the same elements in new places.
"""

import math
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageStat

from renderer.converter.assets import load_asset_image
from renderer.converter.colorutil import parse_rgba
from renderer.converter.shapes import render_shape

from .measure import measure_text
from .paths import assets_dir as default_assets_dir
from .parse import Design, Rect, area, box, effective_alpha, intersect, overlap_area, rotated_box, union
from .roles import BACKDROP_ROLES, RoleInfo, infer_roles, role_of_element

REQUIRED_COMMON = ("id", "type", "x", "y", "width", "height")
REQUIRED_BY_TYPE = {
    "text": ("content", "font_family", "font_size", "color"),
    "image": ("asset_id",),
    "shape": ("shape_type", "fill_color"),
}
# Stage one treats these as fixed (RULES.md, "Adaptive typography and copy").
FROZEN = {
    "text": ("type", "content", "font_family", "font_weight", "font_style", "color", "style_runs"),
    "image": ("type", "asset_id", "fit"),
    "shape": ("type", "shape_type", "fill_color", "stroke_color"),
}
ASPECT_TOLERANCE = 0.05
BOUNDS_TOL = 0.5

WEIGHTS = {
    "collisions": 0.25,
    "readability": 0.20,
    "contrast": 0.15,
    "hierarchy": 0.10,
    "order": 0.05,
    "fill": 0.10,
    "alignment": 0.05,
    "text_fit": 0.10,
}

# Desired type size per role as (absolute floor px, fraction of the short side).
# Below the floor a line counts as unreadable; the fraction is what a designer
# would reach for on a big canvas. Values come from the samples at 1080px.
TYPE_FLOORS = {
    "headline": (14, 0.045),
    "cta": (12, 0.018),
    "subhead": (12, 0.02),
    "badge": (10, 0.013),
    "sticker": (10, 0.016),
    "fineprint": (8, 0.009),
}


@dataclass
class Score:
    total: float
    hard: list[str] = field(default_factory=list)
    parts: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.hard


# -------------------------------------------------------------- hard gates

def hard_gates(source: dict[str, Any], output: dict[str, Any], target: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "canvas" not in output or "elements" not in output:
        return ["output is missing 'canvas' or 'elements'"]
    if (output["canvas"].get("width"), output["canvas"].get("height")) != (target["width"], target["height"]):
        problems.append("output canvas does not match target_canvas")

    src = {e["id"]: e for e in source["elements"]}
    out_ids = [e.get("id") for e in output["elements"]]
    if sorted(map(str, out_ids)) != sorted(src):
        problems.append(f"id mismatch: missing {sorted(set(src) - set(out_ids))}, "
                        f"extra {sorted(set(out_ids) - set(src))}, duplicates={len(out_ids) != len(set(out_ids))}")

    W, H = target["width"], target["height"]
    for el in output["elements"]:
        eid = el.get("id")
        missing = [f for f in REQUIRED_COMMON + REQUIRED_BY_TYPE.get(el.get("type"), ()) if f not in el]
        if missing:
            problems.append(f"{eid}: missing {missing}")
            continue
        if not all(isinstance(el[k], (int, float)) and math.isfinite(el[k]) for k in ("x", "y", "width", "height")):
            problems.append(f"{eid}: non-numeric geometry")
            continue
        if el["width"] < 0 or el["height"] < 0:
            problems.append(f"{eid}: negative size")
        s = src.get(eid)
        if s is not None:
            changed = [k for k in FROZEN.get(s["type"], ()) if s.get(k) != el.get(k)]
            if changed:
                problems.append(f"{eid}: changed frozen fields {changed}")
            if s["type"] == "image" and s.get("fit", "contain") != "cover" and s["height"] and el["height"]:
                a0, a1 = s["width"] / s["height"], el["width"] / el["height"]
                if abs(a1 / a0 - 1) > ASPECT_TOLERANCE:
                    problems.append(f"{eid}: aspect {a1:.3f} vs source {a0:.3f}")
        if el["type"] == "text" and el["font_size"] < 1:
            problems.append(f"{eid}: font_size < 1")
        # We don't know whether the scorer checks the raw or the rotated box,
        # so both have to be inside.
        for label, r in (("box", box(el)), ("rotated box", rotated_box(el))):
            if r[0] < -BOUNDS_TOL or r[1] < -BOUNDS_TOL or r[2] > W + BOUNDS_TOL or r[3] > H + BOUNDS_TOL:
                problems.append(f"{eid}: {label} off-canvas")
                break
    return problems


# --------------------------------------------------------------- raster

def backdrop_raster(d: Design, max_side: int = 400) -> tuple[Image.Image, float]:
    """Everything except text, composited small. Good enough to read the
    colour and busyness under each line of text."""
    s = min(1.0, max_side / max(d.width, d.height))
    canvas = Image.new("RGBA", (max(1, round(d.width * s)), max(1, round(d.height * s))), (255, 255, 255, 255))
    for el in sorted(d.els.values(), key=lambda e: e.get("z_index", 0)):
        if el["type"] == "text" or el["width"] * s < 1 or el["height"] * s < 1:
            continue
        scaled = {**el, "width": el["width"] * s, "height": el["height"] * s,
                  "corner_radius": el.get("corner_radius", 0) * s}
        if el["type"] == "image":
            tile = load_asset_image(str(d.assets or default_assets_dir()), el.get("asset_id"),
                                    scaled["width"], scaled["height"], el.get("fit", "contain"))
        else:
            tile = render_shape(scaled)
        alpha = effective_alpha(el) if el["type"] == "shape" else float(el.get("opacity", 1.0))
        if alpha < 1:
            tile.putalpha(tile.getchannel("A").point(lambda a: round(a * alpha)))
        rot = el.get("rotation", 0) or 0
        cx, cy = (el["x"] + el["width"] / 2) * s, (el["y"] + el["height"] / 2) * s
        if rot:
            tile = tile.rotate(-rot, expand=True, resample=Image.BICUBIC)
        # paste() takes negative offsets, alpha_composite() does not
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(tile, (round(cx - tile.width / 2), round(cy - tile.height / 2)), tile)
        canvas.alpha_composite(layer)
    return canvas.convert("RGB"), s


def _luminance(rgb: tuple[float, float, float]) -> float:
    def ch(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ------------------------------------------------------------- soft score

def soft_score(source: Design, out: Design, roles_src: Optional[dict[str, RoleInfo]] = None) -> tuple[dict[str, float], list[str]]:
    roles_src = roles_src or infer_roles(source)
    by_el = role_of_element(source, roles_src)
    notes: list[str] = []
    short = min(out.width, out.height)

    # Units are defined on the source and carried to the output by member ids.
    src_units = [r.unit for r in roles_src.values()]
    content = [u for u in src_units if roles_src[u.uid].role not in BACKDROP_ROLES]

    def rect_out(u) -> Rect:
        return union([out.visible(m) for m in u.members])

    def has_text(u) -> bool:
        return any(source.els[m]["type"] == "text" for m in u.members)

    # collisions: visible overlap between content units. Overlap the source
    # already had was layered on purpose, so only the growth beyond it counts.
    penalty = 0.0
    for a, b in combinations(content, 2):
        inter = overlap_area(rect_out(a), rect_out(b))
        if inter <= 0:
            continue
        frac = inter / max(1.0, min(area(rect_out(a)), area(rect_out(b))))
        frac -= overlap_area(a.rect, b.rect) / max(1.0, min(area(a.rect), area(b.rect)))
        if frac <= 0:
            continue
        weight = 2.0 if has_text(a) or has_text(b) else 1.0
        penalty += weight * frac
        if frac > 0.02:
            notes.append(f"collision {a.uid} x {b.uid} ({frac:.0%})")
    collisions = max(0.0, 1.0 - penalty)

    # readability and text fit, per text element weighted by importance
    raster, s = backdrop_raster(out)
    read_num = read_den = fit_num = fit_den = con_num = con_den = 0.0
    for eid, info in by_el.items():
        el = out.els[eid]
        if el["type"] != "text":
            continue
        role = info.role if info.role in TYPE_FLOORS else "subhead"
        floor, frac = TYPE_FLOORS[role]
        desired = max(floor, frac * short)
        size = int(el["font_size"])
        r = 0.0 if size < floor else min(1.0, size / desired)
        w = max(info.importance, 0.1)
        read_num += w * r
        read_den += w
        if size < floor:
            notes.append(f"{eid} unreadable at {size}px (floor {floor})")

        block = measure_text(el)
        over_w = max(0.0, block.width - el["width"]) / max(1.0, el["width"])
        over_h = max(0.0, block.height - el["height"]) / max(1.0, el["height"])
        fit_num += w * max(0.0, 1.0 - 2 * (over_w + over_h))
        fit_den += w
        if over_w > 0.02 or over_h > 0.1:
            notes.append(f"{eid} overflows its box (w+{over_w:.0%}, h+{over_h:.0%})")

        vr = out.visible(eid)
        crop = raster.crop(tuple(round(v * s) for v in intersect(vr, out.canvas_rect)))
        if crop.width >= 1 and crop.height >= 1:
            stat = ImageStat.Stat(crop)
            under = tuple(stat.mean[:3])
            busy = sum(stat.stddev[:3]) / 3 / 255
            colors = [el.get("color")] + [run["color"] for run in el.get("style_runs") or [] if "color" in run]
            ratios = []
            for c in colors:
                cr, cg, cb, ca = parse_rgba(c, default=(0, 0, 0, 1.0))
                ca *= float(el.get("opacity", 1.0))
                seen = tuple(ca * t + (1 - ca) * u for t, u in zip((cr, cg, cb), under))
                ratios.append(contrast_ratio(seen, under))
            ratio = min(ratios)
            # 4.5:1 is WCAG body text; busy backgrounds eat into it
            c_score = max(0.0, min(1.0, (ratio - 1.5) / 3.0)) * (1.0 - min(0.5, max(0.0, busy - 0.08) * 3))
            con_num += w * c_score
            con_den += w
            if ratio < 3.0:
                notes.append(f"{eid} low contrast {ratio:.1f}:1")

    readability = read_num / read_den if read_den else 1.0
    text_fit = fit_num / fit_den if fit_den else 1.0
    contrast = con_num / con_den if con_den else 1.0

    # hierarchy: pairwise order of type sizes and of image sizes survives
    def concordance(ids, key_src, key_out, min_gap) -> Optional[float]:
        agree = total = 0.0
        for a, b in combinations(ids, 2):
            ds = key_src(a) - key_src(b)
            if abs(ds) < min_gap:
                continue
            do = key_out(a) - key_out(b)
            total += 1
            agree += 1.0 if ds * do > 0 else (0.5 if do == 0 else 0.0)
        return agree / total if total else None

    texts = [e for e, i in by_el.items() if source.els[e]["type"] == "text"]
    imgs = [e for e, i in by_el.items() if source.els[e]["type"] == "image" and i.role not in BACKDROP_ROLES]
    parts = [p for p in (
        concordance(texts, lambda e: source.els[e]["font_size"], lambda e: out.els[e]["font_size"], 2),
        concordance(imgs, lambda e: area(source.visible(e)), lambda e: area(out.visible(e)), 1),
    ) if p is not None]
    hierarchy = sum(parts) / len(parts) if parts else 1.0

    # reading order of the text-bearing content (and the logo) in row-major order
    readable = [u for u in content if has_text(u) or roles_src[u.uid].role == "logo"]
    src_rank = _row_major_rank([u.rect for u in readable])
    out_rank = _row_major_rank([rect_out(u) for u in readable])
    order = concordance(list(range(len(readable))), lambda i: src_rank[i], lambda i: out_rank[i], 1)
    order = 1.0 if order is None else order

    # fill and balance: content should use the canvas, centred-ish
    if content:
        cr = intersect(union([rect_out(u) for u in content]), out.canvas_rect)
        used = area(cr) / (out.width * out.height)
        fill_s = 1.0 if 0.45 <= used <= 0.92 else (used / 0.45 if used < 0.45 else max(0.0, 1 - (used - 0.92) * 5))
        lm, rm, tm, bm = cr[0], out.width - cr[2], cr[1], out.height - cr[3]
        bal = 0.5 * (_ratio(lm, rm) + _ratio(tm, bm))
        fill = 0.7 * fill_s + 0.3 * bal
    else:
        fill = 1.0

    # alignment: share of content units that line up with another one
    rects = [rect_out(u) for u in content]
    tol = 0.006 * max(out.width, out.height) + 1
    aligned = 0
    for i, r in enumerate(rects):
        keys = _align_keys(r)
        if any(any(abs(a - b) <= tol for a, b in zip(keys[k], _align_keys(o)[k]) ) for k in (0, 1)
               for j, o in enumerate(rects) if j != i):
            aligned += 1
    alignment = aligned / len(rects) if rects else 1.0

    return {
        "collisions": collisions, "readability": readability, "contrast": contrast,
        "hierarchy": hierarchy, "order": order, "fill": fill, "alignment": alignment,
        "text_fit": text_fit,
    }, notes


def _ratio(a: float, b: float) -> float:
    a, b = max(0.0, a), max(0.0, b)
    return 1.0 if a == b else min(a, b) / max(a, b)


def _align_keys(r: Rect) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(left, centre, right) and (top, middle, bottom)."""
    return (r[0], (r[0] + r[2]) / 2, r[2]), (r[1], (r[1] + r[3]) / 2, r[3])


def _row_major_rank(rects: list[Rect]) -> list[int]:
    """Rank rects the way a reader scans: rows top to bottom, left to right
    within a row. Two rects share a row when their vertical spans overlap
    by more than half of the shorter one."""
    order = sorted(range(len(rects)), key=lambda i: (rects[i][1] + rects[i][3]) / 2)
    rows: list[list[int]] = []
    for i in order:
        r = rects[i]
        for row in rows:
            o = rects[row[0]]
            ov = min(r[3], o[3]) - max(r[1], o[1])
            if ov > 0.5 * min(r[3] - r[1], o[3] - o[1]):
                row.append(i)
                break
        else:
            rows.append([i])
    rank = [0] * len(rects)
    n = 0
    for row in rows:
        for i in sorted(row, key=lambda i: rects[i][0]):
            rank[i] = n
            n += 1
    return rank


def score(source: dict[str, Any], output: dict[str, Any], target: dict[str, Any],
          assets: Optional[Path] = None) -> Score:
    hard = hard_gates(source, output, target)
    if any("missing" in h or "id mismatch" in h or "non-numeric" in h for h in hard):
        return Score(0.0, hard)  # too broken to measure
    parts, notes = soft_score(Design(source, assets), Design(output, assets))
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    return Score(0.0 if hard else total, hard, parts, notes)
