-- =============================================================================
-- 03_candidates_migration.sql -- candidates, descriptions and manual choice
-- (SYNTHETIC-IMAGES-019 / -020, 2026-10-08)
--
-- Idempotent: safe to run twice (ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS /
-- backfill guarded by IS NULL). Run it BEFORE deploying the code that writes these columns:
--
--   cd ~/docker/synthetic-images
--   git pull
--   ~/docker/tools/runsqlvaugouindb.sh ~/docker/synthetic-images/03_candidates_migration.sql
--
-- What it does:
--   1. T_WC_T2S_SYNTHETIC_IMAGE gains the entity it was made for (ITEM_CLASS, ID_ITEM), its
--      candidate number, the complete text-to-image prompt, and the description it was
--      rendered from. Several candidates per entity can now coexist (the IMAGE_KEY of
--      candidates 2..N carries the candidate number).
--   2. T_WC_T2S_SYNTHETIC_DESCRIPTION: one row per description call, with the complete
--      text-to-text prompt and its cost, stored once (never spread over the candidates).
--   3. T_WC_T2S_ENTITY_IMAGE gains IS_MANUAL_CHOICE: a choice made in the lab is never
--      undone by a later batch.
--   4. Backfill of the existing rows: every image already mapped to an entity becomes that
--      entity's candidate 1.
-- =============================================================================

-- 1. Candidates on the image artifact.
ALTER TABLE `T_WC_T2S_SYNTHETIC_IMAGE`
  ADD COLUMN IF NOT EXISTS `ITEM_CLASS` varchar(50) DEFAULT NULL AFTER `IMAGE_KEY`,
  ADD COLUMN IF NOT EXISTS `ID_ITEM` int(11) DEFAULT NULL AFTER `ITEM_CLASS`,
  ADD COLUMN IF NOT EXISTS `CANDIDATE_INDEX` int(5) DEFAULT NULL AFTER `ID_ITEM`,
  ADD COLUMN IF NOT EXISTS `ID_SYNTHETIC_DESCRIPTION` int(11) DEFAULT NULL AFTER `OBJECT_DESCRIPTION`,
  ADD COLUMN IF NOT EXISTS `T2I_PROMPT` mediumtext DEFAULT NULL AFTER `T2I_PROMPT_VERSION`,
  ADD KEY IF NOT EXISTS `ITEM_CLASS` (`ITEM_CLASS`),
  ADD KEY IF NOT EXISTS `ID_ITEM` (`ID_ITEM`),
  ADD KEY IF NOT EXISTS `CANDIDATE_INDEX` (`CANDIDATE_INDEX`),
  ADD KEY IF NOT EXISTS `ID_SYNTHETIC_DESCRIPTION` (`ID_SYNTHETIC_DESCRIPTION`);

-- 2. One row per description call.
CREATE TABLE IF NOT EXISTS `T_WC_T2S_SYNTHETIC_DESCRIPTION` (
  `ID_SYNTHETIC_DESCRIPTION` int(11) NOT NULL AUTO_INCREMENT,
  `ITEM_CLASS` varchar(50) DEFAULT NULL,
  `ID_ITEM` int(11) DEFAULT NULL,
  `ID_WIKIDATA` varchar(20) DEFAULT NULL,
  `REPRESENTATION` varchar(50) DEFAULT NULL,
  `ENTITY_NAME` varchar(500) DEFAULT NULL,
  `OBJECT_DESCRIPTION` mediumtext DEFAULT NULL,     -- the description fed to the image model
  `IS_EDITED` int(5) DEFAULT NULL,                  -- 1 = corrected by hand in the lab before rendering
  `T2T_LLM` varchar(100) DEFAULT NULL,
  `T2T_PROMPT_VERSION` varchar(20) DEFAULT NULL,
  `T2T_SYSTEM` mediumtext DEFAULT NULL,             -- system prompt, as sent
  `T2T_PROMPT` mediumtext DEFAULT NULL,             -- user prompt, as sent (source text included)
  `SOURCE` varchar(20) DEFAULT NULL,
  `SOURCE_URL` varchar(1000) DEFAULT NULL,
  `INPUT_TOKENS` int(11) DEFAULT NULL,
  `OUTPUT_TOKENS` int(11) DEFAULT NULL,             -- thinking included (billed as output)
  `T2T_COST` double DEFAULT NULL,                   -- USD, the description call only
  `GENERATION_TIME` double DEFAULT NULL,
  `TIM_GENERATED` datetime DEFAULT NULL,
  -- standard audit block
  `DELETED` int(5) DEFAULT NULL,
  `DISPLAY_ORDER` int(5) DEFAULT NULL,
  `ID_CREATOR` int(5) DEFAULT NULL,
  `DAT_CREAT` date DEFAULT NULL,
  `ID_OWNER` int(5) DEFAULT NULL,
  `TIM_UPDATED` datetime DEFAULT NULL,
  `ID_USER_UPDATED` int(5) DEFAULT NULL,
  PRIMARY KEY (`ID_SYNTHETIC_DESCRIPTION`),
  KEY `ITEM_CLASS` (`ITEM_CLASS`),
  KEY `ID_ITEM` (`ID_ITEM`),
  KEY `ID_WIKIDATA` (`ID_WIKIDATA`),
  KEY `T2T_LLM` (`T2T_LLM`),
  KEY `TIM_GENERATED` (`TIM_GENERATED`),
  KEY `DELETED` (`DELETED`),
  KEY `DAT_CREAT` (`DAT_CREAT`),
  KEY `TIM_UPDATED` (`TIM_UPDATED`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 3. Manual choice on the mapping.
ALTER TABLE `T_WC_T2S_ENTITY_IMAGE`
  ADD COLUMN IF NOT EXISTS `IS_MANUAL_CHOICE` int(5) DEFAULT NULL AFTER `IS_CHOSEN`,
  ADD KEY IF NOT EXISTS `IS_MANUAL_CHOICE` (`IS_MANUAL_CHOICE`);

-- 4. Backfill: an image already mapped to an entity is that entity's candidate 1.
UPDATE `T_WC_T2S_SYNTHETIC_IMAGE` SI
  INNER JOIN `T_WC_T2S_ENTITY_IMAGE` EI ON EI.ID_SYNTHETIC_IMAGE = SI.ID_SYNTHETIC_IMAGE
  SET SI.ITEM_CLASS = EI.ITEM_CLASS, SI.ID_ITEM = EI.ID_ITEM, SI.CANDIDATE_INDEX = 1
  WHERE SI.ITEM_CLASS IS NULL;

-- Control: what the backfill left unattached (images with no mapping row).
SELECT COUNT(*) AS IMAGES, SUM(ITEM_CLASS IS NULL) AS WITHOUT_ENTITY,
       SUM(CANDIDATE_INDEX = 1) AS CANDIDATE_1
FROM `T_WC_T2S_SYNTHETIC_IMAGE`;
