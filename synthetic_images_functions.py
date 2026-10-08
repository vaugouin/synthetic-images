"""
synthetic_images_functions -- two-stage synthetic-illustration pipeline.

Project A of the "An image for everything" plan (see
%USERPROFILE%/Nestor/projets/t2s-backlog/topics/an-image-for-everything/). For one entity it:
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
strt2tpromptversion = os.environ.get("T2T_PROMPT_VERSION", "v2")
strt2tsourcemaxchars = int(os.environ.get("T2T_SOURCE_MAXCHARS", "2000"))
strt2ipromptversion = os.environ.get("T2I_PROMPT_VERSION", "v1")
strt2tmodel = os.environ.get("T2T_MODEL", "claude-haiku-5-5")
# Effort for the description call on models that support it (Claude 4.6+ / 5.x). A short object
# description needs no deep reasoning; "low" keeps adaptive thinking cheap (Haiku 5.5 defaults
# to "medium").
strt2teffort = os.environ.get("T2T_EFFORT", "low")
# Candidates rendered per entity by a batch (SYNTHETIC-IMAGES-019): one description, N renders.
lngcandidatesdefault = int(os.environ.get("CANDIDATES", "3"))
strt2imodel = os.environ.get("T2I_MODEL", "black-forest-labs/flux-schnell")
straspectratio = os.environ.get("ASPECT_RATIO", "2:3")
lngmasterwidth = int(os.environ.get("MASTER_WIDTH", "1024"))
lngmasterheight = int(os.environ.get("MASTER_HEIGHT", "1536"))
# SHARED_DATA_DIR = container WRITE root. The mount is narrow
# (`-v .../shared_data/synthetic-images:/shared`) so /shared already IS the synthetic-images
# folder -- the container cannot see the rest of shared_data. SYNTHETIC_IMAGE_SUBDIR is NOT a
# physical subdir under it; it is the URL prefix Apache (serving from the real shared_data root,
# one level up) prepends. See f_store_image for why the two must stay decoupled.
# Per-image unit cost for budget tracking (review §8), USD for one ~1024x1536 image.
# Read from each provider's pricing page on 2026-10-08 (SYNTHETIC-IMAGES-008); the full survey and
# its sources: Nestor/projets/t2s-backlog/topics/an-image-for-everything/etat-des-lieux-modeles-2026-10-08.md.
# Per-megapixel prices are x 1.57 MP. Prices drift: re-read them before an expensive run.
T2I_COST = {
    # Replicate
    "black-forest-labs/flux-schnell": 0.003,  # $3 / 1000 images; max 1 MP, so ~832x1248 then upscaled
    "prunaai/z-image-turbo": 0.012,           # per-MP tiers, 0.008-0.016 depending on the tier
    "prunaai/p-image": 0.005,                 # $5 / 1000 images; max 1440 px a side
    "krea/krea-2-medium": 0.03,               # text-to-image only (style references cost more)
    "black-forest-labs/flux-2-dev": 0.022,    # $0.014/MP regular (0.019 with go_fast)
    "black-forest-labs/flux-2-pro": 0.039,    # $0.015/run + $0.015/MP
    "black-forest-labs/flux-2-flex": 0.094,   # $0.06/MP (best typography, slower)
    "google/nano-banana": 0.039,
    # OpenAI direct, "medium" quality 1024x1536 (see OPENAI_COST for the other qualities)
    "gpt-image-2": 0.041,
    "gpt-image-2.5-flare": 0.041,             # not published per image; same token rates as gpt-image-2 (estimate)
    "gpt-image-2.5-sunburst": 0.041,          # idem
    "gpt-image-1": 0.063,                     # deprecated, shuts down 2026-10-23
    # Gemini direct, at GEMINI_IMAGE_SIZE (see GEMINI_COST)
    "gemini-nano-banana-2.1": 0.0504,
    "gemini-3.1-flash-lite-image": 0.0336,
    "gemini-3.1-flash-image": 0.101,
    "gemini-3-pro-image": 0.134,
    "gemini-2.5-flash-image": 0.039,          # legacy; Google's shutdown date is contradictory
}
# Conservative cost charged for a model missing from the tables (budget ceiling stays meaningful).
T2I_COST_UNKNOWN = 0.10
# Size-dependent prices: Gemini by image_size, OpenAI by quality. f_t2i_cost() reads these first.
GEMINI_COST = {
    "gemini-nano-banana-2.1": {"1K": 0.0336, "2K": 0.0504, "4K": 0.1134},
    "gemini-3.1-flash-lite-image": {"1K": 0.0336},
    "gemini-3.1-flash-image": {"1K": 0.067, "2K": 0.101, "4K": 0.151},
    "gemini-3-pro-image": {"1K": 0.134, "2K": 0.134, "4K": 0.24},
}
# Gemini image models that accept only the 1K size (passing "2K" would be rejected).
GEMINI_1K_ONLY = ("gemini-3.1-flash-lite-image", "gemini-2.5-flash-image")
OPENAI_COST = {
    "gpt-image-2": {"low": 0.005, "medium": 0.041, "high": 0.165},
    "gpt-image-1": {"low": 0.016, "medium": 0.063, "high": 0.25},
}
# Model ids that no longer answer: fail fast with the replacement instead of a provider error.
T2I_RETIRED = {
    "gemini-3-pro-image-preview": "gemini-3-pro-image",  # shut down 2026-06-25
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
    "Illustrated plate in the style of a visual dictionary. "
    "Flat lighting, plain off-white textured paper background, encyclopedic "
    "technical-illustration style, soft realistic shading, educational reference "
    "aesthetic, minimalist composition. "
    "1990s printed visual dictionary style. "
    "Just the illustrated subject on the paper, nothing else: no text, no title, "
    "no caption, no labels, no callouts, no annotations, no leader lines, no arrows, "
    "no legend, no numbers, no letters, no measurement marks, no border. "
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


def f_image_key(stridentity, strrepresentation, strstyleversion_, lngcandidateindex=1):
    """
    Content/parameter hash. Idempotency keys on (IMAGE_KEY, STYLE_VERSION) (review §6),
    so a style bump produces a new key and the old image can be mass-invalidated.
    Candidate 1 keeps the historical form, so images produced before candidates existed keep
    their key; candidates 2..N append "|c<N>" (SYNTHETIC-IMAGES-019).
    """
    arrparts = [stridentity, strrepresentation or "", strstyleversion_ or ""]
    if lngcandidateindex and int(lngcandidateindex) > 1:
        arrparts.append("c{0}".format(int(lngcandidateindex)))
    strraw = "|".join(arrparts)
    return hashlib.sha256(strraw.encode("utf-8")).hexdigest()[:32]


def f_now():
    return datetime.now(pytz.timezone(strtimezone)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Stage 0 -- resolve a source description for the entity
# ---------------------------------------------------------------------------
def f_resolve_source_text(stridwikidata, strname, stroverview, intdryrun=0, strsearchquery=""):
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
    strsearched = f_web_search_fallback(strsearchquery or strname)
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
# Per-(class, representation) subject rule for the description stage.
#
# This is the ONLY part of the description prompt that varies per entity: it names
# WHAT the central subject is. Everything else -- length, concreteness, and the
# no-style-leakage guard that keeps the grid consistent (keystone rule #1) -- is
# shared in f_text_to_text and never duplicated here.
#
# Keyed on (item_class, representation), the primary key of T_WC_T2S_REPRESENTATION.
# Mirrors that table's T2I_PROMPT_SLOT vocabulary (02_representation_seed.sql);
# production should read the DB column, this copy keeps the offline bake-off DB-free.
# Each value reads grammatically after "a description of ...". Golden-set slugs that
# differ from the seed (e.g. 'landscape' vs 'day-landscape', 'object' vs
# 'award-object') are included as aliases so eval items hit a specific rule rather
# than the class default. The logo-pad classes (company/network) never reach this
# stage -- they take the deterministic Pillow path -- so they are intentionally absent.
T2T_SUBJECT_RULES = {
    # --- Locations (representation drives the subject; review §4.2) ---
    ("location", "day-landscape"): "a representative daytime skyline or landscape view of the place",
    ("location", "night-landscape"): "a representative illuminated night-time cityscape of the place",
    ("location", "landscape"): "a representative skyline or landscape view of the place",
    ("location", "map"): "a clean map locating the place, in neutral cartographic styling",
    ("location", "flag"): "the official flag of the place, shown as a full rectangular flag displayed flat and front-facing with its colours and design clearly visible and correctly ordered; if a flagpole is shown it is a vertical pole along the LEFT (hoist) edge of the flag -- never a horizontal pole above the flag, and the flag hangs to the right of that pole",
    ("location", "satellite"): "a satellite or aerial overhead view of the place",
    ("location", "street-view"): "a street-level, eye-line view of the place",
    ("location", "street-level"): "a street-level, eye-line view of the place",
    ("location", "architecture"): "the building or structure as an architectural subject",
    ("location", "building"): "the building or structure as an architectural subject",
    ("location", "artistic"): "an imaginative depiction of the fictional place (no real photo exists)",
    # --- Occupations (the role's instruments, never a portrait of a person) ---
    ("occupation", "plate"): "the characteristic tools, attire and setting of the role -- its instruments, not a portrait of a person",
    ("occupation", "artistic"): "a person shown performing the role",
    # --- Characters ---
    ("character", "portrait"): "a centered bust of the fictional character",
    ("character", "artistic"): "a full-figure depiction of the fictional character",
    # --- Awards / nominations (generic trophy; never a trademarked real one, §13) ---
    ("award", "award-object"): "a generic award trophy or medal -- do NOT depict a trademarked real trophy such as the Oscar statuette or the Palme d'Or",
    ("award", "object"): "a generic award trophy or medal -- do NOT depict a trademarked real trophy such as the Oscar statuette or the Palme d'Or",
    ("nomination", "award-object"): "a generic award trophy or medal -- do NOT depict a trademarked real trophy",
    # --- Movements (an iconic film/work that exemplifies the movement) ---
    ("movement", "movie-poster"): "an iconic film that exemplifies the movement, framed as a single poster-like scene",
    ("movement", "movie-snapshot"): "a single scene from an iconic film that exemplifies the movement",
    ("movement", "group-photo"): "a group of the figures associated with the movement",
    ("movement", "plate"): "an iconic object, scene or artifact that exemplifies the movement",
    # --- Technicals (film-domain apparatus / material) ---
    ("technical", "plate"): "the physical apparatus, equipment or film material that embodies the technique -- e.g. a reel or strip of film with visible imagery on the frames, a camera, a projector, or a lens",
    # --- Genres ---
    ("genre", "plate"): "the iconic objects, props or setting that signal the film genre",
    ("genre", "artistic"): "an evocative montage of motifs from the film genre",
    # --- Countries ---
    ("country", "flag"): "the official national flag, shown as a full rectangular flag displayed flat and front-facing with its colours and design clearly visible and correctly ordered; if a flagpole is shown it is a vertical pole along the LEFT (hoist) edge of the flag -- never a horizontal pole above the flag, and the flag hangs to the right of that pole",
    ("country", "map"): "a clean map locating the country, in neutral cartographic styling",
    ("country", "day-landscape"): "a representative daytime landscape of the country",
    # --- Languages (the script, NOT a flag, §4.1) ---
    ("language", "typographic"): "the writing system and characteristic script of the language",
    ("language", "globe"): "a globe highlighting the regions where the language is spoken (no national flag)",
    # --- Collections / lists (composite of members) ---
    ("collection", "poster-mix"): "the visual motifs of the collection's members blended into one cohesive scene",
    ("collection", "composite"): "the visual motifs of the collection's members blended into one cohesive scene",
    ("list", "poster-mix"): "the visual motifs of the list's members blended into one cohesive scene",
    # --- Groups ---
    ("group", "group-photo"): "the group of people as a recognizable ensemble",
    ("group", "group-portrait"): "the group of people as a recognizable ensemble",
    ("group", "artistic"): "an emblem representing the group",
    # --- Topics (keyword-derived; sub-type refines the pick, §9.1) ---
    ("topic", "plate"): "the most iconic tangible object associated with the topic",
    ("topic", "portrait"): "a figure representative of the topic",
    ("topic", "map"): "a clean map for the location topic",
    ("topic", "landscape"): "a representative skyline or landscape view of the place",
    ("topic", "artistic"): "an artistic motif evoking the topic",
    # --- Deaths (cause/manner, tasteful & non-graphic; never a person) ---
    ("death", "plate"): "a tasteful, non-graphic symbol of the cause or manner of death (no person, nothing distressing)",
    ("death", "artistic"): "a restrained symbol evoking the cause or manner of death (no person, nothing distressing)",
}

# Fallback when a (class, representation) pair is not listed above.
T2T_CLASS_DEFAULT = {
    "location": "the place as a recognizable setting",
    "occupation": "the characteristic tools and setting of the role, not a portrait of a person",
    "character": "the fictional character as a recognizable figure",
    "award": "a generic award trophy or medal (avoid trademarked real trophies)",
    "nomination": "a generic award trophy or medal (avoid trademarked real trophies)",
    "movement": "an iconic object or scene that exemplifies the movement",
    "technical": "the physical apparatus or material that embodies the technique",
    "genre": "the iconic objects or setting that signal the genre",
    "country": "the country as a recognizable place",
    "language": "the characteristic written script of the language",
    "collection": "the shared visual motifs of the collection's members",
    "list": "the shared visual motifs of the list's members",
    "group": "the group of people as a recognizable ensemble",
    "topic": "the most iconic tangible thing associated with the topic",
    "death": "a tasteful, non-graphic symbol of the cause or manner of death (no person)",
}


def _f_subject_rule(strclass, strrepresentation):
    """The per-entity subject line for the description prompt: representation-specific
    where the representation forces a framing (flag/map/portrait/...), else the class
    default, else a generic fallback."""
    return (T2T_SUBJECT_RULES.get((strclass or "", strrepresentation or ""))
            or T2T_CLASS_DEFAULT.get(strclass or "")
            or "the most iconic tangible form of the subject")


# System prompt of the description call. Part of the "complete T2T prompt" the lab shows and the
# description row stores (T_WC_T2S_SYNTHETIC_DESCRIPTION.T2T_PROMPT).
T2T_SYSTEM = (
    "You write concise, literal visual object descriptions for an encyclopedic "
    "illustration pipeline. Favor simplicity and instant recognizability over "
    "completeness. Respond with the description only -- no preamble, no style or "
    "color words, no aspect ratio."
)

# Text-to-text prices, USD per million tokens (input, output), read 2026-10-08. Used to cost each
# description from the provider's own token counts (SYNTHETIC-IMAGES-019: the description is
# costed once, in its own row, never spread over the candidates it feeds).
T2T_PRICE = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-5-5": (4.0, 20.0),
}


def f_t2t_cost(strmodel, lnginputtokens, lngoutputtokens):
    """USD cost of one description call from its token counts (0.0 for an unpriced model)."""
    dblin, dblout = T2T_PRICE.get(strmodel or "", (0.0, 0.0))
    return round(((lnginputtokens or 0) * dblin + (lngoutputtokens or 0) * dblout) / 1000000.0, 6)


def f_build_t2t_prompt(strname, strsourcetext, strrepresentation, strclass):
    """The user prompt of the description call, exactly as sent (the lab shows it before any spend)."""
    return (
        "You are choosing the SINGLE most emblematic, instantly recognizable form of "
        "'{0}' ({1}) for an encyclopedic visual-dictionary plate.\n"
        "Write ONE or TWO short sentences describing {2}. One clear central subject that "
        "fills the composition. Name only the few defining features that make it recognizable "
        "at a glance -- favor the iconic whole over an exhaustive parts list.\n"
        "Keep it simple: at most 2-3 visual elements. Prefer the most common, prototypical "
        "version; avoid rare variants, internal mechanisms, technical detail, or enumerations.\n"
        "Stay strictly on '{0}': describe only attributes that belong to it; add no unrelated "
        "objects, scenes, or domains.\n"
        "Do NOT describe it as proportions, ratios, dimensions, measurements, an empty frame, "
        "a chart, or a diagram. Do NOT mention art style, color palette, lighting, background, "
        "or aspect ratio.\n\n"
        "Reference (use ONLY to identify the iconic form, do not copy its detail):\n{3}"
    ).format(strname, strclass, _f_subject_rule(strclass, strrepresentation),
             (strsourcetext or "")[:strt2tsourcemaxchars])


def f_describe_text(strname, strsourcetext, strrepresentation, strclass, intdryrun=0, strmodel=None):
    """
    Run the description call and return everything the lab and the description row need:
    {text, model, prompt, system, input_tokens, output_tokens, cost, seconds}.
    The output must describe ONLY the subject/object (review §2) -- never style, palette, or
    aspect ratio. Dry-run returns a deterministic stub so the harness works with no API key.
    """
    strmodel = strmodel or strt2tmodel
    strprompt = f_build_t2t_prompt(strname, strsourcetext, strrepresentation, strclass)
    dctout = {"model": strmodel, "prompt": strprompt, "system": T2T_SYSTEM,
              "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "seconds": 0.0}
    if intdryrun:
        dctout["text"] = "A clear central depiction of {0} ({1}), rendered as a {2}.".format(
            strname, strclass, strrepresentation)
        return dctout
    dblstart = time.time()
    dctcall = _call_t2t_llm(strprompt, strmodel)
    dctout.update(dctcall)
    dctout["cost"] = f_t2t_cost(strmodel, dctout["input_tokens"], dctout["output_tokens"])
    dctout["seconds"] = round(time.time() - dblstart, 3)
    return dctout


def f_text_to_text(strname, strsourcetext, strrepresentation, strclass, intdryrun=0, strmodel=None):
    """Description text only (the bake-off's entry point); see f_describe_text for the full record."""
    return f_describe_text(strname, strsourcetext, strrepresentation, strclass,
                           intdryrun=intdryrun, strmodel=strmodel)["text"]


def _call_t2t_llm(strprompt, strmodel):
    """
    LLM call for the description stage. Dispatches by model family and returns
    {text, input_tokens, output_tokens}. Phase-1 wires Anthropic (claude-*); the OpenAI/Gemini
    branches are added during the eval-plan.md bake-off (the keys are already in .env).
    """
    if (strmodel or "").lower().startswith("claude"):
        return _call_anthropic(strprompt, strmodel)
    raise NotImplementedError(
        "Text-to-text provider for model '{0}' not wired yet. Phase-1 wires Anthropic "
        "(claude-*); add the OpenAI/Gemini branch when the bake-off needs it.".format(strmodel)
    )


def _anthropic_supports_effort(strmodel):
    """output_config.effort exists on Claude 4.6+ and the 5.x family; Haiku 4.5 and older reject it."""
    strmodel = (strmodel or "").lower()
    return strmodel.startswith(("claude-haiku-5", "claude-sonnet-5", "claude-opus-5",
                                "claude-fable-5", "claude-sonnet-4-6", "claude-opus-4-6",
                                "claude-opus-4-7", "claude-opus-4-8"))


def _call_anthropic(strprompt, strmodel):
    """Anthropic Messages API call. Reads ANTHROPIC_API_KEY from the environment (.env).
    Current models think adaptively by default; max_tokens leaves room for that thinking on top
    of the ~200-token description, and T2T_EFFORT keeps it small. Returns
    {text, input_tokens, output_tokens} (output tokens include the thinking, billed as output)."""
    from anthropic import Anthropic
    client = Anthropic()
    dctextra = {}
    if _anthropic_supports_effort(strmodel) and strt2teffort:
        dctextra["output_config"] = {"effort": strt2teffort}
    msg = client.messages.create(
        model=strmodel,
        max_tokens=4096,
        system=T2T_SYSTEM,
        messages=[{"role": "user", "content": strprompt}],
        **dctextra
    )
    dctusage = {"input_tokens": getattr(msg.usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(msg.usage, "output_tokens", 0) or 0}
    if msg.stop_reason == "refusal":
        print("  [t2t] {0} refused the description request".format(strmodel))
        return dict(dctusage, text="")
    if msg.stop_reason == "max_tokens":
        print("  [t2t] {0} hit max_tokens; the description may be truncated".format(strmodel))
    arrparts = [block.text for block in msg.content if getattr(block, "type", "") == "text"]
    return dict(dctusage, text="\n".join(arrparts).strip())


# ---------------------------------------------------------------------------
# Stage 2 -- text-to-image: object description -> style-locked image bytes
# ---------------------------------------------------------------------------
def f_build_t2i_prompt(strobjectdescription):
    """The complete text-to-image prompt, exactly as sent (stored as T2I_PROMPT, shown by the lab)."""
    return STYLE_TEMPLATE.format(object_description=strobjectdescription)


def f_text_to_image(strobjectdescription, lngseed=None, intdryrun=0, strmodel=None):
    """
    Render the locked template + object description to WebP bytes. Returns (bytesimage, seed,
    cost). Dispatches by provider: Gemini image models (Nano Banana / Nano Banana Pro) go through
    the Google GenAI SDK; everything else is a Replicate model (FLUX). Dry-run returns a
    placeholder so storage + validation run offline. strmodel overrides T2I_MODEL (bake-off).
    """
    strt2i = strmodel or strt2imodel
    strprompt = f_build_t2i_prompt(strobjectdescription)
    if intdryrun:
        return _placeholder_image(), (lngseed or 0), 0.0
    if strt2i in T2I_RETIRED:
        print("  [t2i] model '{0}' is retired; use '{1}'".format(strt2i, T2I_RETIRED[strt2i]))
        return None, (lngseed or 0), 0.0
    # Direct Gemini ids have no owner prefix; "google/nano-banana" is the Replicate-hosted copy.
    if strt2i.startswith("gemini") and "/" not in strt2i:
        return _t2i_gemini(strprompt, strt2i, lngseed)
    if strt2i.startswith("gpt-image") or strt2i.startswith("dall-e"):
        return _t2i_openai(strprompt, strt2i, lngseed)
    return _t2i_replicate(strprompt, strt2i, lngseed)


def _replicate_input(strmodel, strprompt, lngseed):
    """Build the per-model Replicate `input` payload. Schemas differ across model families,
    so keep this the single place that knows each one's keys. VERIFY each against the model's
    Replicate 'API' tab -- schemas drift. Default stays FLUX.1-schnell for back-compat."""
    dctin = {
        "prompt": strprompt,
        "output_format": "webp",
        "seed": lngseed if lngseed is not None else 0,
    }
    if strmodel.startswith("black-forest-labs/flux-2"):
        # FLUX.2 dev/pro/flex: aspect_ratio supported, same as FLUX.1.
        dctin["aspect_ratio"] = straspectratio
        return dctin
    if strmodel.startswith("prunaai/z-image"):
        # Z-Image Turbo: sized by explicit width/height; 8-step distilled model.
        dctin.update({
            "width": lngmasterwidth,
            "height": lngmasterheight,
            "num_inference_steps": 8,
        })
        return dctin
    if strmodel == "prunaai/p-image":
        # P-Image: no output_format input, and width/height stop at 1440, so 1024x1536 cannot be
        # asked for directly; the 2:3 preset is normalised to the master size afterwards.
        del dctin["output_format"]
        dctin["aspect_ratio"] = straspectratio
        return dctin
    if strmodel.startswith("krea/krea-2"):
        # Krea 2: no output_format input; "creativity" stays at its default. Style references
        # (style_reference_images) are the lever to test for the style lock, at extra cost.
        del dctin["output_format"]
        dctin["aspect_ratio"] = straspectratio
        return dctin
    # FLUX.1 schnell (current default). Its `megapixels` input stops at 1, so a 2:3 render is
    # ~1 MP and gets normalised to the master size afterwards.
    dctin["aspect_ratio"] = straspectratio
    return dctin


def _t2i_replicate(strprompt, strmodel, lngseed):
    """Replicate-hosted model (FLUX.1/2, Z-Image, P-Image, Krea 2; review §8). Returns (webp_bytes, seed, cost)."""
    try:
        import replicate
        arrout = replicate.run(strmodel, input=_replicate_input(strmodel, strprompt, lngseed))
        return _to_webp(_read_replicate_output(arrout)), (lngseed or 0), f_t2i_cost(strmodel)
    except Exception as err:
        print("  [t2i] replicate generation failed ({0}): {1}".format(strmodel, err))
        return None, (lngseed or 0), 0.0


def _t2i_gemini(strprompt, strmodel, lngseed):
    """
    Google Gemini image generation -- Nano Banana 2.1 (gemini-nano-banana-2.1), Nano Banana 2 and
    2 Lite (gemini-3.1-flash-image / -flash-lite-image), Nano Banana Pro (gemini-3-pro-image), and
    the legacy Nano Banana (gemini-2.5-flash-image). Uses GEMINI_API_KEY. Seed control is not
    documented for images, so the seed is recorded but not enforced. Gemini has no 1024x1536 size
    (2:3 is 848x1264 at 1K, 1696x2528 at 2K): the output is normalised to the master size.
    Returns (webp_bytes, seed, cost).
    """
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        strsize = _gemini_image_size(strmodel)
        rsp = client.models.generate_content(
            model=strmodel,
            contents=strprompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=straspectratio,
                    image_size=strsize,
                ),
            ),
        )
        for part in rsp.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return _to_webp(part.inline_data.data), (lngseed or 0), f_t2i_cost(strmodel)
        print("  [t2i] gemini returned no image for {0} (safety block / refusal?)".format(strmodel))
        return None, (lngseed or 0), 0.0
    except Exception as err:
        print("  [t2i] gemini generation failed ({0}): {1}".format(strmodel, err))
        return None, (lngseed or 0), 0.0


def _t2i_openai(strprompt, strmodel, lngseed):
    """
    OpenAI image generation -- GPT Image (gpt-image-2, gpt-image-2.5-flare / -sunburst; the
    deprecated gpt-image-1 shuts down 2026-10-23). Uses OPENAI_API_KEY. Returns base64 PNG;
    "1024x1536" is the 2:3 portrait that matches our master. There is NO seed control, so the seed
    is recorded but not enforced. Output is normalised to a 2:3 WebP master.
    Returns (webp_bytes, seed, cost).
    """
    try:
        import base64
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        rsp = client.images.generate(
            model=strmodel,
            prompt=strprompt,
            size=os.environ.get("OPENAI_IMAGE_SIZE", "1024x1536"),
            quality=os.environ.get("OPENAI_IMAGE_QUALITY", "medium"),
            n=1,
        )
        strb64 = rsp.data[0].b64_json
        if strb64:
            return _to_webp(base64.b64decode(strb64)), (lngseed or 0), f_t2i_cost(strmodel)
        print("  [t2i] openai returned no image for {0} (safety block / refusal?)".format(strmodel))
        return None, (lngseed or 0), 0.0
    except Exception as err:
        print("  [t2i] openai generation failed ({0}): {1}".format(strmodel, err))
        return None, (lngseed or 0), 0.0


def _gemini_image_size(strmodel):
    """GEMINI_IMAGE_SIZE (default 2K), forced to 1K for the models that only have 1K."""
    if strmodel in GEMINI_1K_ONLY:
        return "1K"
    return os.environ.get("GEMINI_IMAGE_SIZE", "2K")


def f_t2i_cost(strmodel):
    """USD cost of one render with the current size/quality settings."""
    if strmodel in GEMINI_COST:
        dctsizes = GEMINI_COST[strmodel]
        return dctsizes.get(_gemini_image_size(strmodel), T2I_COST.get(strmodel, 0.0))
    if strmodel in OPENAI_COST:
        strquality = os.environ.get("OPENAI_IMAGE_QUALITY", "medium")
        return OPENAI_COST[strmodel].get(strquality, T2I_COST.get(strmodel, 0.0))
    if strmodel not in T2I_COST:
        # Unknown model: count it high rather than free, so the RUN_BUDGET_USD ceiling still bites.
        print("  [t2i] no unit cost recorded for '{0}': budget tracking counts {1}".format(
            strmodel, T2I_COST_UNKNOWN))
    return T2I_COST.get(strmodel, T2I_COST_UNKNOWN)


def _to_webp(bytesimage):
    """
    Normalise provider output (PNG/JPEG/WebP) to the WebP master. Most providers cannot render
    exactly MASTER_WIDTH x MASTER_HEIGHT (FLUX schnell stops at 1 MP, P-Image at 1440 px a side,
    Gemini has 848x1264 / 1696x2528), so a render already within the 2:3 tolerance is resized to
    the master size; anything else is left untouched for f_validate_image to reject.
    """
    from PIL import Image
    img = Image.open(io.BytesIO(bytesimage)).convert("RGB")
    w, h = img.size
    if h and (w, h) != (lngmasterwidth, lngmasterheight) and abs(w / h - 2 / 3) <= 0.02:
        img = img.resize((lngmasterwidth, lngmasterheight), Image.LANCZOS)
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
    Upsert one T_WC_T2S_SYNTHETIC_IMAGE row (unique on IMAGE_KEY), then point the entity's
    T_WC_T2S_ENTITY_IMAGE row at it through f_set_entity_choice, which never overrides a choice
    made by hand in the lab (SYNTHETIC-IMAGES-019) and never duplicates the row when
    ID_WIKIDATA is NULL (a NULL defeats the occurrence UNIQUE key, so an upsert would insert).
    """
    cp.f_sqlbulkupsert(strsqlns + "T2S_SYNTHETIC_IMAGE", [arrimage], ["IMAGE_KEY"], 1)
    idimage = cp.f_fieldfromquery(
        "SELECT ID_SYNTHETIC_IMAGE FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE WHERE IMAGE_KEY=%s",
        "ID_SYNTHETIC_IMAGE", params=(arrimage["IMAGE_KEY"],),
    )
    if idimage and arrimage.get("STATUS") == "generated":
        f_set_entity_choice(
            arrmapping.get("ITEM_CLASS"), arrmapping.get("ID_ITEM"), arrmapping.get("ID_WIKIDATA"),
            arrmapping.get("REPRESENTATION"), idimage, intmanual=0,
            intcandidateindex=arrimage.get("CANDIDATE_INDEX") or 1,
        )
    return idimage


def f_set_entity_choice(stritemclass, lngiditem, stridwikidata, strrepresentation, idimage,
                        intmanual=0, intcandidateindex=1):
    """
    Point the entity's mapping row at image `idimage` (IS_CHOSEN=1). Returns (id_row, changed).

    Automatic calls (intmanual=0, the pipeline) only claim the slot when the entity has no row
    yet, or for candidate 1 when the current choice was not made by hand: candidate 1 is served
    as soon as a batch has produced it, and a style bump still moves the automatic choice to the
    new candidate 1. A manual call (the lab's "Choose") always wins and sets IS_MANUAL_CHOICE=1,
    which no later batch undoes.
    """
    conn = cp.f_getconnection()
    cur = conn.cursor()
    cur.execute(
        "SELECT ID_ROW, IS_MANUAL_CHOICE, ID_SYNTHETIC_IMAGE FROM " + strsqlns + "T2S_ENTITY_IMAGE "
        "WHERE ITEM_CLASS=%s AND ID_ITEM<=>%s AND ID_WIKIDATA<=>%s AND REPRESENTATION=%s "
        "AND (DELETED IS NULL OR DELETED=0) ORDER BY ID_ROW LIMIT 1",
        (stritemclass, lngiditem, stridwikidata or None, strrepresentation),
    )
    arrrow = cur.fetchone()
    strnow = f_now()
    if arrrow:
        if not intmanual and (arrrow.get("IS_MANUAL_CHOICE") or int(intcandidateindex or 1) != 1):
            return arrrow["ID_ROW"], False
        cur.execute(
            "UPDATE " + strsqlns + "T2S_ENTITY_IMAGE SET ID_SYNTHETIC_IMAGE=%s, IS_CHOSEN=1, "
            "IS_MANUAL_CHOICE=%s, TIM_UPDATED=%s WHERE ID_ROW=%s",
            (idimage, 1 if intmanual else 0, strnow, arrrow["ID_ROW"]),
        )
        conn.commit()
        return arrrow["ID_ROW"], True
    cur.execute(
        "INSERT INTO " + strsqlns + "T2S_ENTITY_IMAGE (ITEM_CLASS, ID_ITEM, ID_WIKIDATA, "
        "REPRESENTATION, ID_SYNTHETIC_IMAGE, IS_CHOSEN, IS_MANUAL_CHOICE, DELETED, DAT_CREAT, "
        "TIM_UPDATED) VALUES (%s, %s, %s, %s, %s, 1, %s, 0, %s, %s)",
        (stritemclass, lngiditem, stridwikidata or None, strrepresentation, idimage,
         1 if intmanual else 0, strnow[:10], strnow),
    )
    conn.commit()
    return cur.lastrowid, True


def f_record_description(dctprep, dctdesc):
    """
    One T_WC_T2S_SYNTHETIC_DESCRIPTION row per description call (SYNTHETIC-IMAGES-019): the
    complete T2T prompt and its cost live here once, and every candidate rendered from the
    description points at it. Returns ID_SYNTHETIC_DESCRIPTION.
    """
    conn = cp.f_getconnection()
    cur = conn.cursor()
    strnow = f_now()
    cur.execute(
        "INSERT INTO " + strsqlns + "T2S_SYNTHETIC_DESCRIPTION (ITEM_CLASS, ID_ITEM, ID_WIKIDATA, "
        "REPRESENTATION, ENTITY_NAME, OBJECT_DESCRIPTION, IS_EDITED, T2T_LLM, T2T_PROMPT_VERSION, "
        "T2T_SYSTEM, T2T_PROMPT, SOURCE, SOURCE_URL, INPUT_TOKENS, OUTPUT_TOKENS, T2T_COST, "
        "GENERATION_TIME, TIM_GENERATED, DELETED, DAT_CREAT, TIM_UPDATED) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s)",
        (dctprep.get("item_class"), dctprep.get("id_item"), dctprep.get("id_wikidata") or None,
         dctprep.get("representation"), dctprep.get("name"), dctdesc.get("text"),
         1 if dctdesc.get("is_edited") else 0, dctdesc.get("model"), strt2tpromptversion,
         dctdesc.get("system"), dctdesc.get("prompt"), dctprep.get("source"),
         dctprep.get("source_url"), dctdesc.get("input_tokens"), dctdesc.get("output_tokens"),
         dctdesc.get("cost"), dctdesc.get("seconds"), strnow, strnow[:10], strnow),
    )
    conn.commit()
    return cur.lastrowid


def f_existing_image_key(strimagekey, strstyleversion_):
    """Idempotency probe: has this (IMAGE_KEY, STYLE_VERSION) already been generated?"""
    return cp.f_fieldfromquery(
        "SELECT ID_SYNTHETIC_IMAGE FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE "
        "WHERE IMAGE_KEY=%s AND STYLE_VERSION=%s AND (DELETED IS NULL OR DELETED=0)",
        "ID_SYNTHETIC_IMAGE", params=(strimagekey, strstyleversion_),
    )


def f_next_candidate_indexes(stridentity, strrepresentation, lngcount):
    """The next `lngcount` candidate indexes whose IMAGE_KEY is still free (the lab appends;
    it never overwrites an earlier candidate, whatever its status)."""
    arrindexes, lngindex = [], 1
    while len(arrindexes) < lngcount and lngindex < 1000:
        if not cp.f_fieldfromquery(
            "SELECT ID_SYNTHETIC_IMAGE FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE WHERE IMAGE_KEY=%s",
            "ID_SYNTHETIC_IMAGE",
            params=(f_image_key(stridentity, strrepresentation, strstyleversion, lngindex),),
        ):
            arrindexes.append(lngindex)
        lngindex += 1
    return arrindexes


def f_spent_today():
    """USD spent today on renders and descriptions (the lab's daily ceiling reads this)."""
    dblimages = cp.f_fieldfromquery(
        "SELECT COALESCE(SUM(GENERATION_COST),0) AS S FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE "
        "WHERE TIM_GENERATED >= CURDATE()", "S") or 0
    dbldesc = cp.f_fieldfromquery(
        "SELECT COALESCE(SUM(T2T_COST),0) AS S FROM " + strsqlns + "T2S_SYNTHETIC_DESCRIPTION "
        "WHERE TIM_GENERATED >= CURDATE()", "S") or 0
    return round(float(dblimages) + float(dbldesc), 4)


# ---------------------------------------------------------------------------
# Entity catalogue -- what the lab can pick, per class (SYNTHETIC-IMAGES-020)
# ---------------------------------------------------------------------------
# class -> table, id column, name expression, Q-id column, overview column, order, web-search hint.
# Only classes rendered by the synthetic (text -> text -> image) path; companies and networks pad
# their real logo, lists and collections wait for the poster-mix renderer (-012 / -015).
ENTITY_CATALOG = {
    "technical": {"table": "T2S_TECHNICAL", "id": "ID_TECHNICAL",
                  "name": "COALESCE(NULLIF(WIKIDATA_LABEL,''), DESCRIPTION)", "wikidata": "ID_WIKIDATA",
                  "overview": "OVERVIEW", "order": "POPULARITY DESC", "hint": "film technique"},
    "genre": {"table": "TMDB_GENRE", "id": "id", "name": "name", "wikidata": None,
              "overview": None, "order": "name", "deleted": False, "hint": "film genre",
              "extra": "APPLIES_TO_MOVIE, APPLIES_TO_SERIE"},
    "location": {"table": "T2S_LOCATION", "id": "ID_LOCATION", "name": "LOCATION_NAME",
                 "wikidata": "ID_WIKIDATA", "overview": "OVERVIEW", "order": "POPULARITY DESC"},
    "topic": {"table": "T2S_TOPIC", "id": "ID_TOPIC", "name": "TOPIC_NAME",
              "wikidata": "ID_WIKIDATA", "overview": "OVERVIEW", "order": "POPULARITY DESC"},
    "movement": {"table": "T2S_MOVEMENT", "id": "ID_MOVEMENT", "name": "MOVEMENT_NAME",
                 "wikidata": "ID_WIKIDATA", "overview": "OVERVIEW", "order": "POPULARITY DESC",
                 "hint": "film movement"},
    "award": {"table": "T2S_AWARD", "id": "ID_AWARD", "name": "AWARD_NAME",
              "wikidata": "ID_WIKIDATA", "overview": "OVERVIEW", "order": "POPULARITY DESC",
              "hint": "film award"},
    "nomination": {"table": "T2S_NOMINATION", "id": "ID_NOMINATION", "name": "NOMINATION_NAME",
                   "wikidata": "ID_WIKIDATA", "overview": "OVERVIEW", "order": "POPULARITY DESC",
                   "hint": "film award category"},
    "group": {"table": "T2S_GROUP", "id": "ID_GROUP", "name": "GROUP_NAME",
              "wikidata": "ID_WIKIDATA", "overview": "OVERVIEW", "order": "POPULARITY DESC"},
    "death": {"table": "T2S_DEATH", "id": "ID_DEATH", "name": "DEATH_NAME",
              "wikidata": "ID_WIKIDATA", "overview": "OVERVIEW", "order": "POPULARITY DESC",
              "hint": "cause of death"},
}


def _catalog_select(dctcat):
    arrcols = ["{0} AS id_item".format(dctcat["id"]), "{0} AS name".format(dctcat["name"]),
               "{0} AS id_wikidata".format(dctcat["wikidata"] or "NULL"),
               "{0} AS overview".format(dctcat["overview"] or "NULL")]
    if dctcat.get("extra"):
        arrcols.append(dctcat["extra"])
    strwhere = "(DELETED IS NULL OR DELETED=0)" if dctcat.get("deleted", True) else "1=1"
    return "SELECT " + ", ".join(arrcols) + " FROM " + strsqlns + dctcat["table"], strwhere


def f_search_entities(stritemclass, strquery="", lnglimit=50, arrids=None):
    """Entities of a class, by name substring or by id list, most used first."""
    dctcat = ENTITY_CATALOG.get(stritemclass)
    if not dctcat:
        return []
    strsql, strwhere = _catalog_select(dctcat)
    arrparams = []
    if arrids:
        strwhere += " AND {0} IN ({1})".format(dctcat["id"], ",".join(["%s"] * len(arrids)))
        arrparams.extend(arrids)
    if strquery:
        strwhere += " AND {0} LIKE %s".format(dctcat["name"])
        arrparams.append("%" + strquery + "%")
    strsql += " WHERE " + strwhere + " ORDER BY " + dctcat["order"]
    strsql += " LIMIT {0}".format(max(1, min(int(lnglimit or 50), 500)))
    cur = cp.f_getconnection().cursor()
    cur.execute(strsql, tuple(arrparams))
    return cur.fetchall() or []


def f_get_entity(stritemclass, lngiditem):
    arrrows = f_search_entities(stritemclass, arrids=[lngiditem], lnglimit=1)
    return arrrows[0] if arrrows else None


def f_default_representation(stritemclass):
    """The class default from the representation vocabulary, else 'plate'."""
    try:
        strrep = cp.f_fieldfromquery(
            "SELECT REPRESENTATION FROM " + strsqlns + "T2S_REPRESENTATION "
            "WHERE ITEM_CLASS=%s AND IS_CHOSEN_DEFAULT=1 AND (DELETED IS NULL OR DELETED=0) LIMIT 1",
            "REPRESENTATION", params=(stritemclass,),
        )
    except Exception as err:
        print("  [representation] lookup failed for {0}: {1}".format(stritemclass, err))
        strrep = ""
    return strrep or "plate"


def f_list_representations(stritemclass):
    cur = cp.f_getconnection().cursor()
    cur.execute(
        "SELECT REPRESENTATION, REPRESENTATION_NAME, IS_CHOSEN_DEFAULT FROM " + strsqlns +
        "T2S_REPRESENTATION WHERE ITEM_CLASS=%s AND (DELETED IS NULL OR DELETED=0) "
        "AND REPRESENTATION<>'logo-pad' ORDER BY FALLBACK_ORDER", (stritemclass,))
    return cur.fetchall() or []


def f_list_candidates(stritemclass, arriditems):
    """Candidates and current choice for a set of entities: {id_item: {"candidates": [...], "chosen": id, "manual": 0/1}}."""
    dctout = {lngid: {"candidates": [], "chosen": None, "manual": 0} for lngid in arriditems}
    if not arriditems:
        return dctout
    strin = ",".join(["%s"] * len(arriditems))
    cur = cp.f_getconnection().cursor()
    cur.execute(
        "SELECT SI.ID_SYNTHETIC_IMAGE, SI.ID_ITEM, SI.CANDIDATE_INDEX, SI.IMAGE_PATH, SI.STATUS, "
        "SI.IS_VALIDATED, SI.FAILURE_REASON, SI.T2I_MODEL, SI.T2T_LLM, SI.GENERATION_COST, "
        "SI.OBJECT_DESCRIPTION, SI.T2I_PROMPT, SI.REPRESENTATION, SI.T2I_SEED, SI.STYLE_VERSION, "
        "SI.ID_SYNTHETIC_DESCRIPTION, DATE_FORMAT(SI.TIM_GENERATED, '%%Y-%%m-%%d %%H:%%i') AS TIM_GENERATED "
        "FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE SI WHERE SI.ITEM_CLASS=%s AND SI.ID_ITEM IN (" + strin + ") "
        "AND (SI.DELETED IS NULL OR SI.DELETED=0) ORDER BY SI.ID_ITEM, SI.TIM_GENERATED, SI.CANDIDATE_INDEX",
        tuple([stritemclass] + list(arriditems)))
    for arr in cur.fetchall() or []:
        if arr["ID_ITEM"] in dctout:
            dctout[arr["ID_ITEM"]]["candidates"].append(arr)
    cur.execute(
        "SELECT ID_ITEM, ID_SYNTHETIC_IMAGE, IS_MANUAL_CHOICE FROM " + strsqlns + "T2S_ENTITY_IMAGE "
        "WHERE ITEM_CLASS=%s AND ID_ITEM IN (" + strin + ") AND IS_CHOSEN=1 "
        "AND (DELETED IS NULL OR DELETED=0)", tuple([stritemclass] + list(arriditems)))
    for arr in cur.fetchall() or []:
        if arr["ID_ITEM"] in dctout:
            dctout[arr["ID_ITEM"]]["chosen"] = arr["ID_SYNTHETIC_IMAGE"]
            dctout[arr["ID_ITEM"]]["manual"] = arr.get("IS_MANUAL_CHOICE") or 0
    return dctout


def f_get_image(idimage):
    cur = cp.f_getconnection().cursor()
    cur.execute(
        "SELECT ID_SYNTHETIC_IMAGE, ITEM_CLASS, ID_ITEM, ID_WIKIDATA, REPRESENTATION, STATUS, "
        "IS_VALIDATED FROM " + strsqlns + "T2S_SYNTHETIC_IMAGE WHERE ID_SYNTHETIC_IMAGE=%s", (idimage,))
    return cur.fetchone()


# ---------------------------------------------------------------------------
# The pipeline in three steps: prepare -> describe -> render candidates
# ---------------------------------------------------------------------------
def f_prepare_entity(stritemclass=None, lngiditem=None, stridwikidata=None, strname="",
                     stroverview="", strrepresentation="", intdryrun=0):
    """
    Everything known before any model call: identity, representation, name, and the source text
    the description will read. No spend. The lab's preview is this plus the prompts it implies.
    """
    dctcat = ENTITY_CATALOG.get(stritemclass or "", {})
    if lngiditem is not None and dctcat and not intdryrun and not (strname and (stroverview or stridwikidata)):
        arrentity = f_get_entity(stritemclass, lngiditem)
        if arrentity:
            strname = strname or str(arrentity.get("name") or "")
            stroverview = stroverview or str(arrentity.get("overview") or "")
            stridwikidata = stridwikidata or arrentity.get("id_wikidata")
    if not strrepresentation:
        strrepresentation = "plate" if intdryrun else f_default_representation(stritemclass)
    strquery = (strname + " " + dctcat.get("hint", "")).strip() if strname else ""
    strsourcetext, strsource = f_resolve_source_text(stridwikidata, strname, stroverview,
                                                     intdryrun=intdryrun, strsearchquery=strquery)
    return {
        "item_class": stritemclass, "id_item": lngiditem, "id_wikidata": stridwikidata or None,
        "name": strname, "representation": strrepresentation,
        "identity": f_identity(stridwikidata, stritemclass, lngiditem),
        "source_text": strsourcetext, "source": strsource, "source_url": None,
    }


def f_render_bytes(strobjectdescription, strt2imodelused, lngseed, intdryrun=0):
    """One provider render, no DB access (safe to run in parallel). Returns a dict."""
    dblstart = time.time()
    bytesimage, lngseedused, dblcost = f_text_to_image(
        strobjectdescription, lngseed=lngseed, intdryrun=intdryrun, strmodel=strt2imodelused)
    return {"bytes": bytesimage, "seed": lngseedused, "cost": dblcost,
            "seconds": round(time.time() - dblstart, 3), "model": strt2imodelused}


def f_persist_candidate(dctprep, dctdesc, iddescription, lngindex, dctrender, intdryrun=0):
    """Validate, store and record one rendered candidate. Returns the result dict."""
    strimagekey = f_image_key(dctprep["identity"], dctprep["representation"], strstyleversion, lngindex)
    intok, strreason, w, h = f_validate_image(dctrender["bytes"])
    strstatus = "generated" if intok else "failed"
    strrelpath, lngsize = ("", 0)
    if intok:
        strrelpath, lngsize = f_store_image(dctrender["bytes"], dctprep["item_class"], strimagekey)
    arrimage = {
        "IMAGE_KEY": strimagekey,
        "ITEM_CLASS": dctprep["item_class"], "ID_ITEM": dctprep["id_item"],
        "ID_WIKIDATA": dctprep["id_wikidata"] or None,
        "CANDIDATE_INDEX": lngindex, "ID_SYNTHETIC_DESCRIPTION": iddescription,
        "REPRESENTATION": dctprep["representation"],
        "IMAGE_PATH": strrelpath or None,
        "WIDTH": w or None, "HEIGHT": h or None,
        "ASPECT_RATIO": straspectratio, "FORMAT": "webp", "FILE_SIZE": lngsize or None,
        "OBJECT_DESCRIPTION": dctdesc.get("text"),
        "T2I_PROMPT": f_build_t2i_prompt(dctdesc.get("text") or ""),
        "T2T_LLM": dctdesc.get("model"), "T2T_PROMPT_VERSION": strt2tpromptversion,
        "T2I_MODEL": dctrender["model"], "T2I_PROMPT_VERSION": strt2ipromptversion,
        "T2I_SEED": dctrender["seed"], "STYLE_VERSION": strstyleversion,
        "SOURCE": dctprep.get("source"),
        "STATUS": strstatus, "IS_VALIDATED": 1 if intok else 0,
        "FAILURE_REASON": (strreason or "no image returned") if not intok else None,
        "GENERATION_COST": dctrender["cost"], "GENERATION_TIME": dctrender["seconds"],
        "TIM_GENERATED": f_now(),
    }
    arrmapping = {"ITEM_CLASS": dctprep["item_class"], "ID_ITEM": dctprep["id_item"],
                  "ID_WIKIDATA": dctprep["id_wikidata"] or None,
                  "REPRESENTATION": dctprep["representation"]}
    idimage = None if intdryrun else f_record_image(arrimage, arrmapping)
    return {"status": strstatus, "candidate_index": lngindex, "image_key": strimagekey,
            "id_synthetic_image": idimage, "image_path": strrelpath, "cost": dctrender["cost"],
            "seconds": dctrender["seconds"], "model": dctrender["model"], "seed": dctrender["seed"],
            "failure_reason": arrimage["FAILURE_REASON"]}


def f_render_candidates(dctprep, dctdesc, iddescription, arrindexes, strt2imodelused=None,
                        lngseed=None, intdryrun=0):
    """Render one candidate per index (provider calls in parallel), then persist them in order."""
    import random
    from concurrent.futures import ThreadPoolExecutor
    strt2imodelused = strt2imodelused or strt2imodel
    arrseeds = [(lngseed + i) if lngseed is not None else random.randint(1, 2147483646)
                for i in range(len(arrindexes))]
    with ThreadPoolExecutor(max_workers=max(1, min(len(arrindexes), 4))) as pool:
        arrrenders = list(pool.map(
            lambda lngseedone: f_render_bytes(dctdesc.get("text") or "", strt2imodelused,
                                              lngseedone, intdryrun), arrseeds))
    return [f_persist_candidate(dctprep, dctdesc, iddescription, lngindex, dctrender, intdryrun)
            for lngindex, dctrender in zip(arrindexes, arrrenders)]


# ---------------------------------------------------------------------------
# The single-item harness (review §2 "primary dev loop") and the batch unit
# ---------------------------------------------------------------------------
def f_generate_synthetic_image(
    stridwikidata=None, stritemclass=None, lngiditem=None, strname="",
    stroverview="", strrepresentation="plate", lngseed=None,
    intdryrun=0, intforce=0, intcandidates=None, strt2tmodelused=None, strt2imodelused=None,
):
    """
    Generate (or resume) the candidates 1..N of one entity occurrence (N = CANDIDATES, default 3,
    SYNTHETIC-IMAGES-019): one description, N renders. Candidates already generated are skipped
    unless forced. Candidate 1 is served as soon as it exists, unless a choice was made by hand.

    Returns a result dict with STATUS and the IDs/paths involved. This is the function the
    batch driver loops over AND the function used to test one item with explicit params.
    """
    lngcandidates = int(intcandidates or lngcandidatesdefault)
    stridentity = f_identity(stridwikidata, stritemclass, lngiditem)
    arrindexes = list(range(1, max(1, lngcandidates) + 1))
    if not intforce:
        arrindexes = [i for i in arrindexes if not f_existing_image_key(
            f_image_key(stridentity, strrepresentation, strstyleversion, i), strstyleversion)]
        if not arrindexes:
            return {"status": "skipped", "reason": "exists", "cost": 0.0,
                    "image_key": f_image_key(stridentity, strrepresentation, strstyleversion),
                    "messages": ["already generated"]}

    dctprep = f_prepare_entity(stritemclass, lngiditem, stridwikidata, strname, stroverview,
                               strrepresentation, intdryrun=intdryrun)
    dctdesc = f_describe_text(dctprep["name"], dctprep["source_text"], dctprep["representation"],
                              stritemclass, intdryrun=intdryrun, strmodel=strt2tmodelused)
    arrmessages = ["source={0}".format(dctprep["source"])]
    if not (dctdesc.get("text") or "").strip():
        return {"status": "failed", "cost": dctdesc.get("cost", 0.0), "image_key": "",
                "messages": arrmessages + ["empty description"]}
    iddescription = None if intdryrun else f_record_description(dctprep, dctdesc)
    arrresults = f_render_candidates(dctprep, dctdesc, iddescription, arrindexes,
                                     strt2imodelused=strt2imodelused, lngseed=lngseed,
                                     intdryrun=intdryrun)
    arrok = [r for r in arrresults if r["status"] == "generated"]
    for r in arrresults:
        if r["status"] != "generated":
            arrmessages.append("candidate {0}: {1}".format(r["candidate_index"], r["failure_reason"]))
    if intdryrun:
        arrmessages.append("dry-run: not persisted")
    return {
        "status": "generated" if arrok else "failed",
        "image_key": (arrok or arrresults)[0]["image_key"],
        "id_synthetic_image": arrok[0]["id_synthetic_image"] if arrok else None,
        "image_path": arrok[0]["image_path"] if arrok else "",
        "candidates": arrresults, "id_synthetic_description": iddescription,
        "cost": round(dctdesc.get("cost", 0.0) + sum(r["cost"] or 0.0 for r in arrresults), 6),
        "seconds": sum(r["seconds"] for r in arrresults),
        "object_description": dctdesc.get("text"), "messages": arrmessages,
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
        "ITEM_CLASS": stritemclass, "ID_ITEM": lngiditem, "CANDIDATE_INDEX": 1,
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
