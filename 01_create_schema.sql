-- =============================================================================
-- Project A — Synthetic entity illustrations: reference DDL
--
-- Source spec: "An image for everything - Project A review.md"
--   §5  data model (two-table design + controlled representation vocabulary)
--   §4.2 unified model: image identity keyed on ID_WIKIDATA, INSTANCE_OF drives representation
--   §6  robustness: STATUS dead-letter, IMAGE_KEY + STYLE_VERSION idempotency, seed determinism
--   §7  no-Wikipedia fallback recorded via SOURCE (wikipedia / websearch / model-knowledge)
--   §9  chosen-representation mechanism (IS_CHOSEN / DISPLAY_ORDER + per-class default + fallback)
--
-- Conventions follow doc/sql/T2S-tables.sql: backticked names, int(11) surrogate PKs,
-- the standard audit block, single-column KEY indexes, utf8mb4 / utf8mb4_general_ci, InnoDB.
-- These three tables live in the shared vaugouindb alongside the other T_WC_T2S_* tables.
-- =============================================================================

SET NAMES utf8mb4;

-- -----------------------------------------------------------------------------
-- 1. T_WC_T2S_SYNTHETIC_IMAGE — one row per generated image (the artifact).
--    Identity is the content/parameter hash IMAGE_KEY; primary entity identity
--    is ID_WIKIDATA (§4.2 / decision #14). Provenance + seed are mandatory so an
--    image can be reproduced and a STYLE_VERSION bump can mass-invalidate (§5/§8).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `T_WC_T2S_SYNTHETIC_IMAGE` (
  `ID_SYNTHETIC_IMAGE` int(11) NOT NULL AUTO_INCREMENT,
  `IMAGE_KEY` varchar(64) NOT NULL,                 -- content/parameter hash; idempotency key with STYLE_VERSION (§6)
  `ID_WIKIDATA` varchar(20) DEFAULT NULL,           -- primary image identity (#14); NULL for non-Wikidata stragglers
  `REPRESENTATION` varchar(50) DEFAULT NULL,        -- flag / day-landscape / map / artistic / timeline ... (FK-ish to T_WC_T2S_REPRESENTATION)
  `IMAGE_PATH` varchar(500) DEFAULT NULL,           -- shared_data/synthetic-images/<class>/<hash>.webp
  `WIDTH` int(11) DEFAULT NULL,
  `HEIGHT` int(11) DEFAULT NULL,
  `ASPECT_RATIO` varchar(10) DEFAULT NULL,          -- locked '2:3' (§3)
  `FORMAT` varchar(10) DEFAULT NULL,                -- 'webp' (§8)
  `FILE_SIZE` int(11) DEFAULT NULL,                 -- bytes of the master file
  `OBJECT_DESCRIPTION` mediumtext DEFAULT NULL,     -- the text-to-text output actually fed to the image model (§5/§7)
  -- provenance (mandatory — reproduce / A-B a style change across the corpus, §5)
  `T2T_LLM` varchar(100) DEFAULT NULL,              -- text-to-text model id
  `T2T_PROMPT_VERSION` varchar(20) DEFAULT NULL,
  `T2I_MODEL` varchar(100) DEFAULT NULL,            -- text-to-image model id (e.g. flux-1-schnell)
  `T2I_PROMPT_VERSION` varchar(20) DEFAULT NULL,
  `T2I_SEED` bigint(20) DEFAULT NULL,               -- determinism: re-running one item must reproduce (§6)
  `STYLE_VERSION` varchar(20) DEFAULT NULL,         -- selective mass-regeneration when the global template changes (§5/§8)
  `SOURCE` varchar(20) DEFAULT NULL,                -- 'wikipedia' / 'websearch' / 'model-knowledge' (§7)
  `SOURCE_URL` varchar(1000) DEFAULT NULL,          -- provenance URL when SOURCE='websearch' (Tavily results[].url, §7)
  `WIKIPEDIA_REVISION` varchar(50) DEFAULT NULL,    -- revision of the Wikipedia text consumed
  -- lifecycle / validation gate (§6)
  `STATUS` varchar(20) DEFAULT NULL,                -- pending / generated / rejected / failed (dead-letter)
  `IS_VALIDATED` int(5) DEFAULT NULL,               -- passed the aspect-ratio / integrity / vision-LLM gate
  `VALIDATION_SCORE` double DEFAULT NULL,           -- optional vision-LLM sanity score
  `FAILURE_REASON` mediumtext DEFAULT NULL,         -- populated on STATUS='failed' for the re-run path
  `GENERATION_COST` double DEFAULT NULL,            -- per-image cost for budget tracking
  `GENERATION_TIME` double DEFAULT NULL,            -- seconds (t2t + t2i)
  `TIM_GENERATED` datetime DEFAULT NULL,
  -- standard audit block
  `DELETED` int(5) DEFAULT NULL,
  `DISPLAY_ORDER` int(5) DEFAULT NULL,
  `ID_CREATOR` int(5) DEFAULT NULL,
  `DAT_CREAT` date DEFAULT NULL,
  `ID_OWNER` int(5) DEFAULT NULL,
  `TIM_UPDATED` datetime DEFAULT NULL,
  `ID_USER_UPDATED` int(5) DEFAULT NULL,
  PRIMARY KEY (`ID_SYNTHETIC_IMAGE`),
  UNIQUE KEY `UK_SYNTHETIC_IMAGE_KEY` (`IMAGE_KEY`),
  KEY `ID_WIKIDATA` (`ID_WIKIDATA`),
  KEY `REPRESENTATION` (`REPRESENTATION`),
  KEY `STATUS` (`STATUS`),
  KEY `STYLE_VERSION` (`STYLE_VERSION`),
  KEY `SOURCE` (`SOURCE`),
  KEY `IS_VALIDATED` (`IS_VALIDATED`),
  KEY `T2I_MODEL` (`T2I_MODEL`),
  KEY `IMAGE_PATH` (`IMAGE_PATH`),
  KEY `DELETED` (`DELETED`),
  KEY `DISPLAY_ORDER` (`DISPLAY_ORDER`),
  KEY `ID_CREATOR` (`ID_CREATOR`),
  KEY `DAT_CREAT` (`DAT_CREAT`),
  KEY `ID_OWNER` (`ID_OWNER`),
  KEY `TIM_UPDATED` (`TIM_UPDATED`),
  KEY `ID_USER_UPDATED` (`ID_USER_UPDATED`),
  KEY `TIM_GENERATED` (`TIM_GENERATED`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- -----------------------------------------------------------------------------
-- 2. T_WC_T2S_ENTITY_IMAGE — mapping from an entity occurrence to an image.
--    Makes the dedup case clean: NYC-as-location and NYC-as-topic both point at
--    one image row. ITEM_CLASS/ID_ITEM/ROLE are *labels on the mapping* (§4.2),
--    not separate image pipelines. The chosen-representation rule lives here (§9).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `T_WC_T2S_ENTITY_IMAGE` (
  `ID_ROW` int(11) NOT NULL AUTO_INCREMENT,
  `ITEM_CLASS` varchar(50) DEFAULT NULL,            -- award / nomination / topic / location / occupation / technical / collection ...
  `ID_ITEM` int(11) DEFAULT NULL,                   -- the class PK (NULL for Wikidata-role items reached only via Q-id)
  `ID_WIKIDATA` varchar(20) DEFAULT NULL,           -- entity Q-id; joins to T_WC_T2S_SYNTHETIC_IMAGE.ID_WIKIDATA
  `ID_PROPERTY` varchar(20) DEFAULT NULL,           -- property when reached as a value: P840/P915 location, P106 occupation, P674 character
  `ROLE` varchar(50) DEFAULT NULL,                  -- human label for the hat the item wears (location / occupation / character ...)
  `REPRESENTATION` varchar(50) DEFAULT NULL,        -- which representation this occurrence should use
  `ID_SYNTHETIC_IMAGE` int(11) DEFAULT NULL,        -- FK -> T_WC_T2S_SYNTHETIC_IMAGE
  `IS_CHOSEN` int(5) DEFAULT NULL,                  -- the card image the API returns for this occurrence (§9)
  `USAGE_COUNT` int(11) DEFAULT NULL,               -- MOVIE+SERIE+PERSON refs; drives the ≥5 threshold (§4.1 ②)
  -- standard audit block
  `DELETED` int(5) DEFAULT NULL,
  `DISPLAY_ORDER` int(5) DEFAULT NULL,
  `ID_CREATOR` int(5) DEFAULT NULL,
  `DAT_CREAT` date DEFAULT NULL,
  `ID_OWNER` int(5) DEFAULT NULL,
  `TIM_UPDATED` datetime DEFAULT NULL,
  `ID_USER_UPDATED` int(5) DEFAULT NULL,
  PRIMARY KEY (`ID_ROW`),
  UNIQUE KEY `UK_ENTITY_IMAGE_OCCURRENCE` (`ITEM_CLASS`,`ID_ITEM`,`ID_WIKIDATA`,`REPRESENTATION`),
  KEY `ITEM_CLASS` (`ITEM_CLASS`),
  KEY `ID_ITEM` (`ID_ITEM`),
  KEY `ID_WIKIDATA` (`ID_WIKIDATA`),
  KEY `ID_PROPERTY` (`ID_PROPERTY`),
  KEY `REPRESENTATION` (`REPRESENTATION`),
  KEY `ID_SYNTHETIC_IMAGE` (`ID_SYNTHETIC_IMAGE`),
  KEY `IS_CHOSEN` (`IS_CHOSEN`),
  KEY `USAGE_COUNT` (`USAGE_COUNT`),
  KEY `DELETED` (`DELETED`),
  KEY `DISPLAY_ORDER` (`DISPLAY_ORDER`),
  KEY `ID_CREATOR` (`ID_CREATOR`),
  KEY `DAT_CREAT` (`DAT_CREAT`),
  KEY `ID_OWNER` (`ID_OWNER`),
  KEY `TIM_UPDATED` (`TIM_UPDATED`),
  KEY `ID_USER_UPDATED` (`ID_USER_UPDATED`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- -----------------------------------------------------------------------------
-- 3. T_WC_T2S_REPRESENTATION — controlled representation vocabulary per class.
--    Keeps REPRESENTATION a closed list (not free text) so the chosen-representation
--    rule is enforceable (§5/§9). INSTANCE_OF_RULE encodes the §4.2 mapping
--    (city -> day-landscape, country -> flag/map, fictional -> artistic, ...).
--    T2I_PROMPT_SLOT is the per-representation slot of the inherited template (§7).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `T_WC_T2S_REPRESENTATION` (
  `ID_REPRESENTATION` int(11) NOT NULL AUTO_INCREMENT,
  `ITEM_CLASS` varchar(50) DEFAULT NULL,            -- class this representation applies to ('*' = any)
  `REPRESENTATION` varchar(50) DEFAULT NULL,        -- canonical key: flag / day-landscape / night-landscape / map / satellite / artistic / timeline ...
  `REPRESENTATION_NAME` varchar(100) DEFAULT NULL,
  `REPRESENTATION_NAME_FR` varchar(100) DEFAULT NULL,
  `INSTANCE_OF_RULE` varchar(200) DEFAULT NULL,     -- Wikidata INSTANCE_OF bucket(s) that select this representation (§4.2)
  `T2I_PROMPT_SLOT` mediumtext DEFAULT NULL,        -- per-representation prompt fragment (template inheritance, §7)
  `IS_CHOSEN_DEFAULT` int(5) DEFAULT NULL,          -- default chosen representation for the class (§9)
  `FALLBACK_ORDER` int(5) DEFAULT NULL,             -- position in the fallback chain when the chosen one is missing (§9)
  -- standard audit block
  `DELETED` int(5) DEFAULT NULL,
  `DISPLAY_ORDER` int(5) DEFAULT NULL,
  `ID_CREATOR` int(5) DEFAULT NULL,
  `DAT_CREAT` date DEFAULT NULL,
  `ID_OWNER` int(5) DEFAULT NULL,
  `TIM_UPDATED` datetime DEFAULT NULL,
  `ID_USER_UPDATED` int(5) DEFAULT NULL,
  PRIMARY KEY (`ID_REPRESENTATION`),
  UNIQUE KEY `UK_REPRESENTATION_CLASS` (`ITEM_CLASS`,`REPRESENTATION`),
  KEY `ITEM_CLASS` (`ITEM_CLASS`),
  KEY `REPRESENTATION` (`REPRESENTATION`),
  KEY `IS_CHOSEN_DEFAULT` (`IS_CHOSEN_DEFAULT`),
  KEY `FALLBACK_ORDER` (`FALLBACK_ORDER`),
  KEY `DELETED` (`DELETED`),
  KEY `DISPLAY_ORDER` (`DISPLAY_ORDER`),
  KEY `ID_CREATOR` (`ID_CREATOR`),
  KEY `DAT_CREAT` (`DAT_CREAT`),
  KEY `ID_OWNER` (`ID_OWNER`),
  KEY `TIM_UPDATED` (`TIM_UPDATED`),
  KEY `ID_USER_UPDATED` (`ID_USER_UPDATED`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
