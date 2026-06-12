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
# SHARED_DATA_DIR = container WRITE root. The mount is narrow
# (`-v .../shared_data/synthetic-images:/shared`) so /shared already IS the synthetic-images
# folder -- the container cannot see the rest of shared_data. SYNTHETIC_IMAGE_SUBDIR is NOT a
# physical subdir under it; it is the URL prefix Apache (serving from the real shared_data root,
# one level up) prepends. See f_store_image for why the two must stay decoupled.
# Approximate per-image unit cost for budget tracking (review §8) -- VERIFY current pricing.
T2I_COST = {
    "black-forest-labs/flux-schnell": 0.006,
    "google/nano-banana": 0.039,
    "gemini-2.5-flash-image": 0.039,        # Nano Banana
    "gemini-3-pro-image-preview": 0.145,    # Nano Banana Pro -- MEASURED: EUR 4.93 / 34 imgs (2026-06-10)
}
strshareddatadir = os.environ.get("SHARED_DATA_DIR", "/shared")
strsubdir = os.environ.get("SYNTHETIC_IMAGE_SUBDIR", "synthetic-images")
strtimezone = os.environ.get("USER_TIMEZONE", "Europe/Paris")
# Logo padding (decision #7): TMDb image CDN base for the real company/network logos. The last
# path segment is the TMDb size; LOGO_RASTER_SIZE is the SIZED variant used for .svg logos,
# because the CDN only rasterizes SVG assets at sized endpoints (original serves raw SVG,
# which Pillow cannot read).
strlogobaseurl = os.environ.get("LOGO_BASE_URL", "https://image.tmdb.org/t/p/original")
strlogorastersize = os.environ.get("LOGO_RASTER_SIZE", "w500")
# Shared off-white canvas: identical for synthetic plates (dry-run placeholder) and padded
# logos so the whole corpus reads as one set against the card grid.
CANVAS_RGB = (245, 243, 238)

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

# Wikipedia section titles excluded from the T2T source text (reference/navigation boilerplate,
# both EN and FR). KEPT IN SYNC with f_wikipediallsections() in
# tmdb-front/lib/global-light.inc.php -- if that list changes, change this one too.
WIKIPEDIA_SECTION_EXCLUDE = (
    "References", "See also", "External links", "Notes and references",
    "Références", "Voir aussi", "Liens externes", "Notes et références",
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
def f_resolve_source_text(stridwikidata, strname, stroverview, intdryrun=0):
    """
    Return (strtext, strsource). Source priority, most-curated first (review §4.2 / §7):
      1. an explicit OVERVIEW override (what the batch selector already passes) -> 'wikipedia'
      2. curated DB text keyed on ID_WIKIDATA: Wikipedia article sections, else the Wikidata
         item description (f_db_source_text) -> 'wikipedia' / 'wikidata'
      3. Tavily web search for the genuine label-less long tail -> 'websearch'
      4. the entity name alone -> 'model-knowledge'

    Web search is the EXCEPTION, not the default -- most entities resolve from the DB. Dry-run
    short-circuits to the name so the offline harness never touches the DB or any API.
    """
    if stroverview:
        return stroverview, "wikipedia"
    if intdryrun:
        return strname or "", "dry-run"
    strdbtext, strdbsource = f_db_source_text(stridwikidata)
    if strdbtext:
        return strdbtext, strdbsource
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


def f_db_source_text(stridwikidata, struilang="en", lngmaxchars=6000):
    """
    Curated source text from the read-model, keyed on ID_WIKIDATA. Returns (text, source) or
    ("", ""). English is canonical for generation (decision #11). Priority:
      1. Wikipedia article sections (T_WC_WIKIPEDIA_PAGE_LANG_SECTION) in DISPLAY_ORDER (intro
         first), boilerplate sections dropped, concatenated to a char budget -- the same
         grounding the front-end's f_wikipediallsections serves.
      2. Wikidata item label + description (T_WC_WIKIDATA_ITEM_V1) -- the §4.2 generic resolver.
    """
    if not stridwikidata:
        return "", ""
    # 1. Wikipedia sections, intro first, minus reference/see-also boilerplate, to a char budget.
    try:
        strexcl = ",".join(["%s"] * len(WIKIPEDIA_SECTION_EXCLUDE))
        cur = cp.f_getconnection().cursor()
        cur.execute(
            "SELECT CONTENT FROM " + strsqlns + "WIKIPEDIA_PAGE_LANG_SECTION "
            "WHERE ID_WIKIDATA=%s AND LANG=%s AND TITLE NOT IN (" + strexcl + ") "
            "ORDER BY DISPLAY_ORDER",
            (stridwikidata, struilang) + WIKIPEDIA_SECTION_EXCLUDE,
        )
        arrparts, lngtotal = [], 0
        for arr in (cur.fetchall() or []):
            strcontent = (arr.get("CONTENT") or "").strip()
            if not strcontent:
                continue
            arrparts.append(strcontent)
            lngtotal += len(strcontent)
            if lngtotal >= lngmaxchars:
                break
        if arrparts:
            return "\n\n".join(arrparts)[:lngmaxchars], "wikipedia"
    except Exception as err:
        print("  [source] wikipedia lookup failed for {0}: {1}".format(stridwikidata, err))
    # 2. Wikidata item label + description.
    try:
        cur = cp.f_getconnection().cursor()
        cur.execute(
            "SELECT LABEL, DESCRIPTION FROM " + strsqlns + "WIKIDATA_ITEM_V1 "
            "WHERE ID_WIKIDATA=%s AND LANG=%s LIMIT 1",
            (stridwikidata, struilang),
        )
        arr = cur.fetchone()
        strdesc = (arr.get("DESCRIPTION") or "").strip() if arr else ""
        if strdesc:
            strlabel = (arr.get("LABEL") or "").strip()
            return (strlabel + ": " + strdesc) if strlabel else strdesc, "wikidata"
    except Exception as err:
        print("  [source] wikidata lookup failed for {0}: {1}".format(stridwikidata, err))
    return "", ""


# Per-class read-model source tables. Every T2S entity table follows the same shape
# (ID_WIKIDATA / {CLASS}_NAME / {CLASS}_NAME_FR / OVERVIEW); technical is the legacy exception.
# Maps item_class -> (table suffix, name columns matched case-insensitively).
ENTITY_SOURCE_TABLES = {
    "technical":  ("T2S_TECHNICAL",  ("WIKIDATA_LABEL", "DESCRIPTION", "DESCRIPTION_FR")),
    "movement":   ("T2S_MOVEMENT",   ("MOVEMENT_NAME", "MOVEMENT_NAME_FR")),
    "award":      ("T2S_AWARD",      ("AWARD_NAME", "AWARD_NAME_FR")),
    "group":      ("T2S_GROUP",      ("GROUP_NAME", "GROUP_NAME_FR")),
    "collection": ("T2S_COLLECTION", ("COLLECTION_NAME", "COLLECTION_NAME_FR")),
    "death":      ("T2S_DEATH",      ("DEATH_NAME", "DEATH_NAME_FR")),
    "list":       ("T2S_LIST",       ("LIST_NAME", "LIST_NAME_FR")),
    "nomination": ("T2S_NOMINATION", ("NOMINATION_NAME", "NOMINATION_NAME_FR")),
}


def f_lookup_entity_table(stritemclass, stridwikidata):
    """
    Look up a curated entity row in its class T2S table BY ID_WIKIDATA (exact) and return the
    DB's NATIVE (name, overview, table). The Q-id is the stable identity (decision #14): the
    harness pins the Q-id, the DB supplies the canonical name + OVERVIEW -- exactly the data
    production reads forward from the table, with no typed-name or homonym matching.
    The primary (English) name column is the first entry in the class's column tuple.
    Returns ("", "", "") when the class has no table or no row carries that Q-id.
    """
    if not stridwikidata:
        return "", "", ""
    arrtable = ENTITY_SOURCE_TABLES.get((stritemclass or "").lower())
    if not arrtable:
        return "", "", ""
    strtable, arrcols = arrtable
    strnamecol = arrcols[0]
    try:
        cur = cp.f_getconnection().cursor()
        cur.execute(
            "SELECT " + strnamecol + " AS NAME, OVERVIEW FROM " + strsqlns + strtable +
            " WHERE ID_WIKIDATA=%s AND (DELETED IS NULL OR DELETED=0) LIMIT 1",
            (stridwikidata,),
        )
        arr = cur.fetchone()
        if arr:
            return (arr.get("NAME") or "").strip(), (arr.get("OVERVIEW") or "").strip(), strtable
    except Exception as err:
        print("  [resolve] {0} lookup failed for {1}: {2}".format(strtable, stridwikidata, err))
    return "", "", ""


def f_lookup_wikidata_name(stridwikidata, struilang="en"):
    """
    Native Wikidata-item label for a Q-id (T_WC_WIKIDATA_ITEM_V1, EN) -> name or "". The by-Q-id
    counterpart of f_lookup_entity_table for the classes with no T2S table (locations /
    occupations / characters / countries) -- gives them DB-native names too.
    """
    if not stridwikidata:
        return ""
    try:
        cur = cp.f_getconnection().cursor()
        cur.execute(
            "SELECT LABEL FROM " + strsqlns + "WIKIDATA_ITEM_V1 "
            "WHERE ID_WIKIDATA=%s AND LANG=%s LIMIT 1",
            (stridwikidata, struilang),
        )
        arr = cur.fetchone()
        if arr and (arr.get("LABEL") or "").strip():
            return arr.get("LABEL").strip()
    except Exception as err:
        print("  [resolve] wikidata-name lookup failed for {0}: {1}".format(stridwikidata, err))
    return ""


def f_lookup_wikidata_label(strname):
    """
    Generic Wikidata-item match by exact label (T_WC_WIKIDATA_ITEM_V1, EN) -> id_wikidata or "".
    LAST RESORT ONLY: a bare label is homonym-prone -- 'Paris' resolves to the Trojan prince as
    readily as the city, 'Academy Award' to a radio show -- so callers MUST prefer a curated
    table match or a pinned golden-set Q-id over this, never let it override them.
    """
    if not strname:
        return ""
    try:
        cur = cp.f_getconnection().cursor()
        cur.execute(
            "SELECT ID_WIKIDATA FROM " + strsqlns + "WIKIDATA_ITEM_V1 "
            "WHERE LANG=%s AND LOWER(TRIM(LABEL))=LOWER(TRIM(%s)) LIMIT 1",
            ("en", strname),
        )
        arr = cur.fetchone()
        if arr and (arr.get("ID_WIKIDATA") or "").strip():
            return arr.get("ID_WIKIDATA").strip()
    except Exception as err:
        print("  [resolve] wikidata-item lookup failed for '{0}': {1}".format(strname, err))
    return ""


# ---------------------------------------------------------------------------
# Stage 1 -- text-to-text: source text -> pure OBJECT DESCRIPTION (no style words)
# ---------------------------------------------------------------------------
def f_text_to_text(strname, strsourcetext, strrepresentation, strclass, intdryrun=0, strmodel=None):
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
    strprompt = (
        "You are preparing the central subject for an encyclopedic visual-dictionary plate "
        "of '{0}'.\n"
        "Write ONE concise description (2-3 sentences) of a TANGIBLE, recognizable subject "
        "that represents it: the physical object, device, material, or characteristic "
        "real-world scene most associated with '{0}'. For a film/cinema technical, prefer "
        "the equipment or film material itself (e.g. a reel or strip of film with visible "
        "imagery on the frames, a camera, a projector). The subject must fill the "
        "composition as a concrete thing a person could photograph, with specific named "
        "parts and a clear arrangement.\n"
        "Representation hint: '{1}'. If it names a concrete depiction (flag, map, landscape, "
        "portrait), draw that; if it is generic (e.g. 'plate'), depict the most iconic "
        "tangible form of the subject.\n"
        "Do NOT describe it as proportions, ratios, dimensions, measurements, an empty "
        "frame, a chart, or a diagram. Do NOT mention art style, color palette, lighting, "
        "background, or aspect ratio.\n\n"
        "Reference:\n{2}"
    ).format(strname, strrepresentation, (strsourcetext or "")[:6000])
    return _call_t2t_llm(strprompt, strmodel or strt2tmodel)


def _call_t2t_llm(strprompt, strmodel):
    """
    LLM call for the description stage. Dispatches by model family and returns plain text.
    Phase-1 wires Anthropic (claude-*); the OpenAI/Gemini branches are added during the
    eval-plan.md bake-off (the keys are already in .env). Returns text only -- no preamble.
    """
    if (strmodel or "").lower().startswith("claude"):
        return _call_anthropic(strprompt, strmodel)
    raise NotImplementedError(
        "Text-to-text provider for model '{0}' not wired yet. Phase-1 wires Anthropic "
        "(claude-*); add the OpenAI/Gemini branch when the bake-off needs it.".format(strmodel)
    )


def _call_anthropic(strprompt, strmodel):
    """Anthropic Messages API call. Reads ANTHROPIC_API_KEY from the environment (.env)."""
    from anthropic import Anthropic
    client = Anthropic()
    msg = client.messages.create(
        model=strmodel,
        max_tokens=1024,
        system=("You write concise, literal visual object descriptions for an encyclopedic "
                "illustration pipeline. Respond with the description only -- no preamble, no "
                "style or color words, no aspect ratio."),
        messages=[{"role": "user", "content": strprompt}],
    )
    arrparts = [block.text for block in msg.content if getattr(block, "type", "") == "text"]
    return "\n".join(arrparts).strip()


# ---------------------------------------------------------------------------
# Stage 2 -- text-to-image: object description -> style-locked image bytes
# ---------------------------------------------------------------------------
def f_text_to_image(strobjectdescription, lngseed=None, intdryrun=0, strmodel=None):
    """
    Render the locked template + object description to WebP bytes. Returns (bytesimage, seed,
    cost). Dispatches by provider: Gemini image models (Nano Banana / Nano Banana Pro) go through
    the Google GenAI SDK; everything else is a Replicate model (FLUX). Dry-run returns a
    placeholder so storage + validation run offline. strmodel overrides T2I_MODEL (bake-off).
    """
    strt2i = strmodel or strt2imodel
    strprompt = STYLE_TEMPLATE.format(object_description=strobjectdescription)
    if intdryrun:
        return _placeholder_image(), (lngseed or 0), 0.0
    if strt2i.startswith("gemini") or "nano-banana" in strt2i:
        return _t2i_gemini(strprompt, strt2i, lngseed)
    return _t2i_replicate(strprompt, strt2i, lngseed)


def _t2i_replicate(strprompt, strmodel, lngseed):
    """Replicate-hosted model (FLUX.1 schnell, review §8). Returns (webp_bytes, seed, cost)."""
    try:
        import replicate
        arrout = replicate.run(
            strmodel,
            input={
                "prompt": strprompt,
                "aspect_ratio": straspectratio,
                "output_format": "webp",
                "seed": lngseed if lngseed is not None else 0,
            },
        )
        return _read_replicate_output(arrout), (lngseed or 0), T2I_COST.get(strmodel, 0.006)
    except Exception as err:
        print("  [t2i] replicate generation failed ({0}): {1}".format(strmodel, err))
        return None, (lngseed or 0), 0.0


def _t2i_gemini(strprompt, strmodel, lngseed):
    """
    Google Gemini image generation -- Nano Banana (gemini-2.5-flash-image) / Nano Banana Pro
    (gemini-3-pro-image-preview). Uses GEMINI_API_KEY. The image API has NO seed control, so the
    seed is recorded but not enforced (determinism is FLUX-only). Output (PNG) is normalised to a
    2:3 WebP master for parity with the other providers. Returns (webp_bytes, seed, cost).
    """
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        rsp = client.models.generate_content(
            model=strmodel,
            contents=strprompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=straspectratio,
                    image_size=os.environ.get("GEMINI_IMAGE_SIZE", "2K"),
                ),
            ),
        )
        for part in rsp.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return _to_webp(part.inline_data.data), (lngseed or 0), T2I_COST.get(strmodel, 0.04)
        print("  [t2i] gemini returned no image for {0} (safety block / refusal?)".format(strmodel))
        return None, (lngseed or 0), 0.0
    except Exception as err:
        print("  [t2i] gemini generation failed ({0}): {1}".format(strmodel, err))
        return None, (lngseed or 0), 0.0


def _to_webp(bytesimage):
    """Normalise provider output (PNG/JPEG) to a WebP master."""
    from PIL import Image
    img = Image.open(io.BytesIO(bytesimage)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=90)
    return buf.getvalue()


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
    img = Image.new("RGB", (lngmasterwidth, lngmasterheight), CANVAS_RGB)
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
    """
    Write the master WebP and return (relative_url_path, size).

    Two distinct path roots, do not conflate them (this is what caused the double-nest bug):
      * SHARED_DATA_DIR (/shared) is the container's WRITE root. The container is mounted
        narrowly at its own folder -- `-v $HOME/docker/shared_data/synthetic-images:/shared`
        -- so /shared already IS the synthetic-images directory. We write to /shared/<class>/,
        WITHOUT re-appending SYNTHETIC_IMAGE_SUBDIR (doing so produced .../synthetic-images/synthetic-images/).
      * SYNTHETIC_IMAGE_SUBDIR is the public URL prefix. tmdb-front's Apache serves from the
        real shared_data root (one level above the mount), so the stored path the API hands out
        must still carry the `synthetic-images/` segment to resolve (review §10).
    """
    strdir = os.path.join(strshareddatadir, stritemclass or "misc")
    os.makedirs(strdir, exist_ok=True)
    strfilename = "{0}.webp".format(strimagekey)
    strfullpath = os.path.join(strdir, strfilename)
    with open(strfullpath, "wb") as fh:
        fh.write(bytesimage)
    # URL path is relative to the shared_data root Apache serves -- it keeps the subdir segment.
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
    strsourcetext, strsource = f_resolve_source_text(stridwikidata, strname, stroverview, intdryrun=intdryrun)
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


# ---------------------------------------------------------------------------
# Stage L -- logo padding (decision #7: companies/networks pad the REAL logo)
# ---------------------------------------------------------------------------
# Deterministic Pillow path that replaces Stages 0-2 for the 'logo-pad' representation:
# fetch the real TMDb logo, flatten + center it on the off-white 2:3 canvas, then reuse the
# same validation / storage / persistence as the synthetic path. NO model calls, zero cost --
# the only spend is hosting the created masters.

# Per-class logo source tables (the v1 scope: only rows that HAVE a LOGO_PATH are padded).
LOGO_SOURCE_TABLES = {
    "company": ("T2S_COMPANY", "ID_COMPANY"),
    "network": ("T2S_NETWORK", "ID_NETWORK"),
}


def f_lookup_logo_path(stritemclass, lngiditem):
    """LOGO_PATH for one company/network row (single-item harness; batch passes it in)."""
    arrtable = LOGO_SOURCE_TABLES.get((stritemclass or "").lower())
    if not arrtable or lngiditem is None:
        return ""
    strtable, stridcol = arrtable
    return cp.f_fieldfromquery(
        "SELECT LOGO_PATH FROM " + strsqlns + strtable +
        " WHERE " + stridcol + "=%s AND (DELETED IS NULL OR DELETED=0)",
        "LOGO_PATH", params=(lngiditem,),
    ) or ""


def f_fetch_logo(strlogopath):
    """
    Download one logo from the TMDb image CDN. Returns (bytes_or_None, source_url) -- the URL
    is recorded as SOURCE_URL provenance either way. .svg logos are fetched as the CDN's
    rasterized PNG rendition at LOGO_RASTER_SIZE (see the config note on strlogorastersize).
    """
    if not strlogopath:
        return None, ""
    strpath = strlogopath if strlogopath.startswith("/") else "/" + strlogopath
    if strpath.lower().endswith(".svg"):
        strrasterbase = strlogobaseurl.rsplit("/", 1)[0] + "/" + strlogorastersize
        strurl = strrasterbase + strpath[:-4] + ".png"
    else:
        strurl = strlogobaseurl + strpath
    try:
        import requests
        rsp = requests.get(strurl, timeout=60)
        if rsp.status_code != 200 or not rsp.content:
            print("  [logo-pad] fetch failed ({0}): HTTP {1}".format(strurl, rsp.status_code))
            return None, strurl
        return rsp.content, strurl
    except Exception as err:  # broad by house convention; surface via messages
        print("  [logo-pad] fetch failed ({0}): {1}".format(strurl, err))
        return None, strurl


def f_pad_logo_to_canvas(byteslogo):
    """
    Letterbox/pad real logo bytes onto the 2:3 off-white master canvas. Returns WebP bytes or
    None. The logo is flattened against the canvas (alpha-safe), scaled to fit a safe box of
    80% width x 40% height (logos are mostly wide, so they letterbox naturally), and centered.
    Upscaling is capped at 2x so low-resolution logos don't render visibly blurry.

    A soft dark halo built from the logo's OWN alpha channel is composited behind it (the
    pixel-space equivalent of the front-end CSS drop-shadow) so white/near-white logos stay
    readable on the off-white canvas. Changing the halo or canvas is a STYLE change: bump
    STYLE_VERSION so the already-padded masters mass-regenerate.
    """
    if not byteslogo:
        return None
    try:
        from PIL import Image, ImageFilter
        img = Image.open(io.BytesIO(byteslogo)).convert("RGBA")
    except Exception as err:
        print("  [logo-pad] unreadable logo: {0}".format(err))
        return None
    lngboxwidth = int(lngmasterwidth * 0.80)
    lngboxheight = int(lngmasterheight * 0.40)
    dblscale = min(lngboxwidth / img.width, lngboxheight / img.height, 2.0)
    lngwidth = max(1, int(img.width * dblscale))
    lngheight = max(1, int(img.height * dblscale))
    img = img.resize((lngwidth, lngheight), Image.LANCZOS)
    lngleft = (lngmasterwidth - lngwidth) // 2
    lngtop = (lngmasterheight - lngheight) // 2
    canvas = Image.new("RGB", (lngmasterwidth, lngmasterheight), CANVAS_RGB)
    # Shadow mask: the logo alpha at 50% opacity, blurred on a full-canvas layer so the halo
    # is not clipped at the logo edges. Radius scales with the rendered logo size. The blurred
    # mask is then AMPLIFIED (capped): Gaussian falloff alone leaves only ~quarter-strength
    # shadow right at the glyph edge, which is invisible behind a white logo on the off-white
    # canvas -- amplification turns the halo into a solid outline that fades outward.
    lngblurradius = max(3, int(lngwidth / 60))
    imgshadowmask = Image.new("L", (lngmasterwidth, lngmasterheight), 0)
    imgshadowmask.paste(img.split()[3].point(lambda lngalpha: int(lngalpha * 0.5)), (lngleft, lngtop))
    imgshadowmask = imgshadowmask.filter(ImageFilter.GaussianBlur(lngblurradius))
    imgshadowmask = imgshadowmask.point(lambda lngalpha: min(255, int(lngalpha * 2.2)))
    imgshadowfill = Image.new("RGB", (lngmasterwidth, lngmasterheight), (0, 0, 0))
    canvas.paste(imgshadowfill, (0, 0), imgshadowmask)
    canvas.paste(img, (lngleft, lngtop), img)
    buf = io.BytesIO()
    canvas.save(buf, format="WEBP", quality=90)
    return buf.getvalue()


def _placeholder_logo():
    """A small opaque rectangle standing in for a logo so dry-run pads offline (no CDN)."""
    from PIL import Image
    img = Image.new("RGBA", (400, 160), (40, 40, 40, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def f_pad_logo_image(stritemclass=None, lngiditem=None, strname="", strlogopath="",
                     intdryrun=0, intforce=0):
    """
    Pad one real company/network logo onto the 2:3 canvas (the 'logo-pad' counterpart of
    f_generate_synthetic_image -- same result dict, same idempotency, validation, storage and
    persistence; no LLM/image-model calls, GENERATION_COST is always 0).

    Identity is always the (ITEM_CLASS, ID_ITEM) fallback -- the T2S company/network tables
    carry no ID_WIKIDATA (review #14 anticipated padded logos as non-Wikidata stragglers).
    """
    arrmessages = []
    stridentity = f_identity(None, stritemclass, lngiditem)
    strimagekey = f_image_key(stridentity, "logo-pad", strstyleversion)

    # 1. Idempotency / resume (review §6): skip unless forced.
    if not intforce:
        idexisting = f_existing_image_key(strimagekey, strstyleversion)
        if idexisting:
            return {"status": "skipped", "reason": "exists", "image_key": strimagekey,
                    "id_synthetic_image": idexisting, "messages": ["already generated"]}

    # 2. Resolve the logo, fetch (or stub offline), pad onto the canvas.
    dblstart = time.time()
    if intdryrun:
        byteslogo, strsourceurl = _placeholder_logo(), ""
        arrmessages.append("source=dry-run")
    else:
        if not strlogopath:
            strlogopath = f_lookup_logo_path(stritemclass, lngiditem)
        if not strlogopath:
            return {"status": "failed", "image_key": strimagekey, "id_synthetic_image": None,
                    "image_path": "", "cost": 0.0, "seconds": 0.0,
                    "messages": ["no LOGO_PATH for {0}".format(stridentity)]}
        byteslogo, strsourceurl = f_fetch_logo(strlogopath)
        arrmessages.append("source=tmdb-logo")
        if byteslogo is None:
            arrmessages.append("fetch failed: {0}".format(strsourceurl))
    bytesimage = f_pad_logo_to_canvas(byteslogo)
    dblelapsed = round(time.time() - dblstart, 3)

    # 3. Validation gate (same as the synthetic path).
    intok, strreason, w, h = f_validate_image(bytesimage)
    strstatus = "generated" if intok else "failed"
    strrelpath, lngsize = ("", 0)
    if intok:
        strrelpath, lngsize = f_store_image(bytesimage, stritemclass, strimagekey)
    else:
        arrmessages.append("validation: {0}".format(strreason))

    # 4. Persist provenance + mapping. T2T/T2I provenance fields stay NULL on purpose --
    #    'pillow-logo-pad' marks the rows so the corpus can be filtered by generator.
    arrimage = {
        "IMAGE_KEY": strimagekey,
        "ID_WIKIDATA": None,
        "REPRESENTATION": "logo-pad",
        "IMAGE_PATH": strrelpath or None,
        "WIDTH": w or None, "HEIGHT": h or None,
        "ASPECT_RATIO": straspectratio, "FORMAT": "webp", "FILE_SIZE": lngsize or None,
        "OBJECT_DESCRIPTION": None,
        "T2T_LLM": None, "T2T_PROMPT_VERSION": None,
        "T2I_MODEL": "pillow-logo-pad", "T2I_PROMPT_VERSION": None,
        "T2I_SEED": None, "STYLE_VERSION": strstyleversion,
        "SOURCE": "tmdb-logo",
        "SOURCE_URL": (strsourceurl or None) if not intdryrun else None,
        "STATUS": strstatus, "IS_VALIDATED": 1 if intok else 0,
        "FAILURE_REASON": (strreason or None) if not intok else None,
        "GENERATION_COST": 0.0, "GENERATION_TIME": dblelapsed,
        "TIM_GENERATED": f_now(),
    }
    arrmapping = {
        "ITEM_CLASS": stritemclass, "ID_ITEM": lngiditem, "ID_WIKIDATA": None,
        "REPRESENTATION": "logo-pad", "IS_CHOSEN": 1,
    }

    idimage = None
    if intdryrun:
        arrmessages.append("dry-run: not persisted")
    else:
        idimage = f_record_image(arrimage, arrmapping)

    return {
        "status": strstatus, "image_key": strimagekey, "id_synthetic_image": idimage,
        "image_path": strrelpath, "cost": 0.0, "seconds": dblelapsed,
        "messages": arrmessages,
    }
