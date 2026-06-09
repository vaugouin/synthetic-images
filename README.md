# synthetic-images — Synthetic entity illustrations (Project A)

Generate **consistent, style-locked 2:3 card illustrations** for database entities that lack a
usable image (or whose Wikipedia images vary wildly in style and aspect ratio, breaking card grids).
Part of the **Agent BBB** multi-repo system; the design spec is
`tmdb-front/doc/An image for everything/An image for everything - Project A review.md`.

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

# offline smoke test (no API key, no DB writes):
python synthetic-images.py --class technical --item-id 5 --representation plate --dry-run --force

# batch the Phase-1 class:
python synthetic-images.py --class technical --dry-run        # preview
python synthetic-images.py --class technical --limit 10       # real run, capped
```

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

## Status

Scaffold + Phase-1 (Technicals) vertical slice. The text-to-text and text-to-image **model calls
are pluggable stubs** pending the bake-off (`doc/eval-plan.md`); `--dry-run` exercises the full
loop offline. See `AGENTS.md` for the keystone rules and phasing.

## License

MIT © Philippe Vaugouin.
