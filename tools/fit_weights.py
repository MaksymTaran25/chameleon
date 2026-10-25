"""Fit the soft-score weights to judged pairs.

    python -m tools.fit_weights               # report current vs fitted, with leave-one-out
    python -m tools.fit_weights --samples 40000

Every pair in tune/pairs is rescored with the current scorer (the layouts
themselves are stored next to the renders), so adding a score part and
refitting needs no new labels.

The search is over weight vectors on the simplex. The objective is the
number of pairs where the higher score is the judged winner; among vectors
that tie on that, we take the one closest to the current weights, so the fit
only moves what the labels actually ask it to move. Leave-one-out accuracy
is the honest number: with 40 labels, in-sample agreement flatters.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from engine.chameleon.paths import REPO_ROOT
from engine.chameleon.score import WEIGHTS, score


def load(pairs_dir: Path, labels_path: Path) -> tuple[list[str], np.ndarray, np.ndarray]:
    pairs = json.loads((pairs_dir / "pairs.json").read_text())
    labels = json.loads(labels_path.read_text())["labels"]
    keys = list(WEIGHTS)
    diffs, ys = [], []
    for p in pairs:
        lab = labels.get(str(p["idx"]))
        if lab not in ("L", "R"):
            continue
        task = json.loads(Path(p["task_path"]).read_text())
        parts = []
        for side in ("L", "R"):
            doc = json.loads((pairs_dir / f"pair{p['idx']:03d}_{side}.json").read_text())
            s = score(task["source"], doc, doc["canvas"])
            parts.append([s.parts[k] for k in keys])
        diffs.append(np.array(parts[0]) - np.array(parts[1]))
        ys.append(1.0 if lab == "L" else -1.0)
    return keys, np.array(diffs), np.array(ys)


def agreement(w: np.ndarray, X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Correct pairs per weight vector. w: (m, k), X: (n, k), y: (n,)."""
    return ((X @ w.T) * y[:, None] > 0).sum(axis=0)


def fit(X: np.ndarray, y: np.ndarray, w0: np.ndarray, samples: int, rng: np.random.Generator) -> np.ndarray:
    # Dirichlet samples around the current weights plus uniform ones, and w0
    # itself, so "change nothing" is always a candidate.
    near = rng.dirichlet(w0 * 60 + 0.3, size=samples // 2)
    wide = rng.dirichlet(np.ones(len(w0)), size=samples // 2)
    cand = np.vstack([w0[None, :], near, wide])
    acc = agreement(cand, X, y)
    best = cand[acc == acc.max()]
    return best[np.argmin(np.abs(best - w0).sum(axis=1))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=str(REPO_ROOT / "tune" / "pairs"))
    ap.add_argument("--labels", default=str(REPO_ROOT / "tune" / "labels.json"))
    ap.add_argument("--samples", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    keys, X, y = load(Path(args.pairs), Path(args.labels))
    w0 = np.array([WEIGHTS[k] for k in keys])
    w0 = w0 / w0.sum()
    rng = np.random.default_rng(args.seed)
    n = len(y)

    base = int(agreement(w0[None, :], X, y)[0])
    w = fit(X, y, w0, args.samples, rng)
    fitted = int(agreement(w[None, :], X, y)[0])

    loo = 0
    for i in range(n):
        keep = np.arange(n) != i
        wi = fit(X[keep], y[keep], w0, args.samples, rng)
        loo += int((X[i] @ wi) * y[i] > 0)

    print(f"pairs: {n}")
    print(f"current weights: {base}/{n} agree")
    print(f"fitted weights:  {fitted}/{n} in-sample, {loo}/{n} leave-one-out")
    print(f"{'part':<12} {'current':>8} {'fitted':>8}")
    for k, a, b in zip(keys, w0, w):
        print(f"{k:<12} {a:>8.3f} {b:>8.3f}")
    wrong = [i for i in range(n) if (X[i] @ w) * y[i] <= 0]
    print("still disagree (row index):", wrong)
    return 0


if __name__ == "__main__":
    sys.exit(main())
