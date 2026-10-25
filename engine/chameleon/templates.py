"""Layout families. Each takes a Ctx and returns a finished output design,
or None when it does not apply (Split needs a hero image to split around).

- stack: the source's reading rows, top to bottom, in one column. For tall,
  square and roughly-square targets.
- split: the hero image in one column, everything else stacked in the
  other. For anything clearly wider than it is tall.
- letterbox: the whole design scaled down as one piece and centred. Never
  the best answer on a far ratio, but it is always valid and it is hard to
  beat on a near one, so it is always in the running.
"""

from typing import Any, Optional

from .layout import (CORNER_ROLES, Ctx, Patch, compose, fit_stack, place_backdrop, place_corner,
                     place_stack, rigid_patch, size_rigid, source_rows, visible_rects)
from .parse import Rect

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
    out.append(("letterbox", lambda: letterbox(ctx)))
    return out
