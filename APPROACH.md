# Chameleon engine - approach draft

Status: draft, no code yet. Grounded in the 3 samples, the v0.9 schema, RULES.md
and the converter in `renderer/converter/`.

## Core idea

Classify, then compose, then check. We never transform the source geometry.
We work out what each element *is*, pick a layout family that suits the target
shape, solve sizes and positions inside it, and score several candidates
against a local copy of the rubric. The best one wins.

The pipeline is deterministic and has no model in the critical path. A local
vision model is an optional last-step reranker, so the engine still works if
the approved-model list turns out to be useless.

```
source + target
  -> 1 parse      (units, visual bounds, rotated boxes, missing assets)
  -> 2 roles      (what each unit is + how important it is)
  -> 3 format     (aspect class + absolute scale of the target)
  -> 4 templates  (2-6 candidate layouts for that class)
  -> 5 fit        (text sizing, image scaling, spacing, snapping)
  -> 6 score      (hard checks + rubric-shaped soft score)
  -> best candidate (optionally reranked by rendering top-K)
```

## 1. Parse and normalize

- **Units.** A unit is a set of elements that move as one rigid body.
  - Elements that share a `group_id` form a unit.
  - So does a text element whose box sits fully inside a shape box with a
    higher z. This catches buttons, badges and ribbons that come without a
    `group_id`.
  - Everything else is a unit of one.
- **Visual bounds.** We read each asset's alpha bbox from `brand_kit/assets`.
  This only reads pixels and never changes them. The padding is large: the
  bottle's visible part is 341x551 inside 800x800, and the logo's is 364x216
  inside 900x300. Layout, alignment and collision checks all use the
  *visible* rect. If we used the box, the bottle would look tiny and
  everything near it would look misaligned.
- **Rotated elements.** For a rotated element we compute the axis-aligned box
  of the rotated rect. That box is what we use for bounds and collisions; the
  schema's x/y/w/h is the unrotated box, turned about its centre.
- **Missing assets.** We check whether `asset_id` resolves, the same way the
  renderer does. A missing asset renders as a grey placeholder, so we mark it
  lowest priority and keep it small and out of the way. The engine must never
  crash on it.
- **Text measurement.** We reuse `renderer/converter/wrap.py`: `char_widths`
  plus `wrap_text`, with the same FontManager and skia path the renderer uses.
  That way our "fits / doesn't fit" decision is exactly what renders.
  - Lines = count of `\n` after wrapping.
  - Height = lines x size x line_height.
  - Verified: cr-renderer draws text from the top of its box and never clips
    it, so a box that is too short makes text spill over whatever is below.
    It renders at int(font_size) while the converter wraps at the float size,
    so the engine emits whole-number sizes only.

## 2. Role inference (no semantic labels)

We score each unit on features and give it a role plus an importance weight.
Ids and asset names are a weak tiebreaker at most, and we will test with them
scrambled.

| Role | Signals |
|---|---|
| background | image, `fit: cover`, covers 90% or more of the canvas, lowest z |
| decoration | shape with opacity of 0.3 or less, or shape with no text partner; ellipses common |
| logo | `contain` image, small-medium, wide visible aspect, near top or a corner, high z |
| hero | largest non-background image by visible area |
| headline | text with the largest font_size (x weight), bold, short-to-medium length |
| subhead / body | next text tier, regular weight, longer, often lower alpha |
| cta | shape + short bold text unit, saturated fill, pill/rounded, lower half |
| badge set | 2 or more units with near-equal height and style, aligned in a row or column. Treated as one flowable set, whatever the count |
| sticker / ribbon | rotated unit, or one that bleeds off a corner |
| fine print | smallest font, low alpha, long, hugging the bottom edge |
| misc image | small image near a corner (watermark), or the missing asset |

- **Brand kit priors.** The brief says the brand kit is a "fixed material" we
  may rely on. If the hidden set reuses the Solstice kit, knowing that
  `solstice_logo_lockup.png` is the logo is a legal prior, not hand-tuning to
  one case. We use it to break ties and never depend on it.
- **Importance order.** When space is tight, it sets the shrink and stacking
  priority: headline ~ logo > cta > hero > subhead > badges > sticker >
  fine print > misc > decoration.

## 3. Format classification

The aspect ratio r = W/H picks the layout family. The short side in pixels
sets the type floors: 728x90 and 1600x400 are both ultra-wide, but 90 px
leaves almost no room.

| Class | r | Examples from the platform catalogue |
|---|---|---|
| ultra-tall | < 0.6 | Story 1080x1920, skyscraper 160x600, 300x600 |
| tall | 0.6-0.9 | 4:5 1080x1350, Pinterest 1000x1500 |
| square | 0.9-1.15 | IG post 1080x1080, 300x250 |
| wide | 1.15-2.5 | YouTube thumb 1280x720, 1200x628 link ad |
| very wide | 2.5-5 | Twitter header 1500x500, 1600x400, LinkedIn 1584x396, 970x250 |
| ultra-wide | > 5 | Leaderboard 728x90, mobile 320x50, 468x60 |

We also build a local target catalogue of about 25 real formats and use it as
the test sweep.

## 4. Templates (parametric, not fixed coordinates)

A template is an ordered arrangement of role *slots*, with flexible weights.
Coordinates come out of the fitter. Each class enables a few templates. We
generate every enabled template, plus variants (mirrored, hero side, text
alignment), and let the scorer choose.

- **Stack**, for tall, square and ultra-tall: logo, hero, headline, subhead,
  badge row, cta and fine print, top to bottom, centred. The ribbon goes to
  the top-right corner.
- **Split**, for wide: the hero takes one column and is placed on the side it
  sat on in the source, defaulting to right. The text column holds logo,
  headline, subhead, cta and badges, left-aligned. The ribbon pins to the
  far corner.
- **Band**, for very wide (the poster in the brief's reference sheet): the
  same two columns as Split, but with a tighter text column. The badges
  become a single row and the subhead can share the headline column.
- **Strip**, for ultra-wide: one row, vertically centred. From left to right:
  logo, hero (visible-cropped small), headline (1-2 lines), cta. Everything
  else shrinks to its floor and tucks into leftover gaps, or sits behind as
  decoration.
- **Overlay**, an option for any class when the background is a photo: the
  background is full-bleed and the text goes in its calmest region. The
  "calm" test reads luminance variance of the background crop, so text never
  sits on a busy image.

**Constants in every template:**
- The background is always exactly the canvas box. It is `cover`, so cropping
  is expected, and it can't extend past the canvas edge.
- Decorations are re-scattered by their relative positions and scaled
  uniformly, so circles stay circles. They are clamped inside the canvas,
  because bleeding off the edge fails the off-canvas check, and kept behind
  the content. Overlaps they had in the source may stay.
- z-order is preserved unless a collision fix needs a change.

## 5. Fitting

- **Space allocation.** Slots share the main axis by importance weight, with
  minimum sizes. Margins and gaps come from the short side, roughly 4-6%
  margin and 2-3% gap, snapped to whole pixels with equal gaps.
- **Text.** We binary-search `font_size` per text element to the largest
  size whose wrapped block fits the slot.
  - The size is capped at the source size x the global scale x 1.25, so the
    type never balloons.
  - **Readability floor:** a minimum px tied to the short side, with an
    absolute floor (~11 px for fine print, ~14 px for body). The values are
    to be tuned.
  - **Hierarchy constraint:** the source order of font sizes must survive
    (headline > subhead > cta > fine print). We solve the headline first and
    cap the others below it.
  - The box height is set to the measured block height, so boxes are tight
    and collision checks stay honest.
- **Images.** Images scale uniformly with the source box aspect locked, well
  inside the ~5% rule. They are positioned by visible bounds, so the
  transparent padding can overlap neighbours while the visible pixels don't.
- **Units.** A unit scales as a rigid body. For pills we keep
  `corner_radius` at or below h/2 and scale it along with the unit. A button
  shape can widen to give its label horizontal room, because shapes don't
  have the image aspect rule. The label padding ratio is kept.
- **Badge sets.** A badge set flows as a row, wrapping to a 2-row grid if
  needed, with equal widths per row and centred. Any badge count works.
- **Rotation.** The ribbon keeps its -12 degrees. Placement uses the rotated
  box, then x/y is solved back so the *unrotated* box is on-canvas too,
  because we don't know which one the scorer checks.

## 6. Internal scorer (our copy of the rubric)

The scorer has two layers: hard gates, then a soft score.

**Hard gates.** Any failure drops the candidate:
- same id set, required fields present, `font_size` of 1 or more
- `font_family` and `content` unchanged
- raw boxes and rotated boxes both inside the canvas
- non-cover images within 1% of the source aspect ratio

**Soft score:**
- **Collisions.** We sum the overlap area of visible rects across units.
  Pairs that are exempt: the background, decorations, members of the same
  unit, and pairs that overlapped in the source with a similar relationship.
  Text overlapping anything not in its own unit is penalised hardest.
- **Readability.** We check font px against the floors. For contrast, we
  composite the effective colour under each text box (background pixels plus
  any shapes beneath) and compute a WCAG ratio, aiming for 4.5, with 3 the
  minimum for large text. This catches the dark-on-dark
  "AWARD WINNING 2026" badge in L3.
- **Hierarchy.** We compare font sizes and visible areas against the source,
  using rank correlation.
- **Faithfulness.** Reading order and rough anchors should be preserved:
  logo stays near the top or left, cta after the headline.
- **Intentional look.** We reward shared alignment edges, equal gaps and
  balanced whitespace. We penalise canvas under-use and content stuck in
  one region.
- **Speed.** It's a minor rubric row, so the default is to render only when
  reranking.

**Optional reranker.** We render the top 3 candidates through the real
renderer (about 2-5 s each) and ask a small local VLM from the approved list
which looks most designed. This is only worth it if the size limit allows a
decent model, and only behind a flag.

## 7. Test harness

We only have 3 samples, so we build our own test corpus:
- **Target sweep:** each sample against the ~25-format catalogue gives about
  75 renders, laid out on a contact sheet per run.
- **Id scramble:** run every test with random ids and asset_ids where
  possible, so nothing leans on names.
- **Synthetic designs:**
  - Variants of the samples: drop or add badges, longer and shorter copy,
    moved elements, and a different missing asset.
  - Conversions of Crello designs into our schema (the reverse of their
    converter). Since scoring uses cr-renderer, the hidden set may be
    Crello-like, but that is a guess.
- **Regression:** record the hard-gate pass rate and the mean soft score per
  run, and keep golden contact sheets.

## 8. Code layout (planned)

`engine/resize.py` stays a thin entry point. The logic lives in
`engine/chameleon/`, which the Dockerfile already copies:

```
parse.py      units, visual bounds, rotated AABB, asset resolution
measure.py    text measurement via renderer/converter/wrap.py
roles.py      features + role/importance assignment
formats.py    aspect classes + target catalogue
templates/    stack.py split.py band.py strip.py overlay.py
fit.py        allocation, text search, unit scaling, snapping
score.py      hard gates + soft rubric
select.py     candidate generation + selection (+ optional rerank)
tools/        harness: sweep, scramble, contact sheets (not shipped)
```

## 9. Milestones

Status: 1-4 done. Sweep (3 samples x 26 formats + own targets): 81/81 pass
the hard gates. Corpus (`python -m tools.corpus`, 48 seeded mutations of the
samples, x 27 targets): 1296/1296 pass, no crashes. Soft-score weights are
fitted to 40 judged pairs (`tools/pairs.py`, `tune/labels.json`,
`tools/fit_weights.py`): agreement 32/40 with the hand-set weights, 37/40
leave-one-out after. One judge (me) and 40 pairs is a thin signal; more labels,
ideally from someone else, is the cheapest way to make the fit trustworthy.
The remaining weak cases are small crowded targets, which wait on question 1.

1. Parse, measure, roles, score and harness: the scorer runs against the
   baseline output.
2. Stack and Split plus fitting: L1 clean, and L2 readable at 1600x400.
3. Strip, Band and badge sets: L3 at 728x90 with a legible headline, logo
   and cta.
4. Candidate search, the scramble test and the synthetic corpus, then tune
   the weights.
5. Docker build with `--network none`, the README, and the optional VLM
   rerank if the limits allow.

## Open questions to ask organizers

1. **Can elements be set to `opacity: 0`, or shrunk to a token size,** when
   they can't fit (for example, three badges plus legal at 728x90)? The
   finale talks about letting elements "drop away", but stage one says every
   id must be present. This is the biggest open choice for ultra-wide.
2. **Which box does the off-canvas check use** for rotated elements, the raw
   one or the rotated one? And can decorations that bleed off the edge in the
   source keep bleeding?
3. **Does the structural check flag box overlaps,** or only the rendered
   PNG? Boxes with transparent padding overlap even when the visible pixels
   don't.
4. **Do hidden designs reuse the Solstice brand kit,** and are ids and
   asset_ids meaningful there?
5. **Model list, size cap, and the RAM/CPU spec of the M5 Pro.**
