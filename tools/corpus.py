"""Generate a seeded corpus of test designs beyond the three samples.

    python -m tools.corpus                 # 48 tasks into corpus/
    python -m tools.corpus --n 120 --seed 7 --out corpus_big

Three samples are far too few to tune on; anything tuned to them would
fail the hidden set by design. Each task here starts from a base design and
applies one to three mutations, chosen to hit what the hidden set is likely
to vary: copy length, badge count, which roles are present, left-anchored
layouts, rotation, missing assets, weak contrast, and a source canvas that
is itself landscape or tall.

Landscape and tall bases come from running the engine on a sample. That is
a little circular, but those are valid designs with tight boxes, which is
exactly what a real designer's file looks like, and it is the only way to
get non-square sources out of a portrait-only kit.

Ids are renamed per task, so nothing downstream can lean on "headline".
"""

import argparse
import copy
import json
import random
import string
from pathlib import Path
from typing import Any, Callable

from engine.chameleon.formats import CATALOGUE
from engine.chameleon.paths import REPO_ROOT

Doc = dict[str, Any]

WORDS = ("golden hour light citrus botanical sparkling summer infusion crafted wild honey "
         "blood orange zest rosemary bottled sunset taste batch limited refreshing bright "
         "natural crisp evening garden bloom fizz bold gentle slow small coastal").split()
BADGE_WORDS = ["NEW", "VEGAN", "ZERO SUGAR", "LIMITED EDITION", "AWARD WINNING 2026",
               "SMALL BATCH", "ORGANIC", "LOCALLY MADE", "SPARKLING", "4 FLAVORS"]


def _sentence(rng: random.Random, n: int) -> str:
    words = [rng.choice(WORDS) for _ in range(n)]
    return " ".join(words).capitalize() + "."


def _title(rng: random.Random, n: int) -> str:
    return " ".join(rng.choice(WORDS).capitalize() for _ in range(n))


def _by_size(doc: Doc, etype: str) -> list[dict]:
    els = [e for e in doc["elements"] if e["type"] == etype]
    return sorted(els, key=lambda e: -(e.get("font_size", 0) or e["width"] * e["height"]))


def _texts(doc: Doc) -> list[dict]:
    return [e for e in doc["elements"] if e["type"] == "text" and not e.get("group_id")]


# ------------------------------------------------------------------ mutations

def m_headline_length(doc: Doc, rng: random.Random) -> str:
    head = max(_texts(doc), key=lambda e: e["font_size"])
    n = rng.choice([1, 2, 3, 6, 9, 14])
    head["content"] = _title(rng, n)
    # keep a two-colour headline two-coloured: colour the last couple of words
    if head.get("style_runs"):
        words = head["content"].split(" ")
        start = len(" ".join(words[:max(1, len(words) - 2)])) + (1 if len(words) > 1 else 0)
        run = dict(head["style_runs"][0])
        run.update(start=min(start, len(head["content"]) - 1), end=len(head["content"]))
        head["style_runs"] = [run]
    return f"headline {n} words"


def m_subhead_length(doc: Doc, rng: random.Random) -> str:
    texts = sorted(_texts(doc), key=lambda e: -e["font_size"])
    if len(texts) < 2:
        return "skip"
    sub = texts[1]
    n = rng.choice([3, 8, 20, 35])
    sub["content"] = _sentence(rng, n)
    sub.pop("style_runs", None)
    return f"subhead {n} words"


def m_badge_count(doc: Doc, rng: random.Random) -> str:
    """Replace whatever badges exist with n new ones in a row."""
    groups: dict[str, list[dict]] = {}
    for e in doc["elements"]:
        if e.get("group_id"):
            groups.setdefault(e["group_id"], []).append(e)
    pills = {g: es for g, es in groups.items()
             if {x["type"] for x in es} == {"shape", "text"} and not any(x.get("rotation") for x in es)}
    if not pills:
        return "skip"
    # the biggest pill is the call to action; keep it, badges are the rest
    cta = max(pills, key=lambda g: max(x["width"] * x["height"] for x in pills[g]))
    badge_groups = [g for g in pills if g != cta]
    template = pills[badge_groups[0]] if badge_groups else pills[cta]
    y = min(x["y"] for x in template) if badge_groups else min(x["y"] for x in pills[cta]) - 80
    doc["elements"] = [e for e in doc["elements"] if e.get("group_id") not in badge_groups]
    n = rng.choice([0, 1, 2, 4, 5])
    shape = next(x for x in template if x["type"] == "shape")
    label = next(x for x in template if x["type"] == "text")
    W = doc["canvas"]["width"]
    h = 50 if badge_groups else 46
    fs = label["font_size"] if badge_groups else 15
    words = rng.sample(BADGE_WORDS, n)
    widths = [max(110, len(w) * fs * 0.7 + 50) for w in words]
    gap = 20
    rows, row, cur = [], [], 0.0
    for w, word in zip(widths, words):
        if row and cur + gap + w > W - 120:
            rows.append(row)
            row, cur = [], 0.0
        row.append((w, word))
        cur += (gap if len(row) > 1 else 0) + w
    if row:
        rows.append(row)
    for ri, row in enumerate(rows):
        total = sum(w for w, _ in row) + gap * (len(row) - 1)
        x = (W - total) / 2
        for w, word in row:
            gid = "b" + "".join(rng.choices(string.ascii_lowercase, k=5))
            doc["elements"].append({**shape, "id": gid + "_bg", "group_id": gid, "x": x,
                                    "y": y + ri * (h + 14), "width": w, "height": h,
                                    "corner_radius": h / 2})
            doc["elements"].append({**label, "id": gid + "_label", "group_id": gid, "x": x,
                                    "y": y + ri * (h + 14) + (h - fs * 1.7) / 2, "width": w,
                                    "height": fs * 1.7, "font_size": fs, "content": word})
            x += w + gap
    return f"{n} badges"


def m_drop_role(doc: Doc, rng: random.Random) -> str:
    """Remove one element (or group) that is not the background or the headline."""
    head = max(_texts(doc), key=lambda e: e["font_size"])
    bg = max((e for e in doc["elements"] if e["type"] == "image"), key=lambda e: e["width"] * e["height"])
    options = []
    for e in doc["elements"]:
        if e is head or e is bg:
            continue
        options.append(e.get("group_id") or e["id"])
    if not options:
        return "skip"
    victim = rng.choice(sorted(set(options)))
    doc["elements"] = [e for e in doc["elements"] if (e.get("group_id") or e["id"]) != victim]
    return f"dropped {victim}"


def m_left_anchor(doc: Doc, rng: random.Random) -> str:
    """Re-anchor the content to the left margin, the other common poster layout."""
    W = doc["canvas"]["width"]
    margin = 0.07 * W
    bg = max((e for e in doc["elements"] if e["type"] == "image"), key=lambda e: e["width"] * e["height"])
    units: dict[str, list[dict]] = {}
    for e in doc["elements"]:
        if e is bg or (e["type"] == "shape" and e.get("opacity", 1) < 0.5):
            continue
        units.setdefault(e.get("group_id") or e["id"], []).append(e)
    for es in units.values():
        if any(x.get("rotation") for x in es):
            continue
        x0 = min(x["x"] for x in es)
        width = max(x["x"] + x["width"] for x in es) - x0
        if width > 0.9 * W:
            continue
        for x in es:
            x["x"] += margin - x0
            if x["type"] == "text":
                x["text_align"] = "left"
    return "left-anchored"


def m_rotate(doc: Doc, rng: random.Random) -> str:
    groups = sorted({e["group_id"] for e in doc["elements"] if e.get("group_id")})
    if not groups:
        return "skip"
    g = rng.choice(groups)
    angle = rng.choice([-10, -6, 6, 10])
    for e in doc["elements"]:
        if e.get("group_id") == g:
            e["rotation"] = angle
    return f"rotated {g} {angle}"


def m_missing_asset(doc: Doc, rng: random.Random) -> str:
    imgs = [e for e in doc["elements"] if e["type"] == "image" and e.get("fit") != "cover"]
    if not imgs:
        return "skip"
    e = rng.choice(imgs)
    e["asset_id"] = "missing_" + "".join(rng.choices(string.ascii_lowercase, k=6)) + ".png"
    return f"missing asset on {e['id']}"


def m_low_contrast(doc: Doc, rng: random.Random) -> str:
    texts = sorted(_texts(doc), key=lambda e: -e["font_size"])
    if len(texts) < 2:
        return "skip"
    texts[1]["color"] = "rgba(70,45,70,1)"
    return "dark subhead"


def m_style_runs(doc: Doc, rng: random.Random) -> str:
    head = max(_texts(doc), key=lambda e: e["font_size"])
    n = len(head["content"])
    if n < 4:
        return "skip"
    start = rng.randrange(1, n - 1)
    head["style_runs"] = [{"start": start, "end": n, "color": "rgba(244,180,60,1)"}]
    return "two-colour headline"


MUTATIONS: list[Callable[[Doc, random.Random], str]] = [
    m_headline_length, m_subhead_length, m_badge_count, m_drop_role, m_left_anchor,
    m_rotate, m_missing_asset, m_low_contrast, m_style_runs,
]


# ---------------------------------------------------------------------- bases

def load_bases() -> dict[str, Doc]:
    from engine.chameleon.select import resize_design  # imported late: it pulls in the renderer

    bases = {}
    for p in sorted((REPO_ROOT / "input").glob("*.json")):
        bases[p.stem.replace("chameleon_sample_", "")] = json.loads(p.read_text())["source"]
    derived = {}
    for name, src in bases.items():
        for label, (w, h) in {"landscape": (1200, 628), "story": (1080, 1920), "wide": (1500, 500)}.items():
            derived[f"{name}_{label}"] = resize_design(src, {"width": w, "height": h})
    bases.update(derived)
    return bases


def rename_ids(doc: Doc, rng: random.Random) -> None:
    ids, groups = {}, {}
    for e in doc["elements"]:
        ids[e["id"]] = "e" + "".join(rng.choices(string.ascii_lowercase + string.digits, k=6))
        e["id"] = ids[e["id"]]
        if e.get("group_id"):
            e["group_id"] = groups.setdefault(e["group_id"], "g" + "".join(rng.choices(string.ascii_lowercase, k=5)))
    for i, e in enumerate(doc["elements"]):  # keep z order but renumber densely
        e.setdefault("z_index", i)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=48)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(REPO_ROOT / "corpus"))
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    bases = load_bases()
    names = sorted(bases)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()

    for i in range(args.n):
        base = names[i % len(names)]
        doc = copy.deepcopy(bases[base])
        applied = []
        for m in rng.sample(MUTATIONS, rng.choice([1, 2, 2, 3])):
            note = m(doc, rng)
            if note != "skip":
                applied.append(note)
        rename_ids(doc, rng)
        fmt = rng.choice(CATALOGUE)
        task = {
            "task_id": f"corpus_{i:03d}",
            "difficulty": "synthetic",
            "notes": f"base {base}; " + ("; ".join(applied) or "unchanged"),
            "source": doc,
            "target_canvas": {"width": fmt.width, "height": fmt.height},
        }
        (out / f"corpus_{i:03d}.json").write_text(json.dumps(task, indent=2))
    print(f"wrote {args.n} tasks to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
