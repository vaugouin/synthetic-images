#!/usr/bin/env python
"""
eval_bakeoff -- the Project A model/prompt bake-off harness (eval-plan.md §6).

Sweeps the fixed golden set (golden_set.py) over candidate text-to-text and text-to-image
models, reusing the production pipeline's stage functions (synthetic_images_functions). Writes
an isolated, reproducible run tree -- NO database writes, NO shared-store writes -- so eval
output never pollutes production:

    <EVAL_DIR>/<run_id>/
      run.json                                   models, versions, seed, budget, totals
      descriptions.csv                           stage-1: every (item x t2t_model) description + blank human-score cols
      scores.csv                                 stage-2: every rendered image's metrics + blank human-score cols
      images/<t2t>__<t2i>/<item>.webp            the renders
      contact_sheet__<t2t>__<t2i>.html           the grid to eyeball (the decisive consistency artifact, §4)
      index.html                                 links every sheet + the run summary

Because source text is resolved ONCE per item and reused across all t2t models, the description
comparison is apples-to-apples (eval-plan §0 principle 3).

Examples:
  # Cheap Stage-1 pass -- descriptions only, no images, no API image cost:
  python eval_bakeoff.py --skip-images --t2t-models claude-haiku-4-5-20251001,claude-sonnet-4-6
  # Full end-to-end sweep on a 6-item subset:
  python eval_bakeoff.py --limit 6
  # Offline plumbing test (stubs, no keys):
  python eval_bakeoff.py --dry-run --limit 3

Guards (eval-plan §6): --dry-run, --limit, and a per-run USD budget ceiling (RUN_BUDGET_USD /
--budget). Concurrency is 1 here on purpose; parallelise later once the harness is trusted.
"""
import os
import sys
import csv
import json
import time
import html
import argparse
from datetime import datetime

import pytz

import synthetic_images_functions as si
from golden_set import GOLDEN_SET

strevaldir = os.environ.get("EVAL_DIR", "/shared/eval")
strtimezone = os.environ.get("USER_TIMEZONE", "Europe/Paris")
dblbudgetdefault = float(os.environ.get("RUN_BUDGET_USD", "20"))

# Default candidates. t2t: both Anthropic models are wired today (claude-* dispatch); add the
# OpenAI/Gemini cross-check once those branches land. t2i: only Replicate FLUX is wired.
T2T_MODELS_DEFAULT = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"]
T2I_MODELS_DEFAULT = ["black-forest-labs/flux-schnell"]


def f_now():
    return datetime.now(pytz.timezone(strtimezone)).strftime("%Y-%m-%d %H:%M:%S")


def f_modelslug(strmodel):
    """Filesystem-safe slug for a model id (drops org prefix, replaces punctuation)."""
    strbase = (strmodel or "").split("/")[-1]
    return "".join(c if c.isalnum() else "-" for c in strbase).strip("-").lower()


# ---------------------------------------------------------------------------
# Per-item generation (reuses the pipeline stages; writes to the eval tree only)
# ---------------------------------------------------------------------------
def f_describe(arritem, strsourcetext, strt2tmodel, intdryrun):
    """Stage 1: produce one object description; return (description, seconds)."""
    dblstart = time.time()
    strdesc = si.f_text_to_text(
        arritem["name"], strsourcetext, arritem.get("representation", "plate"),
        arritem.get("item_class", ""), intdryrun=intdryrun, strmodel=strt2tmodel,
    )
    return strdesc, round(time.time() - dblstart, 3)


def f_render(strdesc, strt2imodel, lngseed, strimgpath, intdryrun):
    """Stage 2: render + validate + write the webp. Return a metrics dict."""
    dblstart = time.time()
    bytesimage, lngseedused, dblcost = si.f_text_to_image(
        strdesc, lngseed=lngseed, intdryrun=intdryrun, strmodel=strt2imodel
    )
    dblelapsed = round(time.time() - dblstart, 3)
    intok, strreason, lngw, lngh = si.f_validate_image(bytesimage)
    if intok:
        os.makedirs(os.path.dirname(strimgpath), exist_ok=True)
        with open(strimgpath, "wb") as fh:
            fh.write(bytesimage)
    return {
        "status": "generated" if intok else "failed",
        "width": lngw, "height": lngh,
        "aspect_ok": 1 if intok else 0,
        "failure_reason": "" if intok else strreason,
        "file_size": len(bytesimage) if (intok and bytesimage) else 0,
        "seed": lngseedused, "cost": dblcost, "seconds": dblelapsed,
    }


# ---------------------------------------------------------------------------
# Contact sheets (the decisive artifact -- eyeball the grid, eval-plan §4)
# ---------------------------------------------------------------------------
def f_write_contact_sheet(strpath, strtitle, arrcells):
    """arrcells: list of {category, name, img_rel, status, failure_reason}."""
    arrhtml = [
        "<!doctype html><html><head><meta charset='utf-8'><title>", html.escape(strtitle),
        "</title><style>",
        "body{background:#3a3a3a;color:#eee;font:13px/1.4 system-ui,sans-serif;margin:24px}",
        "h1{font-size:16px;font-weight:600}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px;margin-top:16px}",
        ".card{background:#2a2a2a;border-radius:6px;overflow:hidden;display:flex;flex-direction:column}",
        ".card img{width:100%;aspect-ratio:2/3;object-fit:cover;background:#111;display:block}",
        ".card .cap{padding:6px 8px}.card .nm{font-weight:600}.card .ct{color:#9a9a9a;font-size:11px}",
        ".fail{aspect-ratio:2/3;display:flex;align-items:center;justify-content:center;background:#5a2222;",
        "color:#f2c0c0;text-align:center;padding:8px;font-size:11px}",
        "</style></head><body><h1>", html.escape(strtitle), "</h1><div class='grid'>",
    ]
    for arrcell in arrcells:
        arrhtml.append("<div class='card'>")
        if arrcell["status"] == "generated":
            arrhtml.append("<img src='{0}' alt='{1}'>".format(
                html.escape(arrcell["img_rel"]), html.escape(arrcell["name"])))
        else:
            arrhtml.append("<div class='fail'>FAILED<br>{0}</div>".format(
                html.escape(arrcell.get("failure_reason", "") or "")))
        arrhtml.append("<div class='cap'><div class='nm'>{0}</div><div class='ct'>{1}</div></div>".format(
            html.escape(arrcell["name"]), html.escape(arrcell["category"])))
        arrhtml.append("</div>")
    arrhtml.append("</div></body></html>")
    with open(strpath, "w", encoding="utf-8") as fh:
        fh.write("".join(arrhtml))


def f_write_index(strdir, arrsheets, arrrun):
    arrhtml = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Bake-off ",
        html.escape(arrrun["run_id"]),
        "</title><style>body{font:14px/1.5 system-ui,sans-serif;margin:32px;max-width:820px}",
        "code{background:#eee;padding:1px 4px;border-radius:3px}li{margin:4px 0}</style></head><body>",
        "<h1>Bake-off ", html.escape(arrrun["run_id"]), "</h1>",
        "<p>t2t: <code>", html.escape(", ".join(arrrun["t2t_models"])), "</code><br>",
        "t2i: <code>", html.escape(", ".join(arrrun["t2i_models"])), "</code><br>",
        "items: ", str(arrrun["item_count"]), " &middot; generated: ", str(arrrun["generated"]),
        " &middot; failed: ", str(arrrun["failed"]),
        " &middot; spent: $", "{0:.3f}".format(arrrun["spent_usd"]), "</p>",
        "<h2>Contact sheets</h2><ul>",
    ]
    for strsheet in arrsheets:
        arrhtml.append("<li><a href='{0}'>{0}</a></li>".format(html.escape(strsheet)))
    arrhtml.append("</ul><h2>Data</h2><ul><li><a href='descriptions.csv'>descriptions.csv</a> "
                   "(stage-1 + blank human-score columns)</li>"
                   "<li><a href='scores.csv'>scores.csv</a> (stage-2 + blank human-score columns)</li>"
                   "<li><a href='run.json'>run.json</a></li></ul></body></html>")
    with open(os.path.join(strdir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("".join(arrhtml))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def f_run(args):
    arrt2t = [m.strip() for m in args.t2t_models.split(",") if m.strip()]
    arrt2i = [m.strip() for m in args.t2i_models.split(",") if m.strip()]
    arritems = GOLDEN_SET[: args.limit] if args.limit else GOLDEN_SET
    strrunid = args.run_id or ("run-" + datetime.now(pytz.timezone(strtimezone)).strftime("%Y%m%d-%H%M%S"))
    strrundir = os.path.join(strevaldir, strrunid)
    os.makedirs(strrundir, exist_ok=True)

    print("Bake-off {0}: {1} item(s) x {2} t2t x {3} t2i  (skip_images={4}, dry_run={5})".format(
        strrunid, len(arritems), len(arrt2t), len(arrt2i),
        bool(args.skip_images), bool(args.dry_run)))
    print("  out: {0}".format(strrundir))

    arrdescrows, arrscorerows = [], []
    dctcells = {}  # (t2t_slug, t2i_slug) -> list of cells, in golden-set order
    dblspent, lnggen, lngfail = 0.0, 0, 0
    intbudgethit = 0

    for arritem in arritems:
        arritem = dict(arritem)  # local copy; we overwrite 'name' with the DB's native name
        # Identity is keyed on ID_WIKIDATA (decision #14). With a pinned Q-id we look the row up
        # BY Q-id and take the DB's NATIVE name + OVERVIEW -- exactly how production reads the
        # table forward, so no typed-name or homonym matching. Only items with NO pinned Q-id
        # fall back to a last-resort label match. Skipped in dry-run so the harness stays offline.
        stridw = arritem.get("id_wikidata") or ""
        strov = arritem.get("overview", "")
        strfrom = "golden-set"
        if not args.dry_run:
            if stridw:
                dbname, dbov, strtbl = si.f_lookup_entity_table(arritem.get("item_class", ""), stridw)
                if strtbl:
                    strfrom = strtbl
                    if dbov:
                        strov = dbov
                    if dbname and dbname != arritem["name"]:
                        print("  [resolve] {0}: name '{1}' -> '{2}' ({3} via {4})".format(
                            arritem["slug"], arritem["name"], dbname, strtbl, stridw))
                        arritem["name"] = dbname
                else:
                    wname = si.f_lookup_wikidata_name(stridw)
                    if wname:
                        strfrom = "WIKIDATA_ITEM_V1"
                        if wname != arritem["name"]:
                            print("  [resolve] {0}: name '{1}' -> '{2}' (WIKIDATA_ITEM_V1 via {3})".format(
                                arritem["slug"], arritem["name"], wname, stridw))
                            arritem["name"] = wname
            else:
                idwl = si.f_lookup_wikidata_label(arritem["name"])
                if idwl:
                    stridw, strfrom = idwl, "WIKIDATA_ITEM_V1"
                    print("  [resolve] {0}: (no pinned Q-id) -> {1} (last-resort label match)".format(
                        arritem["slug"], idwl))
                else:
                    print("  [resolve] {0}: no DB match for '{1}' ({2})".format(
                        arritem["slug"], arritem["name"], arritem.get("item_class", "")))
        # Resolve source text ONCE per item -> identical input across all t2t models (§0.3).
        strsource, strsourcetype = si.f_resolve_source_text(
            stridw, arritem["name"], strov, intdryrun=1 if args.dry_run else 0,
        )
        for strt2t in arrt2t:
            try:
                strdesc, dblt2tsec = f_describe(arritem, strsource, strt2t, 1 if args.dry_run else 0)
            except Exception as err:  # broad by house convention; record + continue
                strdesc, dblt2tsec = "", 0.0
                print("  [t2t error] {0} / {1}: {2}".format(arritem["slug"], strt2t, err))
            arrdescrows.append({
                "run_id": strrunid, "item": arritem["slug"], "category": arritem["category"],
                "name": arritem["name"], "item_class": arritem.get("item_class", ""),
                "representation": arritem.get("representation", ""), "source": strsourcetype,
                "id_wikidata": stridw, "id_wikidata_from": strfrom,
                "t2t_model": strt2t, "t2t_prompt_version": os.environ.get("T2T_PROMPT_VERSION", "v1"),
                "t2t_seconds": dblt2tsec, "description": strdesc,
                # blank human-score columns (eval-plan §3) -- fill during review:
                "factual_accuracy": "", "visual_specificity": "", "conciseness": "", "no_style_leakage": "",
            })
            print("  [desc] {0:<18} {1:<28} {2}".format(
                arritem["slug"], f_modelslug(strt2t), (strdesc or "(empty)")[:70]))

            if args.skip_images or not strdesc:
                continue

            for strt2i in arrt2i:
                strt2tslug, strt2islug = f_modelslug(strt2t), f_modelslug(strt2i)
                dctcells.setdefault((strt2tslug, strt2islug), [])
                # URL-style relative path (forward slashes) so the contact-sheet <img> works on any OS.
                strimgrel = "images/{0}__{1}/{2}.webp".format(strt2tslug, strt2islug, arritem["slug"])
                if intbudgethit:
                    dctcells[(strt2tslug, strt2islug)].append({
                        "category": arritem["category"], "name": arritem["name"],
                        "status": "failed", "failure_reason": "budget ceiling reached", "img_rel": strimgrel})
                    continue
                strimgpath = os.path.join(strrundir, *strimgrel.split("/"))
                try:
                    arrm = f_render(strdesc, strt2i, args.seed, strimgpath, 1 if args.dry_run else 0)
                except Exception as err:
                    arrm = {"status": "failed", "width": 0, "height": 0, "aspect_ok": 0,
                            "failure_reason": "render error: {0}".format(err), "file_size": 0,
                            "seed": args.seed or 0, "cost": 0.0, "seconds": 0.0}
                    print("  [t2i error] {0} / {1}: {2}".format(arritem["slug"], strt2i, err))
                dblspent += arrm["cost"]
                if arrm["status"] == "generated":
                    lnggen += 1
                else:
                    lngfail += 1
                arrscorerows.append({
                    "run_id": strrunid, "item": arritem["slug"], "category": arritem["category"],
                    "name": arritem["name"], "t2t_model": strt2t, "t2i_model": strt2i,
                    "seed": arrm["seed"], "status": arrm["status"], "width": arrm["width"],
                    "height": arrm["height"], "aspect_ok": arrm["aspect_ok"],
                    "file_size": arrm["file_size"], "t2i_cost": arrm["cost"],
                    "t2i_seconds": arrm["seconds"], "failure_reason": arrm["failure_reason"],
                    "image_path": strimgrel if arrm["status"] == "generated" else "",
                    # blank human-score columns (eval-plan §4):
                    "style_consistency": "", "prompt_adherence": "", "artifact_free": "", "aesthetic": "",
                })
                dctcells[(strt2tslug, strt2islug)].append({
                    "category": arritem["category"], "name": arritem["name"],
                    "status": arrm["status"], "failure_reason": arrm["failure_reason"],
                    "img_rel": strimgrel})
                print("    [{0}] {1:<18} {2} -> {3} (${4:.3f})".format(
                    arrm["status"], arritem["slug"], strt2islug, strimgrel, arrm["cost"]))
                if dblspent >= args.budget:
                    intbudgethit = 1
                    print("  Budget ceiling ${0:.2f} reached -- remaining renders skipped.".format(args.budget))

    # --- write outputs ---
    _write_csv(os.path.join(strrundir, "descriptions.csv"), arrdescrows)
    if not args.skip_images:
        _write_csv(os.path.join(strrundir, "scores.csv"), arrscorerows)

    arrsheets = []
    for (strt2tslug, strt2islug), arrcells in sorted(dctcells.items()):
        strsheet = "contact_sheet__{0}__{1}.html".format(strt2tslug, strt2islug)
        f_write_contact_sheet(os.path.join(strrundir, strsheet),
                              "t2t={0}  t2i={1}".format(strt2tslug, strt2islug), arrcells)
        arrsheets.append(strsheet)

    arrrun = {
        "run_id": strrunid, "timestamp": f_now(), "t2t_models": arrt2t, "t2i_models": arrt2i,
        "style_version": os.environ.get("STYLE_VERSION", "v1"),
        "t2t_prompt_version": os.environ.get("T2T_PROMPT_VERSION", "v1"),
        "t2i_prompt_version": os.environ.get("T2I_PROMPT_VERSION", "v1"),
        "seed": args.seed, "budget_usd": args.budget, "skip_images": bool(args.skip_images),
        "dry_run": bool(args.dry_run), "item_count": len(arritems),
        "generated": lnggen, "failed": lngfail, "spent_usd": round(dblspent, 4),
        "budget_hit": bool(intbudgethit),
    }
    with open(os.path.join(strrundir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(arrrun, fh, indent=2, ensure_ascii=False)
    f_write_index(strrundir, arrsheets, arrrun)

    print("\nDone. generated={0} failed={1} spent=${2:.3f}".format(lnggen, lngfail, dblspent))
    print("Open: {0}/index.html".format(strrundir))
    return 0


def f_run_from_descriptions(args):
    """
    Stage-2 (eval-plan §4): render an EXISTING descriptions.csv with the configured t2i model(s),
    reusing the FROZEN golden descriptions so image models compete on identical inputs. No text
    stage, no DB -- everything needed is already in the CSV. Contact sheets are keyed by the
    source description's t2t model x the new t2i model, so the new grid sits next to the old one.
    """
    arrt2i = [m.strip() for m in args.t2i_models.split(",") if m.strip()]
    arrrows = []
    with open(args.descriptions, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("description") or "").strip():
                arrrows.append(row)
    if args.filter_t2t:
        arrrows = [r for r in arrrows if (r.get("t2t_model") or "") == args.filter_t2t]
    if args.limit:
        arrrows = arrrows[: args.limit]
    strrunid = args.run_id or ("render-" + datetime.now(pytz.timezone(strtimezone)).strftime("%Y%m%d-%H%M%S"))
    strrundir = os.path.join(strevaldir, strrunid)
    os.makedirs(strrundir, exist_ok=True)
    print("Render-from-descriptions {0}: {1} description(s) x {2} t2i  (dry_run={3})".format(
        strrunid, len(arrrows), len(arrt2i), bool(args.dry_run)))
    print("  from: {0}".format(args.descriptions))
    print("  out:  {0}".format(strrundir))

    arrscorerows, dctcells = [], {}
    dblspent, lnggen, lngfail, intbudgethit = 0.0, 0, 0, 0
    for row in arrrows:
        strdesc = row["description"]
        strt2t = (row.get("t2t_model") or "frozen").strip()
        strt2tslug = f_modelslug(strt2t)
        for strt2i in arrt2i:
            strt2islug = f_modelslug(strt2i)
            strkey = (strt2tslug, strt2islug)
            dctcells.setdefault(strkey, [])
            strimgrel = "images/{0}__{1}/{2}.webp".format(strt2tslug, strt2islug, row["item"])
            if intbudgethit:
                dctcells[strkey].append({"category": row.get("category", ""), "name": row.get("name", ""),
                    "status": "failed", "failure_reason": "budget ceiling reached", "img_rel": strimgrel})
                continue
            strimgpath = os.path.join(strrundir, *strimgrel.split("/"))
            try:
                arrm = f_render(strdesc, strt2i, args.seed, strimgpath, 1 if args.dry_run else 0)
            except Exception as err:
                arrm = {"status": "failed", "width": 0, "height": 0, "aspect_ok": 0,
                        "failure_reason": "render error: {0}".format(err), "file_size": 0,
                        "seed": args.seed or 0, "cost": 0.0, "seconds": 0.0}
                print("  [t2i error] {0} / {1}: {2}".format(row["item"], strt2i, err))
            dblspent += arrm["cost"]
            if arrm["status"] == "generated":
                lnggen += 1
            else:
                lngfail += 1
            arrscorerows.append({
                "run_id": strrunid, "item": row["item"], "category": row.get("category", ""),
                "name": row.get("name", ""), "t2t_model": strt2t, "t2i_model": strt2i,
                "seed": arrm["seed"], "status": arrm["status"], "width": arrm["width"],
                "height": arrm["height"], "aspect_ok": arrm["aspect_ok"], "file_size": arrm["file_size"],
                "t2i_cost": arrm["cost"], "t2i_seconds": arrm["seconds"], "failure_reason": arrm["failure_reason"],
                "image_path": strimgrel if arrm["status"] == "generated" else "",
                "style_consistency": "", "prompt_adherence": "", "artifact_free": "", "aesthetic": "",
            })
            dctcells[strkey].append({"category": row.get("category", ""), "name": row.get("name", ""),
                "status": arrm["status"], "failure_reason": arrm["failure_reason"], "img_rel": strimgrel})
            print("    [{0}] {1:<18} {2} -> {3} (${4:.3f})".format(
                arrm["status"], row["item"], strt2islug, strimgrel, arrm["cost"]))
            if dblspent >= args.budget:
                intbudgethit = 1
                print("  Budget ceiling ${0:.2f} reached -- remaining renders skipped.".format(args.budget))

    _write_csv(os.path.join(strrundir, "scores.csv"), arrscorerows)
    arrsheets = []
    for (strt2tslug, strt2islug), arrcells in sorted(dctcells.items()):
        strsheet = "contact_sheet__{0}__{1}.html".format(strt2tslug, strt2islug)
        f_write_contact_sheet(os.path.join(strrundir, strsheet),
                              "t2t={0}  t2i={1}".format(strt2tslug, strt2islug), arrcells)
        arrsheets.append(strsheet)
    arrrun = {
        "run_id": strrunid, "timestamp": f_now(), "mode": "render-from-descriptions",
        "descriptions_source": args.descriptions,
        "t2t_models": sorted({(r.get("t2t_model") or "frozen") for r in arrrows}),
        "t2i_models": arrt2i, "style_version": os.environ.get("STYLE_VERSION", "v1"),
        "t2i_prompt_version": os.environ.get("T2I_PROMPT_VERSION", "v1"), "seed": args.seed,
        "budget_usd": args.budget, "dry_run": bool(args.dry_run), "item_count": len(arrrows),
        "generated": lnggen, "failed": lngfail, "spent_usd": round(dblspent, 4),
        "budget_hit": bool(intbudgethit),
    }
    with open(os.path.join(strrundir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(arrrun, fh, indent=2, ensure_ascii=False)
    f_write_index(strrundir, arrsheets, arrrun)
    print("\nDone. generated={0} failed={1} spent=${2:.3f}".format(lnggen, lngfail, dblspent))
    print("Open: {0}/index.html".format(strrundir))
    return 0


def _write_csv(strpath, arrrows):
    if not arrrows:
        return
    with open(strpath, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(arrrows[0].keys()))
        writer.writeheader()
        writer.writerows(arrrows)


def f_parse_args(argv):
    p = argparse.ArgumentParser(description="Project A model/prompt bake-off (eval-plan §6).")
    p.add_argument("--t2t-models", dest="t2t_models", default=",".join(T2T_MODELS_DEFAULT),
                   help="comma-separated text-to-text model ids")
    p.add_argument("--t2i-models", dest="t2i_models", default=",".join(T2I_MODELS_DEFAULT),
                   help="comma-separated text-to-image model ids")
    p.add_argument("--limit", type=int, default=0, help="use only the first N golden-set items")
    p.add_argument("--seed", type=int, default=0, help="text-to-image seed (determinism)")
    p.add_argument("--budget", type=float, default=dblbudgetdefault, help="per-run USD ceiling")
    p.add_argument("--run-id", dest="run_id", default="", help="reuse/name the run dir")
    p.add_argument("--skip-images", action="store_true", help="stage-1 only: descriptions, no renders")
    p.add_argument("--descriptions", default="", help="stage-2: render an existing descriptions.csv "
                   "with --t2i-models (reuses the frozen descriptions; skips the text stage)")
    p.add_argument("--filter-t2t", dest="filter_t2t", default="", help="render-from-descriptions: "
                   "only render rows whose t2t_model equals this (e.g. claude-sonnet-4-6)")
    p.add_argument("--dry-run", action="store_true", help="stubs only -- no API calls, no keys")
    return p.parse_args(argv)


def main(argv):
    args = f_parse_args(argv)
    if args.descriptions:
        return f_run_from_descriptions(args)
    return f_run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
