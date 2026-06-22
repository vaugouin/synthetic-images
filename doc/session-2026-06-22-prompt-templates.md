# Session handoff — prompt-template work (2026-06-22)

Branch: `feat/logo-pad-live-models-bakeoff`
File touched: [`synthetic_images_functions.py`](../synthetic_images_functions.py)

Resume tomorrow → run the **/git** skill to commit + push. Review the uncommitted diff first
(`git status` shows `synthetic_images_functions.py` modified plus this new `doc/` file).

---

## 1. What this session changed (DONE — in the working tree, not yet committed)

### a. Text-to-image template — suppress all text/labels/arrows
`STYLE_TEMPLATE` ([synthetic_images_functions.py:69](../synthetic_images_functions.py#L69))

- Reason: renders came back as labeled "visual dictionary" plates — a title, callouts with
  leader lines and arrows (e.g. the CinemaScope projector image, the France flag image), some
  even in French. The words *diagram* / *Corbeil* pull the model toward labeled cutaways.
- Fix: changed "Illustrated **diagram**" → "Illustrated **plate**", and added an explicit
  negative clause: *no text, title, caption, labels, callouts, annotations, leader lines,
  arrows, legend, numbers, letters, measurement marks, or border.*

### b. Text-to-text prompt — per-entity subject rules (was film-biased)
`f_text_to_text` + new `T2T_SUBJECT_RULES` / `T2T_CLASS_DEFAULT` / `_f_subject_rule`
([synthetic_images_functions.py](../synthetic_images_functions.py), Stage-1 section)

- Reason: the old description prompt hard-coded a "for a film/cinema technical, prefer the
  equipment…" bias that leaked into *every* class (a location was still told to prefer
  equipment). The repo already had the right vocabulary in
  `T_WC_T2S_REPRESENTATION.T2I_PROMPT_SLOT` ([02_representation_seed.sql](../02_representation_seed.sql)) —
  the text stage just wasn't using it.
- Fix: one shared prompt scaffold (length / concreteness / **no-style-leakage** guard — all
  kept in one place) + a single injected subject line resolved per `(item_class,
  representation)`. Dict fully populated from the representation seed, with golden-set slug
  aliases (`landscape`↔`day-landscape`, `object`↔`award-object`, etc.) so eval items hit a
  specific rule. Added a "Stay strictly on '{name}'… add no unrelated objects/scenes/domains"
  guard. Dropped the film example (now lives only under `technical/plate`).
- Verified: syntax OK; `_f_subject_rule` resolves correctly for every golden-set class and
  falls back gracefully for unknown pairs.

### c. Flag rule — pole on the LEFT, not the top
`("location","flag")` and `("country","flag")`
([synthetic_images_functions.py:330](../synthetic_images_functions.py#L330) and ~362)

- Reason: the France render hung the flag from a **horizontal pole at the top**; the hampe must
  be a **vertical pole on the left (hoist) edge** along the blue band.
- Fix: both rules now say "full rectangular flag, displayed flat and front-facing, colours
  correctly ordered; if a flagpole is shown it is a vertical pole along the LEFT (hoist) edge —
  never a horizontal pole above the flag, flag hangs to its right."

---

## 2. Things to remember before/after committing

- **Style/prompt version bumps.** The image change is a real style change. Production keys
  idempotency on `(IMAGE_KEY, STYLE_VERSION)` (keystone rule #3), so already-`generated` rows
  won't regenerate unless `STYLE_VERSION` is bumped (env var, currently `v1`). Likewise the
  description change → consider bumping `T2T_PROMPT_VERSION` so it's recorded in `run.json`
  provenance. The bake-off (isolated tree) doesn't care; production re-runs do.
- **Golden-set vs seed slug drift.** The golden set uses `landscape`, `street-level`,
  `building`, `object`, `composite`, `group-portrait`, `movement/plate` — none of which exist
  in `T_WC_T2S_REPRESENTATION`. Handled via aliases for now, but the two vocabularies should be
  reconciled separately (the seed is what production uses).
- **Suggested verification render** before trusting the changes: a small `--limit` /
  flag-only render (e.g. `france`, `japan`) to confirm pole-left + no-labels, or a cheap
  `--skip-images` Stage-1 pass to read the new descriptions across the golden set.

---

## 3. PENDING — not started: anchor movement cards to a real film

Open design question from end of session. Goal: instead of "an iconic film that exemplifies the
movement", name the **actual top-rated film** of the movement (in MariaDB) and optionally use
its real poster (available via TMDb URL).

### Decision fork (decide first)
- **Option A (recommended):** resolve the film from the DB, feed its identity (title +
  overview) into the description, synthesize the plate in the locked style → card still matches
  the grid. Poster URL optionally used only as a *reference image* to the t2i model, not as
  output.
- **Option B:** composite the real poster onto the 2:3 canvas (like `logo-pad` / `poster-mix`).
  Accurate but breaks style consistency + IP concern (posters are copyrighted — same logic that
  made companies/networks logo-pad-only, decision #7). Different path, not the synthetic one.

### Option A mechanism (sketched, not implemented)
1. Make the rule a template with a slot:
   `("movement","movie-poster"): "a poster-like scene evoking the film {exemplar}, which exemplifies the movement"`
2. Add `_f_resolve_exemplar(strclass, stridwikidata, intdryrun)` mirroring
   `f_lookup_entity_table` — DB-free in dry-run; returns `(label, poster_path)` or `("","")`.
3. Thread `stridwikidata` into `f_text_to_text(...)` (new optional param) and `.format()` the
   rule; fall back to "an iconic film that exemplifies it" when empty.

### The one blocker
The movement→top-film SQL is a **placeholder** — real schema unknown:
- how `T2S_MOVEMENT` links to movies (junction table? movie IDs on the row? keyword/topic-derived?)
- which movie table + rating column (`VOTE_AVERAGE`? `POPULARITY`?)
- poster: TMDb `POSTER_PATH` → full URL via the logo CDN base (`strlogobaseurl`).

**Next action when resuming:** discover the linkage (read-only) via the `text2sql` MCP
(`get_movement` / `get_movie`) or by inspecting `T2S_MOVEMENT`, then wire Option A end to end.
If passing the poster as a Gemini reference image, confirm Nano Banana Pro reference-image
support via Context7 first.

---

## 4. Suggested commit framing (for /git tomorrow)

These are three cohesive, related changes to the generation prompts:
- t2i: suppress text/labels/arrows in the style template
- t2t: per-(class, representation) subject rules replacing the film-biased generic prompt
- t2t: precise flag orientation (pole left)

The movement-exemplar work (section 3) is **not** in the tree yet — nothing to commit there.
