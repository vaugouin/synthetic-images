"""
synthetic_images_functions -- two-stage synthetic-illustration pipeline.

Project A of the "An image for everything" plan (see doc/). For one entity it:
  1. resolves a text description of the object (Wikipedia/overview -> web-search fallback),
  2. text-to-text: turns that into a pure OBJECT DESCRIPTION (no style words),
  3. text-to-image: renders a style-locked 2:3 WebP via the locked template,
  4. validates (aspect ratio / integrity), stores the master, and records provenance
     in T_WC_T2S_SYNTHETIC_IMAGE + the entity->image mapping T_WC_T2S_ENTITY_IMAGE.

KEYSTONE RULE (review §2): style + aspect ratio + resolution live ONLY in the
text-to-image template here. The per-item LLM output stays a pure object description.
Do not move style words into the description, or visual consistency drifts.

Model calls (f_text_to_text, f_text_to_image) are intentionally thin and pluggable:
the bake-off (doc/eval-plan.md) picks the final models. Everything runs in --dry-run
without any API key so the single-item harness is usable immediately.
"""
import os
import io
import time
import hashlib
from datetime import datetime

import pytz

import citizenphil as cp

# ---------------------------------------------------------------------------
# Config (from .env, loaded by citizenphil)
# ---------------------------------------------------------------------------
strsqlns = os.environ.get("DB_NAMESPACE", "T_WC_")
strstyleversion = os.environ.get("STYLE_VERSION", "v1")
strt2tpromptversion = os.environ.get("T2T_PROMPT_VERSION", "v1")
strt2ipromptversion = os.environ.get("T2I_PROMPT_VERSION", "v1")
strt2tmodel = os.environ.get("T2T_MODEL", "claude-haiku-4-5-20251001")
strt2imodel = os.environ.get("T2I_MODEL", "black-forest-labs/flux-schnell")
straspectratio = os.environ.get("ASPECT_RATIO", "2:3")
lngmasterwidth = int(os.environ.get("MASTER_WIDTH", "1024"))
lngmasterheight = int(os.environ.get("MASTER_HEIGHT", "1536"))
strshareddatadir = os.environ.get("SHARED_DATA_DIR", "/shared")
strsubdir = os.environ.get("SYNTHETIC_IMAGE_SUBDIR", "synthetic-images")
strtimezone = os.environ.get("USER_TIMEZONE", "Europe/Paris")

# Locked text-to-image style template (source doc "Text-to-image prompt template").
# The single {object_description} slot is the ONLY per-item variation; everything
# else is frozen so the whole corpus renders as one coherent set.
STYLE_TEMPLATE = (
    "Illustrated diagram for a visual dictionary. "
    "Flat lighting, plain off-white textured paper background, encyclopedic "
    "technical-illustration style, soft realistic shading, educational reference "
    "aesthetic, minimalist composition. "
    "1990s printed visual dictionary style, Jean-Claude Corbeil aesthetic. "
    "Aspect ratio " + straspectratio + ". "
    "{object_description}"
)


# ---------------------------------------------------------------------------
# Identity / hashing
# ---------------------------------------------------------------------------
def f_identity(stridwikidata, stritemclass, lngiditem):
    """Primary image identity: ID_WIKIDATA when present, else CLASS:ID (review §4.2/#14)."""
    if stridwikidata:
        return str(stridwikidata)
    return "{0}:{1}".format(stritemclass or "", lngiditem if lngiditem is not None else "")


def f_image_key(stridentity, strrepresentation, strstyleversion_):
    """
    Content/parameter hash. Idempotency keys on (IMAGE_KEY, STYLE_VERSION) (review §6),
    so a style bump produces a new key and the old image can be mass-invalidated.
    """
    strraw = "|".join([stridentity, strrepresentation or "", strstyleversion_ or ""])
    return hashlib.sha256(strraw.encode("utf-8")).hexdigest()[:32]


def f_now():
    return datetime.now(pytz.timezone(strtimezone)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Stage 0 -- resolve a source description for the entity
# ---------------------------------------------------------------------------
def f_resolve_source_text(stridwikidata, strname, stroverview):
    """
    Return (strtext, strsource). Prefer the entity OVERVIEW / Wikipedia text already in
    the DB; fall back to web search (Tavily, review §7) for the rare label-less long tail.

    Wikipedia full-section retrieval (reusing the front-end "hidden sections" logic with a
    token budget) is a TODO for the production build; the OVERVIEW column is enough to start.
    """
    if stroverview:
        return stroverview, "wikipedia"
    # TODO(bake-off+): pull richer Wikipedia sections by ID_WIKIDATA with a token budget.
    strsearched = f_web_search_fallback(strname)
    if strsearched:
        return strsearched, "websearch"
    return strname or "", "model-knowledge"


def f_web_search_fallback(strquery):
    """Tavily web-search fallback (review §7). Returns cleaned text or '' when unavailable."""
    strkey = os.environ.get("TAVILY_API_KEY", "")
    if not strkey or not strquery:
        return ""
    try:
        import requests
        rsp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": strkey, "query": strquery, "include_answer": True, "max_results": 3},
            timeout=30,
        )
        data = rsp.json()
        return data.get("answer") or ""
    except Exception as err:  # broad by house convention; surface via messages
        print("  [websearch] fallback failed: {0}".format(err))
        return ""


# ---------------------------------------------------------------------------
# Stage 1 -- text-to-text: source text -> pure OBJECT DESCRIPTION (no style words)
# ---------------------------------------------------------------------------
def f_text_to_text(strname, strsourcetext, strrepresentation, strclass, intdryrun=0):
    """
    Produce the object description fed verbatim into the image template. The output must
    describe ONLY the subject/object (review §2) -- never style, palette, or aspect ratio.

    Pluggable: wire the bake-off winner (Anthropic/OpenAI/Gemini) here. Dry-run returns a
    deterministic stub so the harness works with no API key.
    """
    if intdryrun:
        return "A clear central depiction of {0} ({1}), rendered as a {2}.".format(
            strname, strclass, strrepresentation
        )
    # TODO(bake-off): call _call_t2t_llm(prompt) with the chosen model + T2T_PROMPT_VERSION.
    strprompt = (
        "From the reference text below, write ONE concise visual object description of "
        "'{0}' suitable as the subject of an encyclopedic illustration. Describe only the "
        "object/scene and its concrete visual attributes. Do NOT mention art style, colors "
        "palette, lighting, background, or aspect ratio. Representation: {1}.\n\n"
        "Reference:\n{2}"
    ).format(strname, strrepresentation, (strsourcetext or "")[:6000])
    return _call_t2t_llm(strprompt)


def _call_t2t_llm(strprompt):
    """Private LLM call for the description stage. Returns text. TODO: implement per bake-off."""
    raise NotImplementedError(
        "Text-to-text model not wired yet. Run with --dry-run, or implement _call_t2t_llm "
        "for the model selected by doc/eval-plan.md (T2T_MODEL)."
    )


# ---------------------------------------------------------------------------
# Stage 2 -- text-to-image: object description -> style-locked image bytes
# ---------------------------------------------------------------------------
def f_text_to_image(strobjectdescription, lngseed=None, intdryrun=0):
    """
    Render the locked template + object description to image bytes (PNG/WebP). Returns
    (bytesimage, lngseed, dblcost). Dry-run returns a generated placeholder so storage and
    validation can be exercised end-to-end offline.
    """
    strprompt = STYLE_TEMPLATE.format(object_description=strobjectdescription)
    if intdryrun:
        return _placeholder_image(), (lngseed or 0), 0.0
    # FLUX.1 schnell on Replicate -- the bake-off front-runner (review §8).
    try:
        import replicate
        arrout = replicate.run(
            strt2imodel,
            input={
                "prompt": strprompt,
                "aspect_ratio": straspectratio,
                "output_format": "webp",
                "seed": lngseed if lngseed is not None else 0,
            },
        )
        bytesimage = _read_replicate_output(arrout)
        return bytesimage, (lngseed or 0), 0.006  # approx FLUX schnell unit cost (review §8)
    except Exception as err:
        print("  [t2i] generation failed: {0}".format(err))
        return None, (lngseed or 0), 0.0


def _read_replicate_output(arrout):
    """Replicate returns a file-like / URL list depending on model + client version."""
    import requests
    item = arrout[0] if isinstance(arrout, (list, tuple)) else arrout
    if hasattr(item, "read"):
        return item.read()
    return requests.get(str(item), timeout=60).content


def _placeholder_image():
    """A valid 2:3 WebP so dry-run exercises validation + storage without any API."""
    from PIL import Image
    img = Image.new("RGB", (lngmasterwidth, lngmasterheight), (245, 243, 238))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=82)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Validation gate (review §6) + storage
# ---------------------------------------------------------------------------
def f_validate_image(bytesimage):
    """
    Return (intok, strreason, lngwidth, lngheight). Checks file integrity + aspect ratio.
    A vision-LLM sanity score ("is this an on-topic 2:3 Corbeil plate?") is a later add (§6).
    """
    if not bytesimage:
        return 0, "empty image", 0, 0
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(bytesimage))
        img.verify()
        img = Image.open(io.BytesIO(bytesimage))
        w, h = img.size
    except Exception as err:
        return 0, "unreadable: {0}".format(err), 0, 0
    dblratio = (w / h) if h else 0
    dbltarget = 2 / 3
    if abs(dblratio - dbltarget) > 0.02:
        return 0, "aspect ratio {0:.3f} != 2:3".format(dblratio), w, h
    return 1, "", w, h


def f_store_image(bytesimage, stritemclass, strimagekey):
    """Write the master WebP to shared_data/<subdir>/<class>/<hash>.webp; return (path, size)."""
    strdir = os.path.join(strshareddatadir, strsubdir, stritemclass or "misc")
    os.makedirs(strdir, exist_ok=True)
    strfilename = "{0}.webp".format(strimagekey)
    strfullpath = os.path.join(strdir, strfilename)
    with open(strfullpath, "wb") as fh:
        fh.write(bytesimage)
    # Stored path is relative to the shared root -- that is what the API/Apache serve (review §10).
    strrelpath = "{0}/{1}/{2}".format(strsubdir, stritemclass or "misc", strfilename)
    return strrelpath, len(bytesimage)


# ---------------------------------------------------------------------------
# DB persistence -- image artifact + entity->image mapping (review §5)
# ---------------------------------------------------------------------------
def f_record_image(arrimage, arrmapping):
    """
    Upsert one T_WC_T2S_SYNTHETIC_IMAGE row (unique on IMAGE_KEY) and its
    T_WC_T2S_ENTITY_IMAGE mapping row (unique on the occurrence). Uses the shared
    citizenphil layer; standard audit fields are added automatically.
    """
    cp.f_sqlbulkupsert(strsqlns + "T2S_SYNTHETIC_IMAGE", [arrimage], ["IMAGE_KEY"], 1)
    idimage = cp.f_fieldfromquery(
        "SELECT ID_SYNTHETIC_IMAGE FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE WHERE IMAGE_KEY=%s",
        "ID_SYNTHETIC_IMAGE", params=(arrimage["IMAGE_KEY"],),
    )
    if idimage:
        arrmapping["ID_SYNTHETIC_IMAGE"] = idimage
        cp.f_sqlbulkupsert(
            strsqlns + "T2S_ENTITY_IMAGE", [arrmapping],
            ["ITEM_CLASS", "ID_ITEM", "ID_WIKIDATA", "REPRESENTATION"], 1,
        )
    return idimage


def f_existing_image_key(strimagekey, strstyleversion_):
    """Idempotency probe: has this (IMAGE_KEY, STYLE_VERSION) already been generated?"""
    return cp.f_fieldfromquery(
        "SELECT ID_SYNTHETIC_IMAGE FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE "
        "WHERE IMAGE_KEY=%s AND STYLE_VERSION=%s AND (DELETED IS NULL OR DELETED=0)",
        "ID_SYNTHETIC_IMAGE", params=(strimagekey, strstyleversion_),
    )


# ---------------------------------------------------------------------------
# The single-item harness (review §2 "primary dev loop")
# ---------------------------------------------------------------------------
def f_generate_synthetic_image(
    stridwikidata=None, stritemclass=None, lngiditem=None, strname="",
    stroverview="", strrepresentation="plate", lngseed=None,
    intdryrun=0, intforce=0,
):
    """
    Generate (or resume) one synthetic illustration for a single entity occurrence.

    Returns a result dict with STATUS and the IDs/paths involved. This is the function the
    batch driver loops over AND the function used to test one item with explicit params
    (the source doc's required test harness).
    """
    arrmessages = []
    stridentity = f_identity(stridwikidata, stritemclass, lngiditem)
    strimagekey = f_image_key(stridentity, strrepresentation, strstyleversion)

    # 1. Idempotency / resume (review §6): skip unless forced.
    if not intforce:
        idexisting = f_existing_image_key(strimagekey, strstyleversion)
        if idexisting:
            return {"status": "skipped", "reason": "exists", "image_key": strimagekey,
                    "id_synthetic_image": idexisting, "messages": ["already generated"]}

    # 2. Stage 0 -> source text, Stage 1 -> object description.
    strsourcetext, strsource = f_resolve_source_text(stridwikidata, strname, stroverview)
    arrmessages.append("source={0}".format(strsource))
    strobjectdescription = f_text_to_text(
        strname, strsourcetext, strrepresentation, stritemclass, intdryrun=intdryrun
    )

    # 3. Stage 2 -> image bytes.
    dblstart = time.time()
    bytesimage, lngseedused, dblcost = f_text_to_image(
        strobjectdescription, lngseed=lngseed, intdryrun=intdryrun
    )
    dblelapsed = round(time.time() - dblstart, 3)

    # 4. Validation gate.
    intok, strreason, w, h = f_validate_image(bytesimage)
    strstatus = "generated" if intok else "failed"
    strrelpath, lngsize = ("", 0)
    if intok:
        strrelpath, lngsize = f_store_image(bytesimage, stritemclass, strimagekey)
    else:
        arrmessages.append("validation: {0}".format(strreason))

    # 5. Persist provenance + mapping.
    arrimage = {
        "IMAGE_KEY": strimagekey,
        "ID_WIKIDATA": stridwikidata or None,
        "REPRESENTATION": strrepresentation,
        "IMAGE_PATH": strrelpath or None,
        "WIDTH": w or None, "HEIGHT": h or None,
        "ASPECT_RATIO": straspectratio, "FORMAT": "webp", "FILE_SIZE": lngsize or None,
        "OBJECT_DESCRIPTION": strobjectdescription,
        "T2T_LLM": strt2tmodel, "T2T_PROMPT_VERSION": strt2tpromptversion,
        "T2I_MODEL": strt2imodel, "T2I_PROMPT_VERSION": strt2ipromptversion,
        "T2I_SEED": lngseedused, "STYLE_VERSION": strstyleversion,
        "SOURCE": strsource,
        "STATUS": strstatus, "IS_VALIDATED": 1 if intok else 0,
        "FAILURE_REASON": (strreason or None) if not intok else None,
        "GENERATION_COST": dblcost, "GENERATION_TIME": dblelapsed,
        "TIM_GENERATED": f_now(),
    }
    arrmapping = {
        "ITEM_CLASS": stritemclass, "ID_ITEM": lngiditem, "ID_WIKIDATA": stridwikidata or None,
        "REPRESENTATION": strrepresentation, "IS_CHOSEN": 1,
    }

    idimage = None
    if intdryrun:
        arrmessages.append("dry-run: not persisted")
    else:
        idimage = f_record_image(arrimage, arrmapping)

    return {
        "status": strstatus, "image_key": strimagekey, "id_synthetic_image": idimage,
        "image_path": strrelpath, "cost": dblcost, "seconds": dblelapsed,
        "object_description": strobjectdescription, "messages": arrmessages,
    }
