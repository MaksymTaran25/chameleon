# Chameleon engine

Submission for Project Chameleon (Adobe University Hackathon 2026).

## What it does

Takes a source design (JSON) and a target canvas size, and returns the same
design laid out again for the new size. Elements are moved and resized, not
stretched or cropped. Every element id is kept exactly once, nothing is placed
off-canvas, non-background images keep their aspect ratio, and `content` and
`font_family` never change. Asset pixels are never altered. The engine is
deterministic, runs offline and uses no model.

## How it works

- **Units.** Elements that share a `group_id`, or a text sitting on a shape,
  move together as one rigid unit.
- **Visible rects.** Layout and collision checks use what is actually visible:
  the alpha bounds of each image, the measured text block and the rotated box.
- **Roles from geometry.** Each unit is classified (background, logo, hero,
  headline, subhead, cta, badge set, sticker, fine print, decoration and so
  on) from geometry and style. Ids are never read; the brand logo's file name
  is used only as a tiebreak.
- **Format classes.** The target's aspect ratio picks the layout families to
  try, from ultra-tall to ultra-wide.
- **Candidate layouts.** Stack, split and strip, in several variants, plus a
  letterbox fallback that is always valid.
- **Fitting.** One global scale per candidate, found by bisection, keeps the
  type hierarchy intact. Text is measured with the renderer's own wrap code
  and fonts, so "fits" means it fits in the PNG.
- **Selection.** Candidates must pass hard gates (ids, frozen fields, image
  aspect, raw and rotated boxes on canvas), then are ranked by a soft score
  (collisions, readability, contrast, hierarchy, alignment, balance). The
  weights were fitted to hand-judged pairs.

Design notes: `APPROACH.md`.

## Run locally

Python 3.10+ (the Docker image uses 3.11).

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m engine.main

Reads every task in `input/` and writes `<task>_output.json` and
`<task>_output.png` to `output/`.

## Run in Docker

    docker build --platform linux/amd64 -t chameleon-engine .
    docker run --rm --network none \
      -v $(pwd)/input:/app/input -v $(pwd)/output:/app/output \
      chameleon-engine

All dependencies install at build time; nothing is fetched at run time.

## Testing

Checked with the tools in `tools/`, run inside the Docker image with
`--network none`:

- 3 samples x 27 formats (26 real platform sizes + each task's own target):
  81/81 pass the hard gates.
- 48 corpus designs (mutated samples) x 27 formats: 1296/1296 pass.
- Scramble test (every id and asset name randomised): same results, 81/81.

## Assumptions where the rules are open

We asked the organizers about the rules below and had no answer by submission.
Where the rules leave room, the engine takes the strictest reading, so none of
these choices can disqualify it. At worst, a looser rule would have let it
score a little higher.

- **No element is hidden or removed.** Nothing is set to `opacity: 0`.
  When a small canvas cannot hold everything readably, the least
  important elements (badges, fine print, stickers) shrink and are placed in
  free space.
- **Nothing leaves the canvas.** Both the raw box and the box after rotation
  stay inside it. Decorations that bled off the edge of the source are pulled
  inside.
- **Overlaps are judged on visible pixels.** The transparent padding around
  an asset may overlap a neighbour's padding. What the viewer sees does not
  collide unless the source already layered it that way.
- **Content and type are fixed.** Text, font family, weight, style and colour
  are never changed. Only geometry, `font_size`, and `text_align` (when a
  layout moves text into a left-aligned column) are.
- **No model.** The approved-model list was not published, and the engine
  does not need one. It is deterministic and fast.
- **Brand kit as fixed material.** The logo's file name is used only as a
  tiebreak in role detection. The `--scramble` sweep shows the results are
  identical with every id and asset name randomised.
- **Missing assets are kept.** The element stays in the output, rendered as
  the renderer's placeholder, placed small in a free spot.

## Repo layout

    engine/resize.py     entry point: resize(source, target_canvas)
    engine/main.py       reads input/, runs resize, self-check, writes output/
    engine/chameleon/    parse, measure, roles, formats, layout, templates,
                         score, select
    renderer/            vendored converter + cr-renderer wrapper for PNGs
    brand_kit/           assets and fonts
    tools/               dev harness: sweep, corpus, pairs, fit_weights,
                         inspect_task, candidates (not in the image)
    corpus/, tune/       test designs and judged pairs for the weight fit
    input/, output/      sample tasks and results
    APPROACH.md          design notes
