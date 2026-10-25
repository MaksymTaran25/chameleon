"""Run the engine over every task x every catalogue format, score it, render it.

    python -m tools.sweep                       # all inputs, all formats, rendered
    python -m tools.sweep --no-render           # scores only, a few seconds
    python -m tools.sweep --formats Leaderboard "Instagram Story"
    python -m tools.sweep --scramble            # random ids and asset names
    python -m tools.sweep --engine engine.resize:resize

Writes runs/<timestamp>/ with one contact sheet per task, every PNG and JSON,
results.csv and summary.md. Not shipped in the Docker image.
"""

import argparse
import csv
import importlib
import json
import os
import random
import re
import shutil
import string
import sys
import tempfile
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

from engine.chameleon.formats import CATALOGUE, Format
from engine.chameleon.paths import REPO_ROOT, assets_dir, fonts_dir
from engine.chameleon.score import WEIGHTS, score

CELL = 300
LABEL_H = 58


# ----------------------------------------------------------------- scramble

def _token(rng: random.Random, prefix: str) -> str:
    return prefix + "".join(rng.choices(string.ascii_lowercase + string.digits, k=6))


def scramble_task(task: dict[str, Any], rng: random.Random, asset_map: dict[str, str]) -> dict[str, Any]:
    """Rename every id, group_id and asset_id so nothing can lean on names.
    `asset_map` is shared across tasks so one asset keeps one fake name."""
    task = json.loads(json.dumps(task))
    ids = {e["id"]: _token(rng, "e_") for e in task["source"]["elements"]}
    groups: dict[str, str] = {}
    for el in task["source"]["elements"]:
        el["id"] = ids[el["id"]]
        if el.get("group_id"):
            el["group_id"] = groups.setdefault(el["group_id"], _token(rng, "g_"))
        if el.get("asset_id"):
            ext = Path(el["asset_id"]).suffix
            el["asset_id"] = asset_map.setdefault(el["asset_id"], _token(rng, "a_") + ext)
    return task


def build_scrambled_assets(asset_map: dict[str, str], src: Path) -> Path:
    dst = Path(tempfile.mkdtemp(prefix="chameleon_assets_"))
    for real, fake in asset_map.items():
        if (src / real).is_file():  # a missing asset has to stay missing
            shutil.copy(src / real, dst / fake)
    return dst


# ------------------------------------------------------------------- worker

def _load_engine(spec: str):
    module, _, fn = spec.partition(":")
    return getattr(importlib.import_module(module), fn or "resize")


def run_case(engine_spec: str, task: dict[str, Any], stem: str, fmt: Optional[Format],
             out_dir: str, render: bool) -> dict[str, Any]:
    """One task at one size. `fmt=None` means the task's own target_canvas."""
    from renderer.converter.convert import render_design_to_png

    target = {"width": fmt.width, "height": fmt.height} if fmt else task["target_canvas"]
    name = fmt.name if fmt else "task target"
    slug = fmt.slug if fmt else "task_target"
    row: dict[str, Any] = {"task": stem, "format": name, "width": target["width"],
                           "height": target["height"]}
    case_dir = Path(out_dir) / stem
    case_dir.mkdir(parents=True, exist_ok=True)
    try:
        t0 = time.time()
        output = _load_engine(engine_spec)(task["source"], target)
        row["engine_s"] = round(time.time() - t0, 3)
        report = getattr(sys.modules.get("engine.chameleon.select"), "LAST_REPORT", {})
        row["template"] = report.get("chosen", "")
        (case_dir / f"{slug}.json").write_text(json.dumps(output, indent=2))
        s = score(task["source"], output, target)
        row.update(total=round(s.total, 4), passed=s.passed, hard="; ".join(s.hard),
                   notes="; ".join(s.notes), **{k: round(v, 3) for k, v in s.parts.items()})
        if render:
            png = case_dir / f"{slug}.png"
            render_design_to_png(output, assets_dir(), png, fonts_dir=fonts_dir(), max_size=1024)
            row["png"] = str(png)
    except Exception as exc:  # a crash is a result too; the scorer would see one
        row.update(total=0.0, passed=False, hard=f"CRASH {type(exc).__name__}: {exc}",
                   notes=traceback.format_exc(limit=3).replace("\n", " | "))
    return row


def render_source(task: dict[str, Any], stem: str, out_dir: str) -> str:
    from renderer.converter.convert import render_design_to_png
    png = Path(out_dir) / stem / "_source.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    render_design_to_png(task["source"], assets_dir(), png, fonts_dir=fonts_dir(), max_size=1024)
    return str(png)


# ------------------------------------------------------------ contact sheet

def _font(size: int):
    try:
        return ImageFont.truetype(str(fonts_dir() / "SolsticeSans-Regular.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def contact_sheet(stem: str, source_png: Optional[str], rows: list[dict[str, Any]], path: Path) -> None:
    cells = [("SOURCE", None, source_png)] + [(r["format"], r, r.get("png")) for r in rows]
    draw_cells(stem, cells, path)


def draw_cells(title: str, cells: list[tuple[str, Optional[dict], Optional[str]]], path: Path) -> None:
    cols = 6
    n_rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * CELL, n_rows * (CELL + LABEL_H) + 40), (245, 242, 236))
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 10), title, fill=(30, 30, 30), font=_font(20))
    small, bold = _font(13), _font(15)
    for i, (name, row, png) in enumerate(cells):
        x, y = (i % cols) * CELL, 40 + (i // cols) * (CELL + LABEL_H)
        draw.rectangle([x + 4, y + 4, x + CELL - 4, y + CELL - 4], fill=(225, 221, 214))
        if png and Path(png).is_file():
            im = Image.open(png).convert("RGB")
            im.thumbnail((CELL - 12, CELL - 12))
            sheet.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))
        draw.text((x + 6, y + CELL + 2), name, fill=(30, 30, 30), font=bold)
        if row is None:
            continue
        draw.text((x + 6, y + CELL + 20), f'{row["width"]}x{row["height"]}', fill=(90, 90, 90), font=small)
        if row.get("passed"):
            draw.text((x + 6, y + CELL + 36), f'score {row["total"]:.2f}  {row.get("template", "")}',
                      fill=(20, 110, 60), font=small)
        else:
            reason = (row.get("hard") or "").split(";")[0][:38]
            draw.text((x + 6, y + CELL + 36), f"FAIL {reason}", fill=(180, 30, 30), font=small)
    sheet.save(path)


# --------------------------------------------------------------------- main

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", nargs="*", default=None, help="task JSONs (default: input/*.json)")
    ap.add_argument("--formats", nargs="*", default=None, help="catalogue names (default: all)")
    ap.add_argument("--engine", default="engine.resize:resize")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--render-worst", type=int, default=0, metavar="N",
                    help="with --no-render: afterwards render only the N lowest-scoring cases, "
                         "each next to its source, on sheet_worst.png")
    ap.add_argument("--worst-min-side", type=int, default=0, metavar="PX",
                    help="only consider targets whose short side is at least PX for --render-worst")
    ap.add_argument("--scramble", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 2))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    inputs = [Path(p) for p in args.inputs] if args.inputs else sorted((REPO_ROOT / "input").glob("*.json"))
    formats = CATALOGUE
    if args.formats:
        wanted = {f.lower() for f in args.formats}
        formats = [f for f in CATALOGUE if f.name.lower() in wanted]
        unknown = wanted - {f.name.lower() for f in formats}
        if unknown:
            ap.error(f"unknown formats: {sorted(unknown)}")

    out_dir = Path(args.out) if args.out else REPO_ROOT / "runs" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = {p.stem: json.loads(p.read_text()) for p in inputs}
    scratch = None
    if args.scramble:
        rng, asset_map = random.Random(args.seed), {}
        tasks = {stem: scramble_task(t, rng, asset_map) for stem, t in tasks.items()}
        scratch = build_scrambled_assets(asset_map, assets_dir())
        # workers are spawned after this, so they inherit it
        os.environ["CHAMELEON_ASSETS_DIR"] = str(scratch)
        os.environ["CHAMELEON_FONTS_DIR"] = str(fonts_dir())

    render = not args.no_render
    t0 = time.time()
    rows: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(run_case, args.engine, t, stem, None, str(out_dir), render)
                for stem, t in tasks.items()]
        futs += [pool.submit(run_case, args.engine, t, stem, f, str(out_dir), render)
                 for stem, t in tasks.items() for f in formats]
        src_futs = {stem: pool.submit(render_source, t, stem, str(out_dir)) for stem, t in tasks.items()} if render else {}
        rows = [f.result() for f in futs]
        sources = {stem: f.result() for stem, f in src_futs.items()}

    if render:
        for stem in tasks:
            contact_sheet(stem, sources.get(stem), [r for r in rows if r["task"] == stem],
                          out_dir / f"sheet_{stem}.png")
    elif args.render_worst:
        by_name = {f.name: f for f in CATALOGUE}
        pool_rows = [r for r in rows if min(r["width"], r["height"]) >= args.worst_min_side]
        worst = sorted(pool_rows, key=lambda r: r["total"])[:args.render_worst]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(run_case, args.engine, tasks[r["task"]], r["task"],
                                by_name.get(r["format"]), str(out_dir), True) for r in worst]
            src_futs = {stem: pool.submit(render_source, tasks[stem], stem, str(out_dir))
                        for stem in {r["task"] for r in worst}}
            rendered = [f.result() for f in futs]
            srcs = {stem: f.result() for stem, f in src_futs.items()}
        cells = []
        for r in rendered:
            cells.append((f"{r['task']} source", None, srcs[r["task"]]))
            cells.append((r["format"], r, r.get("png")))
        draw_cells(f"worst {len(rendered)}", cells, out_dir / "sheet_worst.png")

    keys = ["task", "format", "width", "height", "template", "total", "passed", *WEIGHTS, "engine_s", "hard", "notes", "png"]
    with open(out_dir / "results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    lines = [f"# Sweep {out_dir.name}", "",
             f"engine `{args.engine}`, {len(tasks)} tasks x {len(formats)} formats (+ each task's own target)"
             f"{', scrambled' if args.scramble else ''}, {time.time() - t0:.0f}s", "",
             "| task | pass rate | mean score | own target | worst part |", "|---|---|---|---|---|"]
    for stem in tasks:
        rs = [r for r in rows if r["task"] == stem]
        own = next(r for r in rs if r["format"] == "task target")
        passed = [r for r in rs if r.get("passed")]
        mean = sum(r["total"] for r in rs) / len(rs)
        parts = {k: sum(r.get(k, 0) for r in rs) / len(rs) for k in WEIGHTS}
        worst = min(parts, key=parts.get)
        lines.append(f"| {stem} | {len(passed)}/{len(rs)} | {mean:.3f} | "
                     f"{own['total']:.3f}{'' if own.get('passed') else ' FAIL'} | {worst} {parts[worst]:.2f} |")
    all_mean = sum(r["total"] for r in rows) / len(rows)
    lines += ["", f"overall mean {all_mean:.3f}, pass rate {sum(bool(r.get('passed')) for r in rows)}/{len(rows)}"]
    hard_counts: dict[str, int] = {}
    for r in rows:
        for h in filter(None, (r.get("hard") or "").split("; ")):
            # "logo: aspect 2.560 vs source 3.200" -> "aspect", so causes group
            key = h[:60] if h.startswith("CRASH") else re.sub(r"[\d.]+", "", h.split(":", 1)[-1]).split(" vs ")[0].strip()
            hard_counts[key] = hard_counts.get(key, 0) + 1
    if hard_counts:
        lines += ["", "most common hard failures:"]
        lines += [f"- {n}x {k}" for k, n in sorted(hard_counts.items(), key=lambda kv: -kv[1])[:8]]
    summary = "\n".join(lines)
    (out_dir / "summary.md").write_text(summary + "\n")
    print(summary)
    print(f"\n-> {out_dir}")

    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
