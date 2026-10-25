"""Show what the engine thinks a design is made of.

    python -m tools.inspect_task input/chameleon_sample_level3_advanced.json

Prints units, roles and visible rects, and writes <task>_inspect.png next to
the run output: the real render with element boxes (thin) and visible rects
(thick) drawn over it, coloured by role.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from renderer.converter.convert import render_design_to_png

from engine.chameleon.parse import Design, box
from engine.chameleon.paths import REPO_ROOT, assets_dir, fonts_dir
from engine.chameleon.roles import infer_roles

ROLE_COLORS = {
    "background": (120, 120, 120), "decoration": (160, 160, 200), "panel": (140, 140, 140),
    "logo": (255, 200, 0), "hero": (0, 200, 255), "headline": (255, 60, 60),
    "subhead": (255, 140, 200), "cta": (0, 230, 120), "badge": (150, 255, 80),
    "sticker": (255, 120, 0), "fineprint": (200, 200, 200), "misc_image": (180, 90, 255),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--out", default=str(REPO_ROOT / "runs" / "inspect"))
    args = ap.parse_args(argv)

    task = json.loads(Path(args.task).read_text())
    d = Design(task["source"])
    roles = infer_roles(d)

    print(f"{Path(args.task).name}: {d.width}x{d.height} -> "
          f"{task['target_canvas']['width']}x{task['target_canvas']['height']}")
    print(f"{'unit':<22} {'role':<11} {'imp':>4}  {'visible rect':<26} members")
    for u in sorted(d.units, key=lambda u: -roles[u.uid].importance):
        r = roles[u.uid]
        rect = "(%d, %d, %d, %d)" % tuple(round(v) for v in u.rect)
        extra = f"  [{r.set_id}]" if r.set_id else ""
        print(f"{u.uid:<22} {r.role:<11} {r.importance:>4.2f}  {rect:<26} {', '.join(u.members)}{extra}")
    missing = [e for e in d.els if d.is_missing_asset(e)]
    if missing:
        print(f"missing assets: {missing}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    png = out / f"{Path(args.task).stem}_inspect.png"
    render_design_to_png(task["source"], assets_dir(), png, fonts_dir=fonts_dir())
    im = Image.open(png).convert("RGB")
    s = im.width / d.width
    draw = ImageDraw.Draw(im)
    for u in d.units:
        color = ROLE_COLORS.get(roles[u.uid].role, (255, 255, 255))
        for m in u.members:
            draw.rectangle([v * s for v in box(d.els[m])], outline=color, width=1)
        draw.rectangle([v * s for v in u.rect], outline=color, width=4)
        draw.text((u.rect[0] * s + 4, u.rect[1] * s + 4), roles[u.uid].role, fill=color)
    im.save(png)
    print(f"-> {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
