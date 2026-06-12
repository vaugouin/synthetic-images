"""
golden_set -- the fixed ~35-item evaluation set for the Project A bake-off (eval-plan.md §1).

Hand-picked to span the DISTINCT illustration challenges, not sampled randomly, so the winning
model/prompt is the one that handles ALL modes -- abstract technicals, real/fictional/by-type
places, person-roles, cultural movements, IP objects, composites, characters, the no-Wikipedia
fallback, and the dedup case. Keep this list FIXED once chosen so runs are comparable.

Each item:
  slug           unique id, used for filenames / contact-sheet cells
  category       grouping for the contact sheet (the "challenge mode")
  name           the entity label fed to the description stage (keep it unambiguous)
  item_class     a label for the row (storage subdir / provenance); not the t2t subject driver
  representation the representation hint (drives the t2t subject + the per-class rule, review §4.2)
  id_wikidata    optional Q-id (provenance; the Wikipedia-by-Qid pull is a TODO in the pipeline)
  overview       optional source-text override; when absent the pipeline resolves via web search

The harness resolves source text ONCE per item and reuses it across all t2t models, so the
description comparison is apples-to-apples (eval-plan §0 principle 3).
"""

GOLDEN_SET = [
    # 1-7 Technicals (the v1 class; abstract technical concepts -- hardest to draw)
    {"slug": "technicolor", "category": "technical", "name": "Technicolor", "item_class": "technical", "representation": "plate", "id_wikidata": "Q674564"},
    {"slug": "cinemascope", "category": "technical", "name": "CinemaScope", "item_class": "technical", "representation": "plate", "id_wikidata": "Q833561"},
    {"slug": "imax", "category": "technical", "name": "IMAX", "item_class": "technical", "representation": "plate", "id_wikidata": "Q74098"},
    {"slug": "dolby-atmos", "category": "technical", "name": "Dolby Atmos", "item_class": "technical", "representation": "plate", "id_wikidata": "Q3033860"},
    {"slug": "black-and-white", "category": "technical", "name": "Black-and-white film", "item_class": "technical", "representation": "plate", "id_wikidata": "Q838368"},
    {"slug": "35mm-film", "category": "technical", "name": "35 mm film", "item_class": "technical", "representation": "plate", "id_wikidata": "Q226528"},
    {"slug": "widescreen-239", "category": "technical", "name": "2.39:1 anamorphic widescreen format", "item_class": "technical", "representation": "plate", "id_wikidata": "Q24668302"},

    # 8-11 Locations -- real settlement / country (INSTANCE_OF -> representation rule)
    {"slug": "paris", "category": "location-real", "name": "Paris", "item_class": "location", "representation": "landscape", "id_wikidata": "Q90"},
    {"slug": "new-york-city", "category": "location-real", "name": "New York City", "item_class": "location", "representation": "landscape", "id_wikidata": "Q60"},
    {"slug": "france", "category": "location-real", "name": "France", "item_class": "location", "representation": "flag", "id_wikidata": "Q142"},
    {"slug": "sicily", "category": "location-real", "name": "Sicily", "item_class": "location", "representation": "map", "id_wikidata": "Q1460"},

    # 12-13 Locations -- street / building
    {"slug": "hollywood-blvd", "category": "location-built", "name": "Hollywood Boulevard", "item_class": "location", "representation": "street-level", "id_wikidata": "Q604582"},
    {"slug": "babelsberg-studio", "category": "location-built", "name": "Babelsberg Studio", "item_class": "location", "representation": "building", "id_wikidata": "Q705676"},

    # 14-15 Locations -- fictional (no real photo exists -> pure synthetic win)
    {"slug": "gotham-city", "category": "location-fictional", "name": "Gotham City", "item_class": "location", "representation": "artistic", "id_wikidata": "Q732858"},
    {"slug": "tatooine", "category": "location-fictional", "name": "Tatooine", "item_class": "location", "representation": "artistic", "id_wikidata": "Q723764"},

    # 16-19 Occupations (the "occupation card" -- tool/symbol of the role)
    {"slug": "film-director", "category": "occupation", "name": "Film director", "item_class": "occupation", "representation": "plate", "id_wikidata": "Q2526255"},
    {"slug": "cinematographer", "category": "occupation", "name": "Cinematographer", "item_class": "occupation", "representation": "plate", "id_wikidata": "Q222344"},
    {"slug": "film-composer", "category": "occupation", "name": "Film score composer", "item_class": "occupation", "representation": "plate", "id_wikidata": "Q1415090"},
    {"slug": "stunt-performer", "category": "occupation", "name": "Stunt performer", "item_class": "occupation", "representation": "plate", "id_wikidata": "Q465501"},

    # 20-22 Genres (mood / atmosphere, not an object)
    {"slug": "film-noir", "category": "genre", "name": "Film noir", "item_class": "genre", "representation": "plate", "id_wikidata": "Q185867"},
    {"slug": "western", "category": "genre", "name": "Western", "item_class": "genre", "representation": "plate", "id_wikidata": "Q21590660"},
    {"slug": "science-fiction", "category": "genre", "name": "Science fiction film", "item_class": "genre", "representation": "plate", "id_wikidata": "Q471839"},

    # 23-25 Movements (abstract cultural/period concepts)
    {"slug": "french-new-wave", "category": "movement", "name": "French New Wave", "item_class": "movement", "representation": "plate", "id_wikidata": "Q193541"},
    {"slug": "new-hollywood", "category": "movement", "name": "New Hollywood", "item_class": "movement", "representation": "plate", "id_wikidata": "Q377616"},
    {"slug": "german-expressionism", "category": "movement", "name": "German expressionist cinema", "item_class": "movement", "representation": "plate", "id_wikidata": "Q160476"},

    # 26-27 Awards (iconic trademarked objects -- IP + recognizability)
    {"slug": "academy-award", "category": "award", "name": "Academy Award", "item_class": "award", "representation": "object", "id_wikidata": "Q19020"},
    {"slug": "palme-dor", "category": "award", "name": "Palme d'Or", "item_class": "award", "representation": "object", "id_wikidata": "Q179808"},

    # 28 Group (multi-person abstraction)
    {"slug": "rat-pack", "category": "group", "name": "Rat Pack", "item_class": "group", "representation": "group-portrait", "id_wikidata": "Q1195790"},

    # 29-30 Country / Language (closed reference sets; language is very abstract)
    {"slug": "japan", "category": "country-language", "name": "Japan", "item_class": "country", "representation": "flag", "id_wikidata": "Q17"},
    {"slug": "japanese-language", "category": "country-language", "name": "Japanese language", "item_class": "language", "representation": "typographic", "id_wikidata": "Q5287"},

    # 31 Collection (composite poster-mix path, not Wikipedia->image)
    {"slug": "lotr-trilogy", "category": "collection", "name": "The Lord of the Rings Collection", "item_class": "collection", "representation": "composite", "id_wikidata": "Q190214"},

    # 32-33 Characters (all-synthetic class; the "bust" representation)
    {"slug": "james-bond", "category": "character", "name": "James Bond", "item_class": "character", "representation": "portrait", "id_wikidata": "Q2009573"},
    {"slug": "vito-corleone", "category": "character", "name": "Vito Corleone", "item_class": "character", "representation": "portrait", "id_wikidata": "Q745077"},

    # 34 No-Wikipedia fallback (exercises web-search / model-knowledge path, review §7)
    {"slug": "chronochrome", "category": "fallback", "name": "Chronochrome Gaumont color process", "item_class": "technical", "representation": "plate"},

    # 35 Dedup case (NYC as a topic as well as a location -> one image, two roles)
    {"slug": "nyc-topic", "category": "dedup", "name": "New York City", "item_class": "topic", "representation": "landscape", "id_wikidata": "Q60"},
]
