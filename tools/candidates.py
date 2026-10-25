"""Score every candidate template for one task at one size, to see why the
selector picked what it picked.

    python -m tools.candidates input/chameleon_sample_level3_advanced.json 1600 400
"""

import json
import sys

from engine.chameleon.layout import Ctx
from engine.chameleon.parse import Design
from engine.chameleon.score import score
from engine.chameleon.templates import candidates


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 3:
        print(__doc__)
        return 2
    task = json.load(open(argv[0]))
    W, H = int(argv[1]), int(argv[2])
    src = task["source"]
    ctx = Ctx(Design(src), W, H)
    print(f"{W}x{H} class={ctx.cls}")
    for name, make in candidates(ctx):
        doc = make()
        if doc is None:
            print(f"{name:28} n/a")
            continue
        s = score(src, doc, {"width": W, "height": H})
        print(f"{name:28} {s.total:.3f} pass={s.passed}", {k: round(v, 2) for k, v in s.parts.items()})
        for h in s.hard[:3]:
            print("    HARD", h)
        for n in s.notes[:4]:
            print("    -", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
