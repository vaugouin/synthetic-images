#!/usr/bin/env python
"""
synthetic_images_lab -- the lab: a small web service over the synthetic-images pipeline
(SYNTHETIC-IMAGES-020).

Pick an entity class and an entity, see the complete text-to-text and text-to-image prompts and
the estimated cost before spending anything, get the description (and correct it), render N
candidates with any text-to-image model, watch them arrive, and choose the image served for the
entity. Every render is a candidate (SYNTHETIC-IMAGES-019): nothing is thrown away, and a choice
made here is never undone by a batch.

The service calls the same functions as the command line (synthetic_images_functions); the keys
stay in this repository's .env. Served behind NGINX at /synthetic-review/ (Basic auth on the whole
path, API included); the page uses relative URLs, so the prefix is free.

    python synthetic_images_lab.py            # LAB_HOST / LAB_PORT, see .env.example

Guards: the cost is shown before every launch; at most LAB_MAX_RENDERS renders per click; a daily
ceiling LAB_DAILY_BUDGET_USD computed from the database (renders + descriptions of the day);
LAB_DRY_RUN=1 stubs every model call (zero spend) while keeping the database writes.
"""
import os
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import citizenphil as cp
import synthetic_images_functions as si

strlabhost = os.environ.get("LAB_HOST", "127.0.0.1")
lnglabport = int(os.environ.get("LAB_PORT", "8195"))
dbllabbudget = float(os.environ.get("LAB_DAILY_BUDGET_USD", "5"))
lnglabmaxrenders = int(os.environ.get("LAB_MAX_RENDERS", "4"))
# Images per click preselected in the page (the batch keeps CANDIDATES): one, so a first look is cheap.
lnglabdefaultrenders = int(os.environ.get("LAB_DEFAULT_RENDERS", "1"))
intlabdryrun = 1 if os.environ.get("LAB_DRY_RUN", "0") == "1" else 0
strlabdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lab")

# citizenphil holds ONE shared connection: every database touch goes through this lock. Provider
# calls (seconds to minutes) run outside it, so the page stays responsive during renders.
DBLOCK = threading.RLock()
JOBS = {}
JOBSLOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=2)
PREPS = {}                      # prep_id -> prepared entity (avoids a second web search)
PREPS_MAX = 200

app = FastAPI(title="Synthetic images lab", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=strlabdir), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _image_url(strimagepath):
    """IMAGE_PATH ('synthetic-images/<class>/<key>.webp') -> the lab's relative file URL."""
    if not strimagepath:
        return ""
    strprefix = si.strsubdir.strip("/") + "/"
    strrel = strimagepath[len(strprefix):] if strimagepath.startswith(strprefix) else strimagepath
    return "files/" + strrel


def _budget():
    with DBLOCK:
        dblspent = si.f_spent_today()
    return {"spent_today": dblspent, "daily_budget": dbllabbudget,
            "remaining": round(max(0.0, dbllabbudget - dblspent), 4)}


def _check_budget(dblestimate):
    dctbudget = _budget()
    if dctbudget["spent_today"] + dblestimate > dbllabbudget:
        raise HTTPException(status_code=409, detail=(
            "Plafond du jour atteint : {0:.2f} $ dépensés sur {1:.2f} $, cette action en coûterait "
            "{2:.3f}. Relever LAB_DAILY_BUDGET_USD si c'est voulu.").format(
                dctbudget["spent_today"], dbllabbudget, dblestimate))
    return dctbudget


def _t2t_estimate(strmodel, strsystem, strprompt):
    """Rough cost of a description before the call: ~4 characters per token in, ~400 tokens out
    (the description plus a little thinking at low effort)."""
    lngin = int((len(strsystem) + len(strprompt)) / 4) + 20
    return si.f_t2t_cost(strmodel, lngin, 400)


def _remember_prep(dctprep):
    strid = uuid.uuid4().hex[:12]
    if len(PREPS) >= PREPS_MAX:
        PREPS.pop(next(iter(PREPS)))
    PREPS[strid] = dctprep
    return strid


def _get_description(iddescription):
    with DBLOCK:
        cur = cp.f_getconnection().cursor()
        cur.execute(
            "SELECT * FROM " + si.strsqlns + "T2S_SYNTHETIC_DESCRIPTION WHERE ID_SYNTHETIC_DESCRIPTION=%s",
            (iddescription,))
        return cur.fetchone()


def _prep_from_description(arrdesc):
    return {
        "item_class": arrdesc["ITEM_CLASS"], "id_item": arrdesc["ID_ITEM"],
        "id_wikidata": arrdesc["ID_WIKIDATA"], "name": arrdesc["ENTITY_NAME"],
        "representation": arrdesc["REPRESENTATION"],
        "identity": si.f_identity(arrdesc["ID_WIKIDATA"], arrdesc["ITEM_CLASS"], arrdesc["ID_ITEM"]),
        "source": arrdesc["SOURCE"], "source_url": arrdesc["SOURCE_URL"], "source_text": "",
    }


# ---------------------------------------------------------------------------
# Page and files
# ---------------------------------------------------------------------------
@app.get("/")
def page():
    return FileResponse(os.path.join(strlabdir, "index.html"))


@app.get("/files/{strpath:path}")
def files(strpath: str):
    strroot = os.path.realpath(si.strshareddatadir)
    strfull = os.path.realpath(os.path.join(strroot, strpath))
    if not strfull.startswith(strroot + os.sep) or not os.path.isfile(strfull):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(strfull, headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
@app.get("/api/config")
def config():
    arrt2i = []
    for strmodel in si.T2I_COST:
        arrt2i.append({"id": strmodel, "cost": si.f_t2i_cost(strmodel),
                       "default": strmodel == si.strt2imodel})
    arrt2i.sort(key=lambda d: d["cost"])
    arrt2t = [{"id": strmodel, "price_in": prices[0], "price_out": prices[1],
               "default": strmodel == si.strt2tmodel} for strmodel, prices in si.T2T_PRICE.items()]
    arrclasses = [{"id": strclass, "hint": dctcat.get("hint", "")}
                  for strclass, dctcat in si.ENTITY_CATALOG.items()]
    dctout = {"t2i": arrt2i, "t2t": arrt2t, "classes": arrclasses,
              "candidates_default": max(1, min(lnglabdefaultrenders, lnglabmaxrenders)), "max_renders": lnglabmaxrenders,
              "style_version": si.strstyleversion, "dry_run": bool(intlabdryrun)}
    dctout.update(_budget())
    return dctout


@app.get("/api/representations")
def representations(item_class: str):
    with DBLOCK:
        arrrows = si.f_list_representations(item_class)
        strdefault = si.f_default_representation(item_class)
    return {"default": strdefault, "items": arrrows}


@app.get("/api/entities")
def entities(item_class: str, q: str = "", limit: int = 30):
    if item_class not in si.ENTITY_CATALOG:
        raise HTTPException(status_code=400, detail="classe inconnue")
    with DBLOCK:
        return {"items": si.f_search_entities(item_class, q, limit)}


@app.get("/api/budget")
def budget():
    return _budget()


# ---------------------------------------------------------------------------
# The workshop: preview -> describe -> render
# ---------------------------------------------------------------------------
class PreviewIn(BaseModel):
    item_class: str
    id_item: int
    representation: str = ""
    t2t_model: str = ""
    t2i_model: str = ""
    n: int = 1


@app.post("/api/preview")
def preview(body: PreviewIn):
    """No model call: the source text, both prompts and the estimate. (The source text may come
    from a web search when the database has none, which is the only possible spend here.)"""
    if body.item_class not in si.ENTITY_CATALOG:
        raise HTTPException(status_code=400, detail="classe inconnue")
    with DBLOCK:
        dctprep = si.f_prepare_entity(body.item_class, body.id_item, None, "", "",
                                      body.representation, intdryrun=0)
    if not dctprep.get("name"):
        raise HTTPException(status_code=404, detail="entité introuvable")
    strt2t = body.t2t_model or si.strt2tmodel
    strt2i = body.t2i_model or si.strt2imodel
    lngn = max(1, min(int(body.n or 1), lnglabmaxrenders))
    strprompt = si.f_build_t2t_prompt(dctprep["name"], dctprep["source_text"],
                                      dctprep["representation"], dctprep["item_class"])
    dblt2t = _t2t_estimate(strt2t, si.T2T_SYSTEM, strprompt)
    dblt2i = round(lngn * si.f_t2i_cost(strt2i), 4)
    dctout = {
        "prep_id": _remember_prep(dctprep), "entity": {k: dctprep[k] for k in (
            "item_class", "id_item", "id_wikidata", "name", "representation", "source")},
        "source_text": dctprep["source_text"],
        "t2t": {"model": strt2t, "system": si.T2T_SYSTEM, "prompt": strprompt},
        "t2i": {"model": strt2i, "template": si.STYLE_TEMPLATE},
        "estimate": {"t2t": dblt2t, "t2i": dblt2i, "n": lngn, "total": round(dblt2t + dblt2i, 4)},
    }
    dctout.update(_budget())
    return dctout


class DescribeIn(BaseModel):
    prep_id: str
    t2t_model: str = ""


@app.post("/api/describe")
def describe(body: DescribeIn):
    dctprep = PREPS.get(body.prep_id)
    if not dctprep:
        raise HTTPException(status_code=410, detail="prévisualisation expirée, relancer « Prévisualiser »")
    strt2t = body.t2t_model or si.strt2tmodel
    strprompt = si.f_build_t2t_prompt(dctprep["name"], dctprep["source_text"],
                                      dctprep["representation"], dctprep["item_class"])
    _check_budget(_t2t_estimate(strt2t, si.T2T_SYSTEM, strprompt))
    try:
        dctdesc = si.f_describe_text(dctprep["name"], dctprep["source_text"],
                                     dctprep["representation"], dctprep["item_class"],
                                     intdryrun=intlabdryrun, strmodel=strt2t)
    except Exception as err:
        raise HTTPException(status_code=502, detail="appel T2T en échec : {0}".format(err))
    if not (dctdesc.get("text") or "").strip():
        raise HTTPException(status_code=502, detail="le modèle n'a rendu aucune description (refus ?)")
    with DBLOCK:
        iddescription = si.f_record_description(dctprep, dctdesc)
    return {"id_description": iddescription, "text": dctdesc["text"], "cost": dctdesc["cost"],
            "input_tokens": dctdesc["input_tokens"], "output_tokens": dctdesc["output_tokens"],
            "seconds": dctdesc["seconds"], "t2i_prompt": si.f_build_t2i_prompt(dctdesc["text"])}


class RenderIn(BaseModel):
    id_description: int
    text: str = ""                  # the description, possibly corrected by hand
    t2i_model: str = ""
    n: int = 1


@app.post("/api/render")
def render(body: RenderIn):
    arrdesc = _get_description(body.id_description)
    if not arrdesc:
        raise HTTPException(status_code=404, detail="description introuvable")
    strt2i = body.t2i_model or si.strt2imodel
    if strt2i in si.T2I_RETIRED:
        raise HTTPException(status_code=400, detail="modèle retiré, utiliser {0}".format(si.T2I_RETIRED[strt2i]))
    lngn = max(1, min(int(body.n or 1), lnglabmaxrenders))
    _check_budget(lngn * si.f_t2i_cost(strt2i))
    dctprep = _prep_from_description(arrdesc)
    strtext = (body.text or "").strip() or arrdesc["OBJECT_DESCRIPTION"]
    iddescription = arrdesc["ID_SYNTHETIC_DESCRIPTION"]
    dctdesc = {"text": strtext, "model": arrdesc["T2T_LLM"]}
    if strtext != (arrdesc["OBJECT_DESCRIPTION"] or "").strip():
        # A corrected description is a description of its own (zero cost, flagged as edited),
        # so every candidate stays traceable to the exact text it was rendered from.
        dctedited = {"text": strtext, "model": arrdesc["T2T_LLM"], "is_edited": 1,
                     "system": arrdesc["T2T_SYSTEM"], "prompt": arrdesc["T2T_PROMPT"],
                     "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "seconds": 0.0}
        with DBLOCK:
            iddescription = si.f_record_description(dctprep, dctedited)
        dctdesc = dctedited
    with DBLOCK:
        arrindexes = si.f_next_candidate_indexes(dctprep["identity"], dctprep["representation"], lngn)
    strjob = uuid.uuid4().hex[:12]
    with JOBSLOCK:
        JOBS[strjob] = {"status": "running", "model": strt2i, "n": lngn, "results": [],
                        "started": time.time(), "id_description": iddescription,
                        "item_class": dctprep["item_class"], "id_item": dctprep["id_item"]}
    EXECUTOR.submit(_run_job, strjob, dctprep, dctdesc, iddescription, arrindexes, strt2i)
    return {"job_id": strjob, "candidate_indexes": arrindexes, "id_description": iddescription}


def _run_job(strjob, dctprep, dctdesc, iddescription, arrindexes, strt2i):
    """Renders in parallel (no lock), each candidate persisted under the lock as soon as it lands."""
    import random
    try:
        with ThreadPoolExecutor(max_workers=len(arrindexes)) as pool:
            dctfutures = {pool.submit(si.f_render_bytes, dctdesc["text"], strt2i,
                                      random.randint(1, 2147483646), intlabdryrun): lngindex
                          for lngindex in arrindexes}
            for future in as_completed(dctfutures):
                lngindex = dctfutures[future]
                try:
                    dctrender = future.result()
                    with DBLOCK:
                        dctres = si.f_persist_candidate(dctprep, dctdesc, iddescription, lngindex, dctrender)
                except Exception as err:
                    dctres = {"status": "failed", "candidate_index": lngindex, "failure_reason": str(err),
                              "cost": 0.0, "model": strt2i}
                dctres.pop("bytes", None)
                dctres["url"] = _image_url(dctres.get("image_path"))
                with JOBSLOCK:
                    JOBS[strjob]["results"].append(dctres)
        with JOBSLOCK:
            JOBS[strjob]["status"] = "done"
    except Exception as err:
        with JOBSLOCK:
            JOBS[strjob]["status"] = "failed"
            JOBS[strjob]["error"] = str(err)


@app.get("/api/jobs/{strjob}")
def job(strjob: str):
    with JOBSLOCK:
        dctjob = JOBS.get(strjob)
        if not dctjob:
            raise HTTPException(status_code=404, detail="travail inconnu (service redémarré ?)")
        dctout = dict(dctjob, results=list(dctjob["results"]),
                      elapsed=round(time.time() - dctjob["started"], 1))
    return dctout


# ---------------------------------------------------------------------------
# The review: one row per entity, candidates side by side, choose
# ---------------------------------------------------------------------------
@app.get("/api/candidates")
def candidates(item_class: str, q: str = "", limit: int = 30, only: str = "all", id_item: int = 0):
    """only = all | with (has candidates) | unchosen (has candidates, no manual choice)."""
    if item_class not in si.ENTITY_CATALOG:
        raise HTTPException(status_code=400, detail="classe inconnue")
    with DBLOCK:
        if id_item:
            arrentities = si.f_search_entities(item_class, arrids=[id_item], lnglimit=1)
        else:
            arrentities = si.f_search_entities(item_class, q, 500 if only != "all" else limit)
        dctcand = si.f_list_candidates(item_class, [e["id_item"] for e in arrentities])
    arrrows = []
    for arrentity in arrentities:
        dctc = dctcand.get(arrentity["id_item"], {"candidates": [], "chosen": None, "manual": 0})
        if only in ("with", "unchosen") and not dctc["candidates"]:
            continue
        if only == "unchosen" and dctc["manual"]:
            continue
        for arrcand in dctc["candidates"]:
            arrcand["url"] = _image_url(arrcand.get("IMAGE_PATH"))
        arrrows.append({"entity": arrentity, "candidates": dctc["candidates"],
                        "chosen": dctc["chosen"], "manual": dctc["manual"]})
        if len(arrrows) >= limit:
            break
    return {"rows": arrrows}


class ChooseIn(BaseModel):
    id_synthetic_image: int


@app.post("/api/choose")
def choose(body: ChooseIn):
    with DBLOCK:
        arrimage = si.f_get_image(body.id_synthetic_image)
        if not arrimage:
            raise HTTPException(status_code=404, detail="image introuvable")
        if arrimage["STATUS"] != "generated" or not arrimage["IS_VALIDATED"]:
            raise HTTPException(status_code=400, detail="seule une image valide peut être choisie")
        if not arrimage["ITEM_CLASS"]:
            raise HTTPException(status_code=400, detail="image sans entité (migration 03 non passée ?)")
        idrow, _ = si.f_set_entity_choice(
            arrimage["ITEM_CLASS"], arrimage["ID_ITEM"], arrimage["ID_WIKIDATA"],
            arrimage["REPRESENTATION"], arrimage["ID_SYNTHETIC_IMAGE"], intmanual=1)
    return {"ok": True, "id_row": idrow}


if __name__ == "__main__":
    import uvicorn
    print("[lab] http://{0}:{1}/  (dry-run={2}, daily budget ${3:.2f})".format(
        strlabhost, lnglabport, bool(intlabdryrun), dbllabbudget), flush=True)
    uvicorn.run(app, host=strlabhost, port=lnglabport)
