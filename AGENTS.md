# AGENTS.md — Agent guide for `synthetic-images`

Batch enrichment pipeline that generates **synthetic, style-locked 2:3 illustrations** for
entities that lack a consistent card image, stores them on the web server, tracks them in the
DB, and exposes the path through the API so `tmdb-front` and `voice-agent` can render uniform
card grids. This is **Project A** of the "An image for everything" plan.

> Canonical design spec lives in the front-end repo:
> `%USERPROFILE%/Nestor/projets/t2s-backlog/topics/an-image-for-everything/` — read **`an-image-for-everything-project-a-review.md`**
> (the executable spec) and **`eval-plan.md`** (the model/prompt bake-off) before changing pipeline behavior.

## Ecosystem

Sibling repo of **Agent BBB** (`%USERPROFILE%/Code/<repo>`, `github.com/vaugouin/<repo>`), built like
the crawlers: a Python Docker container that reads/writes the shared MySQL/MariaDB `T_WC_*` database
via the **`citizenphil.py`** access layer (copied verbatim across repos — never change its signatures).
Upstream entity tables come from `tmdb-*`, `wikidata-crawler`, `wikipedia-crawler`; the generated
images are served by `tmdb-front`'s Apache and consumed by `tmdb-front` + `voice-agent`.

## Layout

- `synthetic-images.py` — CLI driver: single-item harness **and** batch-over-a-class. Dispatch is
  by representation: `logo-pad` (the company/network default) routes to the deterministic padding
  path; everything else goes through the two-stage synthetic pipeline.
- `synthetic_images_functions.py` — the two-stage pipeline (description → image), validation, storage, persistence; plus **Stage L** (`f_pad_logo_image`), the zero-cost Pillow path that pads the real TMDb company/network logo onto the 2:3 canvas (decision #7) and reuses the same validation/storage/persistence.
- `synthetic_images_lab.py` + `lab/`: **the lab** (SYNTHETIC-IMAGES-020): FastAPI service and its page (vanilla JS, French UI) to preview prompts and cost, describe, render N candidates with any model, and choose the served image. Behind NGINX at `/synthetic-review/` (prefix stripped, page uses relative URLs). Run by `synthetic-images-lab.sh` (container `synthetic-images-lab`, host networking, listens on `172.17.0.1:8195` only). It calls the same pipeline functions as the CLI; never re-implement pipeline logic in the page.
- `eval_bakeoff.py` — model/prompt bake-off harness (eval-plan §6): sweeps the golden set × t2t/t2i models, writes an isolated `EVAL_DIR/<run_id>/` tree (descriptions.csv, scores.csv, contact-sheet HTML, run.json). **No DB / no shared-store writes** — reuses the pipeline stage functions only.
- `golden_set.py` — the fixed ~35-item evaluation set (eval-plan §1); keep it stable once chosen.
- `citizenphil.py` — shared DB layer (verbatim copy; do not edit here).
- `01_create_schema.sql` — `T_WC_T2S_SYNTHETIC_IMAGE` + `T_WC_T2S_ENTITY_IMAGE` + `T_WC_T2S_REPRESENTATION`.
- `02_representation_seed.sql` — the controlled representation vocabulary per class.
- `03_candidates_migration.sql`: candidates, `T_WC_T2S_SYNTHETIC_DESCRIPTION`, `IS_MANUAL_CHOICE` (idempotent; **run before deploying code from 2026-10-08 on**, the pipeline writes these columns).

## Keystone rules (do not break)

1. **Style-lock separation.** Style + aspect ratio + resolution live ONLY in `STYLE_TEMPLATE`
   (text-to-image). The per-item LLM output is a **pure object description** — never style words.
   This is what buys grid consistency (review §2).
2. **Identity = `ID_WIKIDATA`** (fall back to `(ITEM_CLASS, ID_ITEM)`); one image per Q-id serves
   every role it plays — dedup is automatic (review §4.2 / decision #14).
3. **Idempotency** keys on `(IMAGE_KEY, STYLE_VERSION)` — a style bump regenerates; otherwise skip.
   An entity has **candidates** 1..N (`CANDIDATES`, default 3): one description, N renders.
   Candidate 1 keeps the historical key, candidates 2..N append `|c<N>`. The lab appends new
   indexes and never overwrites a candidate.
4. **Provenance + seed are mandatory** on every image row (reproduce / A-B a style change).
5. **No real-time generation for end users** (that's Project B). The lab is an operator tool behind authentication, not a user-facing path.
6. **Generation language = English** (canonical). Images are language-neutral.
7. **Companies/networks are never synthesized** — pad the real TMDb logo (decision #7;
   synthesizing a brand's logo misrepresents a trademark). `--class company` / `--class network`
   runs are zero-model-cost by construction: `logo-pad` is their only representation.
8. **A manual choice is final.** `f_set_entity_choice()` is the only writer of
   `T_WC_T2S_ENTITY_IMAGE`: automatic calls claim the slot only when the entity has none, or for
   candidate 1 when the current choice is not manual. Never write that table with
   `f_sqlbulkupsert`: `ID_WIKIDATA` is NULL for genres and the like, a NULL defeats the UNIQUE key,
   and an upsert inserts a duplicate.
9. **Costs are stored where they are incurred.** A render's cost is `GENERATION_COST` on its image
   row; a description's cost is `T2T_COST` on its description row, once. Prices live in
   `T2I_COST` / `GEMINI_COST` / `OPENAI_COST` / `T2T_PRICE` (survey of 2026-10-08); refresh them
   rather than guessing.

## Failure handling (daily-schedule end state)

Failures are **categorized, not blindly retried** (review §6.1). A rule-based classifier tags each
failure with a `FAILURE_CATEGORY`; only `transient_api` auto-retries (bounded + backoff). Every other
category (`safety_refusal`, `no_source`, `bad_description`, `wrong_entity`, `offspec_ratio`,
`garbled_text`, `low_quality`) is recorded `STATUS='failed'` and goes to a **manual review queue**
(CLI `--report-failures`, later `back/synthetic-failures.php`) with a per-category suggested fix.
Fix **patterns, not items**: bulk-requeue a whole category after a template/version fix. The
idempotency probe skips only `STATUS='generated'`, so failed items stay eligible and self-heal once
reviewed — never skipped forever. Review-loop columns: `FAILURE_CATEGORY` / `ATTEMPT_COUNT` /
`REVIEW_STATUS` / `RESOLUTION_ACTION` / `REVIEW_NOTES`.

## Dev loop

Primary loop is the **single-item harness** — fast, offline, no API key. Build the image once
(`docker build -t synthetic-images-python-app .`), then run the CLI inside the container:

```bash
docker run --rm --network="host" --env-file .env -v $HOME/docker/shared_data/synthetic-images:/shared \
  synthetic-images-python-app python ./synthetic-images.py --class technical --item-id 5 --representation plate --dry-run --force
```

Batch a class (Phase 1 = Technicals): same `docker run …` wrapper with
`python ./synthetic-images.py --class technical --dry-run`.
Guards: `--dry-run` (no API, no DB writes), `--limit N`, `RUN_BUDGET_USD` ceiling. Code conventions
follow the ecosystem: `f_…` public / `_…` private functions, Hungarian variable prefixes
(`str`/`lng`/`int`/`arr`/`dbl`), broad try/except with console logging, UTF-8 everywhere.

## Secrets

`.env` (DB + provider keys) stays OUTSIDE the image: `.dockerignore`d and `.gitignore`d, injected at
runtime via `--env-file`. See `.env.example`.

## Backlog (Nestor second-brain)

The prioritized, agent-ready implementation backlog for this repo lives in the **Nestor**
knowledge repo (a separate repo, not cloned alongside this one):

- This repo: `C:\Users\vaugo\Nestor\projets\t2s-backlog\repos\synthetic-images.md`
- Cross-repo dashboard: `C:\Users\vaugo\Nestor\projets\t2s-backlog\index.md`

Consult it before implementing: tasks are `SYNTHETIC-IMAGES-NNN` with status (done / in-progress /
todo), priority, and quick-wins. NOTE: these are local paths on Philippe's PC and do not
resolve on the VPS or on cloud agents (claude.ai/code).
