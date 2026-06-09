# AGENTS.md — Agent guide for `synthetic-images`

Batch enrichment pipeline that generates **synthetic, style-locked 2:3 illustrations** for
entities that lack a consistent card image, stores them on the web server, tracks them in the
DB, and exposes the path through the API so `tmdb-front` and `voice-agent` can render uniform
card grids. This is **Project A** of the "An image for everything" plan.

> Canonical design spec lives in the front-end repo:
> `tmdb-front/doc/An image for everything/` — read **`An image for everything - Project A review.md`**
> (the executable spec) and **`eval-plan.md`** (the model/prompt bake-off) before changing pipeline behavior.

## Ecosystem

Sibling repo of **Agent BBB** (`%USERPROFILE%/Code/<repo>`, `github.com/vaugouin/<repo>`), built like
the crawlers: a Python Docker container that reads/writes the shared MySQL/MariaDB `T_WC_*` database
via the **`citizenphil.py`** access layer (copied verbatim across repos — never change its signatures).
Upstream entity tables come from `tmdb-*`, `wikidata-crawler`, `wikipedia-crawler`; the generated
images are served by `tmdb-front`'s Apache and consumed by `tmdb-front` + `voice-agent`.

## Layout

- `synthetic-images.py` — CLI driver: single-item harness **and** batch-over-a-class.
- `synthetic_images_functions.py` — the two-stage pipeline (description → image), validation, storage, persistence.
- `citizenphil.py` — shared DB layer (verbatim copy; do not edit here).
- `01_create_schema.sql` — `T_WC_T2S_SYNTHETIC_IMAGE` + `T_WC_T2S_ENTITY_IMAGE` + `T_WC_T2S_REPRESENTATION`.
- `02_representation_seed.sql` — the controlled representation vocabulary per class.

## Keystone rules (do not break)

1. **Style-lock separation.** Style + aspect ratio + resolution live ONLY in `STYLE_TEMPLATE`
   (text-to-image). The per-item LLM output is a **pure object description** — never style words.
   This is what buys grid consistency (review §2).
2. **Identity = `ID_WIKIDATA`** (fall back to `(ITEM_CLASS, ID_ITEM)`); one image per Q-id serves
   every role it plays — dedup is automatic (review §4.2 / decision #14).
3. **Idempotency** keys on `(IMAGE_KEY, STYLE_VERSION)` — a style bump regenerates; otherwise skip.
4. **Provenance + seed are mandatory** on every image row (reproduce / A-B a style change).
5. **Batch-only v1.** No real-time generation (that's Project B).
6. **Generation language = English** (canonical). Images are language-neutral.

## Dev loop

Primary loop is the **single-item harness** — fast, offline, no API key:

```bash
python synthetic-images.py --class technical --item-id 5 --representation plate --dry-run --force
```

Batch a class (Phase 1 = Technicals): `python synthetic-images.py --class technical --dry-run`.
Guards: `--dry-run` (no API, no DB writes), `--limit N`, `RUN_BUDGET_USD` ceiling. Code conventions
follow the ecosystem: `f_…` public / `_…` private functions, Hungarian variable prefixes
(`str`/`lng`/`int`/`arr`/`dbl`), broad try/except with console logging, UTF-8 everywhere.

## Secrets

`.env` (DB + provider keys) stays OUTSIDE the image: `.dockerignore`d and `.gitignore`d, injected at
runtime via `--env-file`. See `.env.example`.
