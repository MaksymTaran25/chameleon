"""Work out what each unit is, with no semantic labels to go on.

The hidden designs only give us ids, types and geometry, so roles come from
how a designer would have built the thing: the background covers the canvas,
the headline is the biggest type, a button is a short bold label on a solid
pill, decoration is faint. Ids and asset names are never read here. The one
brand prior we allow ourselves is the logo's asset file, because the brief
lists the brand kit as fixed material we may rely on.
"""

from dataclasses import dataclass
from statistics import median
from typing import Optional

from renderer.converter.colorutil import parse_rgba

from .parse import Design, Unit, area, box, effective_alpha

# How much each role matters when space runs out. Higher keeps its size;
# lower shrinks first and is the first to be tucked into leftover space.
IMPORTANCE = {
    "headline": 1.0,
    "logo": 0.95,
    "cta": 0.9,
    "hero": 0.85,
    "subhead": 0.6,
    "badge": 0.45,
    "sticker": 0.4,
    "fineprint": 0.25,
    "misc_image": 0.15,
    "panel": 0.1,
    "decoration": 0.05,
    "background": 0.0,
}

# Roles a layout arranges. The rest is backdrop that fills in behind.
CONTENT_ROLES = {"headline", "logo", "cta", "hero", "subhead", "badge", "sticker", "fineprint", "misc_image"}
BACKDROP_ROLES = {"background", "decoration", "panel"}

BRAND_LOGO_ASSETS = {"solstice_logo_lockup.png"}


@dataclass
class RoleInfo:
    role: str
    importance: float
    unit: Unit
    set_id: Optional[str] = None  # shared by units that form a badge set


def infer_roles(d: Design) -> dict[str, RoleInfo]:
    """Role per unit id."""
    W, H = d.width, d.height
    canvas_area = W * H
    roles: dict[str, RoleInfo] = {}

    def assign(u: Unit, role: str, set_id: Optional[str] = None) -> None:
        roles[u.uid] = RoleInfo(role, IMPORTANCE[role], u, set_id)

    singles = [u for u in d.units if len(u.members) == 1]
    composites = [u for u in d.units if len(u.members) > 1]

    # --- backdrop: background images, faint or textless shapes
    for u in singles:
        el = d.els[u.members[0]]
        if el["type"] == "image" and area(box(el)) >= 0.9 * canvas_area and (
                el.get("fit") == "cover" or u.z == min(e.get("z_index", 0) for e in d.els.values())):
            assign(u, "background")
        elif el["type"] == "shape":
            big = area(box(el)) >= 0.25 * canvas_area
            assign(u, "panel" if big and effective_alpha(el) >= 0.5 else "decoration")

    # --- composites: a rotated one is a sticker; otherwise the biggest pill is
    # the call to action and the rest are badges
    pills = []
    for u in composites:
        if any(d.els[m].get("rotation", 0) for m in u.members):
            assign(u, "sticker")
        elif _has_text(d, u):
            pills.append(u)
        else:
            assign(u, "decoration")
    if pills:
        cta = max(pills, key=lambda u: _pill_score(d, u))
        badges = [u for u in pills if u is not cta]
        # A lone small pill sitting high up is a badge; if the "cta" is no
        # bigger than its peers, the whole lot is one badge set instead.
        if badges and area(cta.rect) < 1.3 * median(area(b.rect) for b in badges):
            badges.append(cta)
            cta = None
        if cta is not None:
            assign(cta, "cta")
        for b in badges:
            assign(b, "badge", set_id=_badge_set_key(d, b, badges))

    # --- images
    images = [u for u in singles if u.uid not in roles and d.els[u.members[0]]["type"] == "image"]
    present = [u for u in images if not d.is_missing_asset(u.members[0])]
    for u in images:
        if u not in present:
            assign(u, "misc_image")
    logo = None
    if present:
        logo = max(present, key=lambda u: _logo_score(d, u))
        if _logo_score(d, logo) < 1.0:
            logo = None
    if logo is not None:
        assign(logo, "logo")
    rest = sorted((u for u in present if u is not logo), key=lambda u: area(u.rect), reverse=True)
    for i, u in enumerate(rest):
        assign(u, "hero" if i == 0 and area(u.rect) >= 0.03 * canvas_area else "misc_image")

    # --- standalone text: biggest type is the headline, the smallest faint
    # long-ish line hugging an edge is fine print, everything else subhead
    texts = [u for u in singles if u.uid not in roles and d.els[u.members[0]]["type"] == "text"]
    if texts:
        texts.sort(key=lambda u: _prominence(d.els[u.members[0]]), reverse=True)
        assign(texts[0], "headline")
        sizes = [d.els[u.members[0]]["font_size"] for u in texts]
        # No button shape anywhere? A short bold line in a brand colour is
        # the call to action set as plain text (L1 does this).
        if not any(r.role == "cta" for r in roles.values()):
            link = [u for u in texts[1:] if _looks_like_text_cta(d.els[u.members[0]])]
            if link:
                assign(link[0], "cta")
        for u in texts[1:]:
            if u.uid in roles:
                continue
            el = d.els[u.members[0]]
            small = el["font_size"] <= 0.6 * max(sizes) and el["font_size"] <= median(sizes)
            faint = effective_alpha(el) <= 0.75
            near_edge = u.rect[3] >= 0.9 * H or u.rect[1] <= 0.1 * H
            assign(u, "fineprint" if small and (faint or near_edge) else "subhead")

    for u in d.units:  # anything we could not place reads as decoration
        roles.setdefault(u.uid, RoleInfo("decoration", IMPORTANCE["decoration"], u))
    return roles


def role_of_element(d: Design, roles: dict[str, RoleInfo]) -> dict[str, RoleInfo]:
    return {m: roles[u.uid] for u in d.units for m in u.members}


def _has_text(d: Design, u: Unit) -> bool:
    return any(d.els[m]["type"] == "text" for m in u.members)


def _unit_text(d: Design, u: Unit) -> dict:
    return next(d.els[m] for m in u.members if d.els[m]["type"] == "text")


def _prominence(el: dict) -> float:
    return el["font_size"] * (1.15 if el.get("font_weight") == "bold" else 1.0)


def _looks_like_text_cta(el: dict) -> bool:
    r, g, b, _a = parse_rgba(el.get("color"), default=(0, 0, 0, 1.0))
    saturated = max(r, g, b) - min(r, g, b) >= 60
    short = len(str(el.get("content", "")).split()) <= 6
    return el.get("font_weight") == "bold" and saturated and short


def _pill_score(d: Design, u: Unit) -> float:
    # Area dominates; sitting in the lower half nudges ties toward the CTA,
    # which is where designers almost always put it.
    lower = (u.rect[1] + u.rect[3]) / 2 >= d.height / 2
    return area(u.rect) * (1.2 if lower else 1.0) * _unit_text(d, u)["font_size"]


def _badge_set_key(d: Design, u: Unit, badges: list[Unit]) -> str:
    """Badges that share a row (or column) and a height are one set."""
    h = u.rect[3] - u.rect[1]
    cy = (u.rect[1] + u.rect[3]) / 2
    peers = [b for b in badges
             if abs((b.rect[3] - b.rect[1]) - h) <= 0.15 * h
             and abs((b.rect[1] + b.rect[3]) / 2 - cy) <= 0.5 * h]
    return "badges:" + min(p.uid for p in peers)


def _logo_score(d: Design, u: Unit) -> float:
    el = d.els[u.members[0]]
    W, H = d.width, d.height
    r = u.rect
    vw, vh = r[2] - r[0], r[3] - r[1]
    score = 0.0
    if area(r) <= 0.06 * W * H:
        score += 0.5
    cy = (r[1] + r[3]) / 2
    cx = (r[0] + r[2]) / 2
    if cy <= 0.25 * H or cy >= 0.85 * H or cx <= 0.2 * W or cx >= 0.8 * W:
        score += 0.4  # logos live near an edge
    if vw >= 1.2 * vh:
        score += 0.2
    if el.get("asset_id") in BRAND_LOGO_ASSETS:
        score += 1.0
    return score
