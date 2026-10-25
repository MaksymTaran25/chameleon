"""Render pairs of competing candidates for a human to judge.

    python -m tools.pairs --n 40 --out tune/pairs

For each sampled (task, format) it scores every candidate template and keeps
the best one plus the best from a different family (stack vs split vs strip
vs letterbox), since those are the choices the weights actually decide.
Left and right are shuffled so the judge can't learn "left is the engine's
pick". Writes one montage per 8 pairs and pairs.json with every candidate's
score parts, which tools/fit_weights.py reads together with the labels.
"""

import argparse
import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from engine.chameleon.formats import CATALOGUE
from engine.chameleon.paths import REPO_ROOT, assets_dir, fonts_dir

PANEL_W, PANEL_H, LABEL = 440, 300, 26


def make_pair(task_path: str, fmt_name: str, idx: int, out_dir: str, flip: bool) -> dict[str, Any]:
    from renderer.converter.convert import render_design_to_png

    from engine.chameleon.layout import Ctx
    from engine.chameleon.parse import Design
    from engine.chameleon.score import score
    from engine.chameleon.templates import candidates

    task = json.loads(Path(task_path).read_text())
    fmt = next(f for f in CATALOGUE if f.name == fmt_name)
    target = {"width": fmt.width, "height": fmt.height}
    ctx = Ctx(Design(task["source"]), fmt.width, fmt.height)
    scored = []
    for name, make in candidates(ctx):
        try:
            doc = make()
        except Exception:
            continue
        if doc is None:
            continue
        s = score(task["source"], doc, target)
        if s.passed:
            scored.append((s.total, name, doc, s.parts))
    scored.sort(key=lambda t: -t[0])
    if len(scored) < 2:
        return {"idx": idx, "skip": True}
    a = scored[0]
    family = a[1].split("/")[0]
    b = next((c for c in scored[1:] if c[1].split("/")[0] != family), scored[1])
    left, right = (b, a) if flip else (a, b)
    out = Path(out_dir)
    pngs = []
    for side, c in (("L", left), ("R", right)):
        png = out / f"pair{idx:03d}_{side}.png"
        render_design_to_png(c[2], assets_dir(), png, fonts_dir=fonts_dir(), max_size=900)
        # keep the layout itself so the weights can be refit against a newer scorer
        (out / f"pair{idx:03d}_{side}.json").write_text(json.dumps(c[2]))
        pngs.append(str(png))
    return {
        "idx": idx, "task": Path(task_path).stem, "task_path": task_path, "format": fmt_name,
        "left": {"name": left[1], "total": left[0], "parts": left[3], "png": pngs[0]},
        "right": {"name": right[1], "total": right[0], "parts": right[3], "png": pngs[1]},
    }


def montage(pairs: list[dict], path: Path) -> None:
    sheet = Image.new("RGB", (2 * PANEL_W + 30, len(pairs) * (PANEL_H + LABEL + 10)), (245, 242, 236))
    draw = ImageDraw.Draw(sheet)
    for row, p in enumerate(pairs):
        y = row * (PANEL_H + LABEL + 10)
        draw.text((6, y + 4), f"#{p['idx']}  {p['task']}  {p['format']}", fill=(20, 20, 20))
        for col, side in enumerate(("left", "right")):
            x = 10 + col * (PANEL_W + 10)
            draw.rectangle([x, y + LABEL, x + PANEL_W, y + LABEL + PANEL_H], fill=(215, 211, 204))
            im = Image.open(p[side]["png"]).convert("RGB")
            im.thumbnail((PANEL_W, PANEL_H))
            sheet.paste(im, (x + (PANEL_W - im.width) // 2, y + LABEL + (PANEL_H - im.height) // 2))
            draw.text((x + 4, y + LABEL + 4), "L" if col == 0 else "R", fill=(255, 255, 0))
    sheet.save(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=str(REPO_ROOT / "tune" / "pairs"))
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    tasks = sorted((REPO_ROOT / "corpus").glob("*.json")) + sorted((REPO_ROOT / "input").glob("*.json"))
    jobs = [(str(rng.choice(tasks)), rng.choice(CATALOGUE).name, i, rng.random() < 0.5)
            for i in range(int(args.n * 1.5))]  # some will have no rival; oversample
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=8) as pool:
        results = [f.result() for f in [pool.submit(make_pair, t, f, i, str(out), flip) for t, f, i, flip in jobs]]
    pairs = [r for r in results if not r.get("skip")][:args.n]
    (out / "pairs.json").write_text(json.dumps(pairs, indent=2))
    for i in range(0, len(pairs), 8):
        montage(pairs[i:i + 8], out / f"montage_{i // 8}.png")
    print(f"{len(pairs)} pairs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
