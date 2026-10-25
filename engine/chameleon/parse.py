"""Geometry and structure of a design: visible bounds, rotated boxes, units.

The element box is often not what the viewer sees. The brand kit PNGs carry
a lot of transparent padding (the bottle is 341x551 visible inside 800x800),
text only fills part of its box, and rotation moves the corners. Everything
that judges a layout - alignment, collisions, bounds - should look at the
visible rect, so this module is where that gets worked out once.
"""

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from renderer.converter.colorutil import parse_rgba

from .measure import measure_text
from .paths import assets_dir as default_assets_dir

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1

# Alpha below this counts as empty; anti-aliased halos otherwise inflate the
# bbox by a few pixels on every side.
_ALPHA_THRESHOLD = 8


# ---------------------------------------------------------------- rect math

def area(r: Rect) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def intersect(a: Rect, b: Rect) -> Rect:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def overlap_area(a: Rect, b: Rect) -> float:
    return area(intersect(a, b))


def union(rects: list[Rect]) -> Rect:
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def contains(outer: Rect, inner: Rect, tol: float = 0.5) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def box(el: dict[str, Any]) -> Rect:
    return (el["x"], el["y"], el["x"] + el["width"], el["y"] + el["height"])


def rotate_rect(r: Rect, degrees: float, cx: float, cy: float) -> Rect:
    """Axis-aligned bounds of `r` turned about (cx, cy). Positive degrees is
    clockwise in screen coordinates, which is what skia's rotate does."""
    if not degrees:
        return r
    t = math.radians(degrees)
    cos, sin = math.cos(t), math.sin(t)
    xs, ys = [], []
    for x, y in ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])):
        dx, dy = x - cx, y - cy
        xs.append(cx + dx * cos - dy * sin)
        ys.append(cy + dx * sin + dy * cos)
    return (min(xs), min(ys), max(xs), max(ys))


def effective_alpha(el: dict[str, Any]) -> float:
    """How opaque an element really paints: the converter multiplies a
    shape's opacity by the alpha inside its fill_color."""
    alpha = float(el.get("opacity", 1.0))
    if el["type"] == "shape":
        alpha *= parse_rgba(el.get("fill_color"), default=(0, 0, 0, 1.0))[3]
    elif el["type"] == "text":
        alpha *= parse_rgba(el.get("color"), default=(0, 0, 0, 1.0))[3]
    return alpha


def rotated_box(el: dict[str, Any]) -> Rect:
    b = box(el)
    return rotate_rect(b, el.get("rotation", 0) or 0, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


# ------------------------------------------------------------------- assets

@dataclass(frozen=True)
class AssetInfo:
    width: int
    height: int
    alpha_bbox: Rect  # in asset pixels; the whole image when it is opaque


@lru_cache(maxsize=256)
def _asset_info(path: str) -> AssetInfo:
    im = Image.open(path)
    bbox = None
    if im.mode in ("RGBA", "LA") or "transparency" in im.info:
        alpha = im.convert("RGBA").getchannel("A")
        bbox = alpha.point(lambda a: 255 if a > _ALPHA_THRESHOLD else 0).getbbox()
    return AssetInfo(im.width, im.height, tuple(bbox) if bbox else (0, 0, im.width, im.height))


def asset_info(asset_id: Optional[str], assets: Optional[Path] = None) -> Optional[AssetInfo]:
    if not asset_id:
        return None
    path = Path(assets or default_assets_dir()) / asset_id
    return _asset_info(str(path)) if path.is_file() else None


def image_visible_rect(el: dict[str, Any], info: Optional[AssetInfo]) -> Rect:
    """Where the opaque pixels of an image land, mirroring the converter's
    contain/cover/fill emulation in renderer/converter/assets.py."""
    b = box(el)
    if info is None:
        return b  # missing asset: the placeholder fills the whole box
    bw, bh = el["width"], el["height"]
    ax0, ay0, ax1, ay1 = info.alpha_bbox
    fit = el.get("fit", "contain")
    if fit == "fill":
        sx, sy = bw / info.width, bh / info.height
        return (b[0] + ax0 * sx, b[1] + ay0 * sy, b[0] + ax1 * sx, b[1] + ay1 * sy)
    if fit == "cover":
        s = max(bw / info.width, bh / info.height)
    else:
        s = min(bw / info.width, bh / info.height)
    ox = b[0] + (bw - info.width * s) / 2
    oy = b[1] + (bh - info.height * s) / 2
    r = (ox + ax0 * s, oy + ay0 * s, ox + ax1 * s, oy + ay1 * s)
    return intersect(r, b) if fit == "cover" else r


def text_visible_rect(el: dict[str, Any]) -> Rect:
    block = measure_text(el)
    align = el.get("text_align", "left")
    x0, x1 = math.inf, -math.inf
    for w in block.line_widths:
        if align == "center":
            off = (el["width"] - w) / 2
        elif align == "right":
            off = el["width"] - w
        else:
            off = 0.0
        x0, x1 = min(x0, el["x"] + off), max(x1, el["x"] + off + w)
    if x0 > x1:  # empty string
        x0 = x1 = el["x"]
    return (x0, el["y"], x1, el["y"] + block.height)


# -------------------------------------------------------------------- units

@dataclass
class Unit:
    """Elements that should move as one rigid body."""
    uid: str
    members: list[str]
    kind: str  # "group" (shared group_id), "contained" (text inside a shape) or "single"
    rect: Rect = (0, 0, 0, 0)  # visible union, filled in by Design
    z: int = 0


@dataclass
class Design:
    """A design plus everything derived from it that layout code keeps asking for."""
    doc: dict[str, Any]
    assets: Optional[Path] = None
    els: dict[str, dict[str, Any]] = field(init=False)
    units: list[Unit] = field(init=False)
    unit_of: dict[str, Unit] = field(init=False)

    def __post_init__(self) -> None:
        self.els = {e["id"]: e for e in self.doc["elements"]}
        self.units = build_units(self.doc["elements"])
        self.unit_of = {m: u for u in self.units for m in u.members}
        for u in self.units:
            u.rect = union([self.visible(m) for m in u.members])
            u.z = max(self.els[m].get("z_index", 0) for m in u.members)

    @property
    def width(self) -> float:
        return self.doc["canvas"]["width"]

    @property
    def height(self) -> float:
        return self.doc["canvas"]["height"]

    @property
    def canvas_rect(self) -> Rect:
        return (0.0, 0.0, float(self.width), float(self.height))

    def asset(self, eid: str) -> Optional[AssetInfo]:
        el = self.els[eid]
        return asset_info(el.get("asset_id"), self.assets) if el["type"] == "image" else None

    def is_missing_asset(self, eid: str) -> bool:
        return self.els[eid]["type"] == "image" and self.asset(eid) is None

    def visible(self, eid: str) -> Rect:
        """Visible rect, after rotation, in canvas pixels."""
        el = self.els[eid]
        if el["type"] == "image":
            r = image_visible_rect(el, self.asset(eid))
        elif el["type"] == "text":
            r = text_visible_rect(el)
        else:
            r = box(el)
        b = box(el)
        return rotate_rect(r, el.get("rotation", 0) or 0, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def build_units(elements: list[dict[str, Any]]) -> list[Unit]:
    units: list[Unit] = []
    grouped: dict[str, list[str]] = {}
    for el in elements:
        if el.get("group_id"):
            grouped.setdefault(el["group_id"], []).append(el["id"])
    for gid, members in grouped.items():
        units.append(Unit(f"group:{gid}", members, "group"))
    taken = {m for u in units for m in u.members}

    # Buttons and badges don't always come with a group_id. A text whose box
    # sits inside a shape drawn under it is the same designer intent.
    loose = [e for e in elements if e["id"] not in taken]
    # Faint shapes are decoration, not containers, even when a text happens
    # to fall inside their box.
    shapes = [e for e in loose if e["type"] == "shape" and effective_alpha(e) >= 0.5]
    claimed: set[str] = set()
    for text in (e for e in loose if e["type"] == "text"):
        hosts = [s for s in shapes
                 if s["id"] not in claimed
                 and s.get("z_index", 0) < text.get("z_index", 0)
                 and (s.get("rotation", 0) or 0) == (text.get("rotation", 0) or 0)
                 and contains(box(s), box(text), tol=2.0)
                 # a headline on a big colour panel is not a button
                 and area(box(text)) >= 0.1 * area(box(s))]
        if hosts:
            host = min(hosts, key=lambda s: s["width"] * s["height"])  # the tightest one
            claimed.update({host["id"], text["id"]})
            units.append(Unit(f"unit:{host['id']}", [host["id"], text["id"]], "contained"))

    for el in loose:
        if el["id"] not in claimed:
            units.append(Unit(el["id"], [el["id"]], "single"))
    return units
