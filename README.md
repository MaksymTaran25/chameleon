# Chameleon engine

A resize engine for the Adobe Chameleon challenge. Give it a finished design and
a target canvas, and it lays the same elements out again for the new shape:
moving the furniture, not stretching the photo.

It is deterministic, runs offline, uses no model, and never touches an asset's
pixels. It only changes positions, sizes and font sizes (and `text_align`
when a layout moves text into a left-aligned column).

## Run it

Locally (Python 3.11):

```bash
pip install -r requirements.txt
python -m engine.main
```

This reads every task in `input/` and writes `<task>_output.json` and
`<task>_output.png` to `output/`.

In Docker, the way stage one is scored:

```bash
docker build --platform linux/amd64 -t chameleon-engine .
docker run --rm --network none \
  -v "$(pwd)/input:/app/input" -v "$(pwd)/output:/app/output" \
  chameleon-engine
```

The image builds and runs with `--network none` and has been checked against
the local run: same hard-gate results and the same scores, to within 0.001.

## How it works

The engine works in three steps: classify the elements, compose layouts, then
pick the best. The full design notes are in `APPROACH.md`.

1. **Parse** (`engine/chameleon/parse.py`)
   - **Visible rects.** Every element gets the rect a viewer actually sees,
     not its box. The brand-kit PNGs carry heavy transparent padding (the
     bottle is 341x551 visible inside 800x800), text fills only part of its
     box, and rotation moves corners.
   - **Units.** Elements that move together become one unit. That is any
     shared `group_id`, plus a text sitting on a solid shape drawn under it.

2. **Roles** (`roles.py`)
   - **Geometry only.** Each unit gets a role from geometry alone: ids and
     file names are never read. The roles are background, decoration, logo,
     hero, headline, subhead, call to action, badge set, sticker, fine print
     and misc image.
   - **Priority.** Importance per role sets what shrinks first when space
     runs out.

3. **Layouts** (`layout.py`, `templates.py`)
   - **One global scale.** Every candidate sizes everything from a single
     global scale, so the type hierarchy survives. A bisection finds the
     largest scale that fits.
   - **Measurement.** Text is measured with the renderer's own wrap code and
     fonts, so "fits" means it fits in the PNG.
   - **The families:**
     - **stack:** the source's reading rows, top to bottom. Spare height
       follows the source's own spacing.
     - **split:** the hero in one column, everything else stacked in the
       other. Both sides, two widths and two alignments are tried.
     - **strip:** one row (logo, hero, text column, button) for banners.
       Anything without a slot is tucked into free space, nearest to where
       it sat in the source.
     - **letterbox:** the whole design scaled as one piece. It is a safe
       fallback that is always valid.
   - **Always on.** The background fills the canvas, decorations keep their
     place and stay circular, and every raw and rotated box is kept on the
     canvas.

4. **Select** (`score.py`, `select.py`)
   - **Hard gates first.** Every candidate is checked against hard gates
     that mirror `RULES.md`:
     - same ids
     - required and frozen fields unchanged
     - image proportions kept
     - nothing off canvas, checking both the raw and the rotated box
   - **Soft score.** Candidates that pass are ranked by a soft score built
     like the rubric:
     - no unwanted collisions, measured on visible pixels
     - readable sizes
     - contrast against what is actually under the text
     - hierarchy and reading order kept
     - canvas fill and balance, and alignment
     - text fitting its box, and content keeping its share of the canvas
   - **Fitted weights.** The weights are fitted to judged pairs of
     candidates (see Testing below).
   - **Never crashes.** A template that throws is skipped, so `resize()`
     always returns something valid.

## Testing

- **`python -m tools.sweep`** runs every task against 26 real platform
  formats, from 728x90 to A4. It writes contact sheets, `results.csv` and a
  summary to `runs/`. `--scramble` renames every id and asset to prove
  nothing leans on names.
- **`python -m tools.corpus`** builds 48 seeded variations of the samples in
  `corpus/`:
  - copy length and badge count
  - dropped roles and left-anchored layouts
  - rotation, missing assets and low contrast
  - landscape and tall sources
- **`python -m tools.pairs`** and **`tools.fit_weights`** handle weight
  fitting. The first renders competing candidates side by side for a human to
  judge; `tune/labels.json` holds 40 judged pairs. The second fits the
  soft-score weights to those labels, with leave-one-out validation.
- **`python -m tools.inspect_task <task>`** and **`tools.candidates <task> W H`**
  are for debugging: the roles and visible rects drawn over the render, and
  every candidate's score breakdown.

Current numbers:

| Set | Cases | Pass hard gates | Mean soft score |
|---|---|---|---|
| 3 samples x 27 targets | 81 | 81 | 0.92 |
| 48-task corpus x 27 targets | 1296 | 1296 | 0.92 |

Weight fit: the hand-set weights agreed with 32 of the 40 judged pairs. The
fitted weights agree with 37 of 40 leave-one-out.

The engine takes about 0.17 s per resize on average, 1 s at most, natively on
an M-series Mac.

## Known limits

- **Small, crowded targets.** L3 at 300x250 or 320x50 cannot hold every
  element readably. The low-priority ones (badges, fine print) end up small
  but clear of everything else. Whether the engine may hide such elements is
  an open question to the organizers.
- **Cross-platform measurement.** Glyph widths from the same skia build
  differ slightly between macOS and Linux, so outputs can differ by a few
  pixels between a local run and the container. Each environment is
  self-consistent, because text is measured with the same skia that renders
  it.
- **Thin labels.** The weight labels come from one judge, 40 pairs. More
  labels from other judges would make the fit more trustworthy.

## Layout of the repo

```
engine/resize.py        entry point: resize(source, target_canvas)
engine/chameleon/       parse, measure, roles, formats, layout, templates, score, select
tools/                  sweep, corpus, pairs, fit_weights, inspect_task, candidates
corpus/                 generated test designs
tune/                   judged pairs and labels for the weight fit
APPROACH.md             design notes and open questions
STARTER_README.md       the organizers' original starter instructions
```
