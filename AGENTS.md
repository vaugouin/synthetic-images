# AGENTS.md — Agent guide for `synthetic-images`

Agent rules only. What the pipeline does, the lab, logo padding, data model,
configuration, bake-off and run commands: @README.md (canonical).

> Canonical design spec: `%USERPROFILE%/Nestor/projets/t2s-backlog/topics/an-image-for-everything/`.
> Read **`an-image-for-everything-project-a-review.md`** (the executable spec) and
> **`eval-plan.md`** (the bake-off) before changing pipeline behavior.

## Ecosystem and layout

Agent BBB sibling (`%USERPROFILE%/Code/<repo>`, `github.com/vaugouin/<repo>`), built like the
crawlers: a Python Docker container on the shared `T_WC_*` database through **`citizenphil.py`**,
a verbatim copy shared across repos: do not edit it here, never change its signatures.

- `synthetic-images.py` (CLI) dispatches by representation: `logo-pad` goes to the Pillow padding
  path (`f_pad_logo_image`), everything else to the two-stage pipeline in
  `synthetic_images_functions.py`.
- The lab (`synthetic_images_lab.py` + `lab/`, French UI) calls the same pipeline functions as the
  CLI; never re-implement pipeline logic in the page.
- `eval_bakeoff.py` never writes the DB or the shared store; `golden_set.py` stays stable once chosen.
- `03_candidates_migration.sql` must be applied before deploying code from 2026-10-08 on (the
  pipeline writes its columns).

## Keystone rules (do not break)

1. **Style-lock separation.** Style, aspect ratio and resolution live ONLY in `STYLE_TEMPLATE`
   (text-to-image). The per-item LLM output is a **pure object description**, never style words.
2. **Identity = `ID_WIKIDATA`** (fallback `(ITEM_CLASS, ID_ITEM)`): one image per Q-id serves every
   role it plays (review §4.2 / decision #14).
3. **Idempotency** keys on `(IMAGE_KEY, STYLE_VERSION)`: a style bump regenerates, otherwise skip.
   Candidate 1 keeps the historical key, candidates 2..N append `|c<N>`; the lab appends new
   indexes and never overwrites a candidate.
4. **Provenance + seed are mandatory** on every image row.
5. **No real-time generation for end users** (that is Project B); the lab is an authenticated
   operator tool.
6. **Generation language = English.**
7. **Companies/networks are never synthesized** (decision #7, trademark): `logo-pad` is their only
   representation, so those runs cost no model call.
8. **A manual choice is final.** `f_set_entity_choice()` is the only writer of
   `T_WC_T2S_ENTITY_IMAGE`. Never write that table with `f_sqlbulkupsert`: `ID_WIKIDATA` is NULL
   for genres and the like, a NULL defeats the UNIQUE key, and the upsert inserts a duplicate.
9. **Costs are stored where they are incurred**: `GENERATION_COST` on the image row, `T2T_COST`
   once on the description row. Prices live in `T2I_COST` / `GEMINI_COST` / `OPENAI_COST` /
   `T2T_PRICE` (survey 2026-10-08); refresh them rather than guessing.

## Provider pitfalls

- **Replicate is called over plain HTTP** (`_replicate_predict()`, see its docstring). Do not
  reintroduce the `replicate` client.
- **Gemini `402 RESOURCE_EXHAUSTED`** means the AI Studio project's prepaid credit is spent
  (https://ai.studio/projects), not a bad model id; `GET /v1beta/models` checks ids for free.
- A failed render carries the provider's message in `FAILURE_REASON` (and on the lab thumbnail):
  read it before guessing.

## Failure handling (designed, not yet implemented)

Failures are **categorized, not blindly retried** (review §6.1). A rule-based classifier sets
`FAILURE_CATEGORY`; only `transient_api` auto-retries (bounded, backoff). The others
(`safety_refusal`, `no_source`, `bad_description`, `wrong_entity`, `offspec_ratio`,
`garbled_text`, `low_quality`) are recorded `STATUS='failed'` for a manual review queue (CLI
`--report-failures`, later `back/synthetic-failures.php`). Fix **patterns, not items**: bulk-requeue
a category after a template/version fix. The idempotency probe skips only `STATUS='generated'`, so
failed items self-heal. Review columns: `FAILURE_CATEGORY` / `ATTEMPT_COUNT` / `REVIEW_STATUS` /
`RESOLUTION_ACTION` / `REVIEW_NOTES`.

## Dev loop and conventions

Start with the single-item harness, `--dry-run` (no API key, no DB writes); guards `--limit N` and
`RUN_BUDGET_USD`. Commands: README § Quick start. Conventions: `f_…` public / `_…` private,
Hungarian prefixes (`str`/`lng`/`int`/`arr`/`dbl`), broad try/except with console logging, UTF-8.
`.env` (DB + provider keys) never enters the image or git: `--env-file` at runtime.

## Backlog (Nestor second-brain)

Tasks `SYNTHETIC-IMAGES-NNN` (status, priority, quick wins) live in
`C:\Users\vaugo\Nestor\projets\t2s-backlog\repos\synthetic-images.md`; dashboard
`...\t2s-backlog\index.md`. Consult before implementing. Local PC paths, not on the VPS or cloud
agents.
