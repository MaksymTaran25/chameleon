"""Text measurement that agrees with the renderer.

We go through the converter's own wrap code and font lookup, so a "fits" here
means it fits in the PNG. Two renderer quirks shape this module:

- cr-renderer draws at int(font_size) but the converter wraps at the float
  size, so the engine should only ever emit whole-number font sizes.
- Text is drawn from the top of its box and never clipped. A box that is too
  short does not hide anything, the text just spills over whatever is below.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

import skia  # type: ignore

from renderer.converter.brand_fonts import load_brand_font_manager
from renderer.converter.convert import _normalize_weight, _weight_style_to_font_style, _BOLD_WEIGHTS
from renderer.converter.wrap import char_widths, wrap_text

from .paths import fonts_dir

DEFAULT_LINE_HEIGHT = 1.2


@dataclass(frozen=True)
class TextBlock:
    lines: tuple[str, ...]
    line_widths: tuple[float, ...]
    width: float   # widest rendered line
    height: float  # top of the first line's ascent to the bottom of the last line's descent

    @property
    def n_lines(self) -> int:
        return len(self.lines)


@lru_cache(maxsize=1)
def font_manager():
    return load_brand_font_manager(str(fonts_dir()))


@lru_cache(maxsize=512)
def _metrics(family: str, weight: str, style: str, size: int) -> tuple[float, float]:
    typeface = skia.Typeface.MakeFromData(font_manager().lookup(family, weight, style))
    m = skia.Font(typeface, size).getMetrics()
    return m.fAscent, m.fDescent


def _font_key(el: dict[str, Any]) -> tuple[str, str, str]:
    weight = _normalize_weight(el.get("font_weight"))
    italic = el.get("font_style") == "italic"
    style = _weight_style_to_font_style(weight in _BOLD_WEIGHTS, italic)
    return el.get("font_family", "Montserrat"), weight, style


@lru_cache(maxsize=8192)
def _measure(content: str, family: str, weight: str, style: str, size: int,
             width: float, line_height: float, letter_spacing: float) -> TextBlock:
    widths = char_widths(font_manager(), family, weight, style, size, content)
    wrapped = wrap_text(content, widths, width)

    lines, line_widths = [], []
    start = 0
    for i, c in enumerate(wrapped + "\n"):
        if c != "\n":
            continue
        line = wrapped[start:i]
        # wrap_text keeps every char at its original index, so widths line up
        w = sum(widths[start:i]) +letter_spacing * max(0, len(line) - 1)
        lines.append(line)
        line_widths.append(w)
        start = i + 1

    ascent, descent = _metrics(family, weight, style, size)
    height = (len(lines) - 1) * size * line_height + (descent - ascent)
    return TextBlock(tuple(lines), tuple(line_widths), max(line_widths, default=0.0), height)


def measure_text(el: dict[str, Any], font_size: Optional[float] = None,
                 width: Optional[float] = None) -> TextBlock:
    """Wrap and measure a text element, optionally at a different size or box width."""
    family, weight, style = _font_key(el)
    size = int(font_size if font_size is not None else el["font_size"])
    return _measure(
        str(el.get("content", "")), family, weight, style, max(1, size),
        float(width if width is not None else el["width"]),
        float(el.get("line_height", DEFAULT_LINE_HEIGHT)),
        float(el.get("letter_spacing", 0.0)),
    )


def fit_font_size(el: dict[str, Any], width: float, height: float,
                  min_size: int, max_size: int) -> tuple[int, TextBlock, bool]:
    """Largest whole font size in [min_size, max_size] whose wrapped block fits
    width x height. Returns (size, block, fits); when nothing fits we return
    min_size with fits=False and let the caller decide what gives."""
    min_size, max_size = max(1, int(min_size)), max(1, int(max_size))
    if max_size < min_size:
        max_size = min_size

    def fits(size: int) -> Optional[TextBlock]:
        block = measure_text(el, size, width)
        if block.width <= width + 0.5 and block.height <= height + 0.5:
            return block
        return None

    # Fit is monotone in size except at wrap boundaries, which are rare and
    # small enough that a plain binary search is the right trade.
    lo, hi, best = min_size, max_size, None
    while lo <= hi:
        mid = (lo + hi) // 2
        block = fits(mid)
        if block is not None:
            best = (mid, block)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return min_size, measure_text(el, min_size, width), False
    return best[0], best[1], True
