# synthetic-images — Synthetic entity illustrations (Project A)

Generate **consistent, style-locked 2:3 card illustrations** for database entities that lack a
usable image (or whose Wikipedia images vary wildly in style and aspect ratio, breaking card grids).
Part of the **Agent BBB** multi-repo system; the design spec is
`%USERPROFILE%/Nestor/projets/t2s-backlog/topics/an-image-for-everything/an-image-for-everything-project-a-review.md`.

For every in-scope entity, a two-stage pipeline produces one synthetic illustration:

```
entity ─▶ resolve source text (Wikipedia / overview ─▶ web-search fallback)
       ─▶ text-to-text  : pure OBJECT DESCRIPTION (no style words)
       ─▶ text-to-image : locked Corbeil "visual dictionary" template, 2:3 WebP
       ─▶ validate (aspect ratio / integrity) ─▶ store ─▶ record provenance + mapping
```

The result is stored as a WebP master under `shared_data/synthetic-images/<class>/<hash>.webp`,
served by `tmdb-front`'s Apache and surfaced through the FastAPI API for the front-ends.

## Why it exists

Card grids in `tmdb-front` / `voice-agent` need **uniform** imagery. Wikipedia photos don't deliver
that (mixed styles, ratios; many entities have none at all — e.g. characters, fictional places,
causes of death). A single locked illustration style across the whole corpus fixes the grids.

## Quick start

```bash
cp .env.example .env          # fill DB creds + provider keys (keep OUTSIDE git)
mysql … < 01_create_schema.sql
mysql … < 02_representation_seed.sql

# build the image once (re-run after code changes):
docker build -t synthetic-images-python-app .

# offline smoke test (no API key, no DB writes):
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./synthetic-images.py --class technical --item-id 5 --representation plate --dry-run --force

# batch the Phase-1 class:
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./synthetic-images.py --class technical --dry-run        # preview
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./synthetic-images.py --class technical --limit 10       # real run, capped
```

## Logo padding — companies / networks (zero model cost)

Companies and networks are **not synthesized** (decision #7 — synthesizing a brand's logo would
misrepresent a real trademark). Instead the pipeline **pads the real TMDb logo onto the 2:3
off-white canvas** with Pillow: flattened against the canvas, scaled into a safe box
(≤80% width × ≤40% height, upscale capped at 2×), centered, stored as the same WebP master with
the same idempotency / validation / provenance as synthetic images (`T2I_MODEL='pillow-logo-pad'`,
`SOURCE='tmdb-logo'`, `SOURCE_URL` = the CDN URL, `GENERATION_COST=0`).

Because `logo-pad` is the **only** representation for these classes, class selection *is* cost
selection — these runs make **no LLM or image-model calls** (the only cost is hosting the files):

```bash
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./synthetic-images.py --class company        # ~19k logos
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./synthetic-images.py --class network       # ~2.5k logos
```

`--limit`, `--dry-run`, `--force` and idempotent resume work as for any class. Scope: only rows
**with** a `LOGO_PATH` (the ~156k logo-less companies are a separate threshold/skip decision).
`.svg` logos are fetched as the CDN's rasterized PNG rendition at `LOGO_RASTER_SIZE`.

## Data model (`01_create_schema.sql`)

| Table | Role |
|---|---|
| `T_WC_T2S_SYNTHETIC_IMAGE` | one row per generated image (artifact + full provenance + lifecycle). |
| `T_WC_T2S_ENTITY_IMAGE` | entity-occurrence → image mapping; makes the Wikidata dedup clean. |
| `T_WC_T2S_REPRESENTATION` | controlled representation vocabulary per class (flag / map / plate / …). |

## Configuration

All via `.env` (see `.env.example`): DB connection, the text-to-image provider
(`REPLICATE_API_TOKEN`, `T2I_MODEL` — bake-off front-runner **FLUX.1 schnell**), a text-to-text
provider key, the Tavily fallback key, storage paths, the provenance versions
(`STYLE_VERSION` / `*_PROMPT_VERSION`), and the run guards (`RUN_BUDGET_USD`, `USAGE_THRESHOLD`).

## Evaluation — model/prompt bake-off

Before mass production, validate the description template, the style template, and the model
choices on a small fixed sample (`doc/eval-plan.md`). `eval_bakeoff.py` sweeps the **golden set**
(`golden_set.py`, ~35 items spanning every illustration mode) over candidate text-to-text and
text-to-image models and writes an **isolated, reproducible run tree** — no DB writes, no
shared-store writes:

```
EVAL_DIR/<run_id>/
  descriptions.csv                 stage-1: per (item × t2t model) + blank human-score columns
  scores.csv                       stage-2: per render (aspect, size, cost, latency) + blank scores
  images/<t2t>__<t2i>/<item>.webp  the renders
  contact_sheet__<t2t>__<t2i>.html the grid to eyeball (the decisive consistency artifact)
  index.html  run.json
```

```bash
# Cheap stage-1 pass — descriptions only (Haiku vs Sonnet), no image cost:
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./eval_bakeoff.py --skip-images

# Full sweep on a subset, then open EVAL_DIR/<run_id>/index.html:
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./eval_bakeoff.py --limit 8

# Offline plumbing test (stubs, no keys):
… python ./eval_bakeoff.py --dry-run --limit 3
```

`EVAL_DIR` defaults to `/shared/eval` (→ `shared_data/synthetic-images/eval/` on the host).
Guards: `--dry-run`, `--limit N`, `--budget` (USD ceiling), `--t2t-models` / `--t2i-models`
(comma-separated). Today the wired candidates are Anthropic Haiku + Sonnet (t2t) and FLUX.1
schnell (t2i); add OpenAI/Gemini (t2t) and Nano Banana / GPT Image (t2i) as those providers are
wired. The CSVs carry blank human-score columns to fill while reviewing the contact sheets.

## Failure handling

Built for an unattended daily run: when re-run nightly, new entities get a card automatically while
existing ones are skipped (idempotent on `(IMAGE_KEY, STYLE_VERSION)`, skipping only **successful**
rows so failures self-heal). Failures are **categorized, not blindly retried** — only transient API
errors auto-retry; the rest land in a manual review queue with a per-category suggested fix and
**bulk-requeue-by-category** for pattern fixes. See review §6.1 and `AGENTS.md`.

## Status

Scaffold + Phase-1 (Technicals) vertical slice + the **logo-padding path for companies/networks**
(deterministic, zero model cost — see "Logo padding" above). **Live model calls are wired**: text-to-text via
the Anthropic SDK (`claude-*`; OpenAI/Gemini branches added during the `doc/eval-plan.md` bake-off)
and text-to-image via Replicate (FLUX.1 schnell). `--dry-run` still exercises the full loop offline
with no API key. Failure categorization + the review queue (review §6.1) are **designed, not yet
implemented** — build order: schema fields + classifier → transient auto-retry → CLI
`--report-failures` → bulk-requeue → web dashboard. See `AGENTS.md` for the keystone rules and phasing.

## License

MIT © Philippe Vaugouin.
