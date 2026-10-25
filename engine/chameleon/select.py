"""Generate every candidate layout for a target, score each, keep the best.

The engine must hand back something valid for any input, so a template that
throws is skipped rather than fatal, and if every template fails we fall
back to letterbox and, past that, to a clamped copy of the source.
"""

import traceback
from typing import Any

from .layout import Ctx, compose
from .parse import Design
from .score import score
from .templates import candidates, letterbox

# What the last resize() picked and why. The sweep harness reads it; the
# engine itself never does.
LAST_REPORT: dict[str, Any] = {}


def resize_design(source: dict[str, Any], target_canvas: dict[str, Any]) -> dict[str, Any]:
    target = {"width": int(target_canvas["width"]), "height": int(target_canvas["height"])}
    ctx = Ctx(Design(source), target["width"], target["height"])

    tried = []
    for name, make in candidates(ctx):
        try:
            doc = make()
        except Exception:
            tried.append((name, None, traceback.format_exc(limit=2)))
            continue
        if doc is None:
            continue
        s = score(source, doc, target)
        tried.append((name, doc, s))

    scored = [(n, d, s) for n, d, s in tried if d is not None]
    # a candidate that passes every hard gate always beats one that doesn't
    scored.sort(key=lambda t: (t[2].passed, t[2].total if t[2].passed else sum(t[2].parts.values())),
                reverse=True)
    LAST_REPORT.clear()
    LAST_REPORT["candidates"] = [(n, round(s.total, 4), s.passed) if d is not None else (n, None, s)
                                 for n, d, s in tried]
    if scored:
        LAST_REPORT["chosen"] = scored[0][0]
        return scored[0][1]

    LAST_REPORT["chosen"] = "fallback"
    try:
        return letterbox(ctx)
    except Exception:
        return compose(ctx, {})
