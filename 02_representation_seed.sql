-- =============================================================================
-- Project A — seed data for T_WC_T2S_REPRESENTATION
--
-- The controlled representation vocabulary per item class. Derived from:
--   §4.2  Locations INSTANCE_OF -> representation rule (city->landscape, country->flag/map, ...)
--   source "an-image-for-everything.md" §§93-126 (per-class representation lists)
--   §13   timeline/evolution (Technicals/Movements), poster-mix (Collections/Lists),
--         award-object IP caveat (prefer last-winner portrait)
--   decision #7 (companies/networks = pad real logo, no synthesis)
--
-- IS_CHOSEN_DEFAULT=1 marks the class default the API returns; INSTANCE_OF_RULE refines
-- the per-item pick where a class spans sub-types (locations, topics). FALLBACK_ORDER gives
-- the §9 fallback chain when the chosen representation is missing. T2I_PROMPT_SLOT is the
-- per-representation fragment of the inherited text-to-image template (§7) — English (EN
-- canonical, decision #11); the base style/aspect-ratio block lives in the template, not here.
--
-- Re-runnable: clears the table first so edits to the vocabulary take effect cleanly.
-- =============================================================================

SET NAMES utf8mb4;

DELETE FROM `T_WC_T2S_REPRESENTATION`;

INSERT INTO `T_WC_T2S_REPRESENTATION`
  (`ITEM_CLASS`,`REPRESENTATION`,`REPRESENTATION_NAME`,`REPRESENTATION_NAME_FR`,`INSTANCE_OF_RULE`,`T2I_PROMPT_SLOT`,`IS_CHOSEN_DEFAULT`,`FALLBACK_ORDER`,`DISPLAY_ORDER`,`DELETED`,`DAT_CREAT`)
VALUES
-- ---- Locations (Wikidata items via P840/P915; INSTANCE_OF drives the per-item pick, §4.2) ----
('location','day-landscape','Day landscape','Paysage de jour','city, big city, town, village, human settlement, commune of France, municipality','a representative daytime skyline or landscape view of the place as the central subject',1,1,10,0,CURDATE()),
('location','night-landscape','Night landscape','Paysage de nuit','metropolis, big city','a representative night-time cityscape of the place, illuminated, as the central subject',0,5,20,0,CURDATE()),
('location','map','Map view','Vue cartographique','country, sovereign state, region, geographic region, island, administrative territorial entity','a clean encyclopedic map plate locating the place, neutral cartographic styling',0,2,30,0,CURDATE()),
('location','flag','Flag','Drapeau','sovereign state, historical country, country, U.S. state, federal subject, autonomous region','the official flag of the place rendered as the central subject',0,4,40,0,CURDATE()),
('location','satellite','Satellite view','Vue satellite','island, archipelago, astronomical object, planet, desert','a satellite / aerial overhead view of the place as the central subject',0,3,50,0,CURDATE()),
('location','street-view','Street-level view','Vue de rue','street, square, neighborhood, district','a street-level eye-line view of the place as the central subject',0,6,60,0,CURDATE()),
('location','architecture','Architecture shot','Architecture','castle, chateau, building, railway station, film studio, palace, monument','an architectural portrait of the building/structure as the central subject',0,7,70,0,CURDATE()),
('location','artistic','Artistic illustration','Illustration artistique','fictional city, fictional country, fictional planet, mythical location','an imaginative artistic illustration of the fictional place (no real photo exists)',0,8,80,0,CURDATE()),

-- ---- Occupations (Wikidata items via P106) ----
('occupation','plate','Occupation plate','Plaque de metier','*','an encyclopedic plate depicting the occupation through its characteristic tools, attire and setting',1,1,10,0,CURDATE()),
('occupation','artistic','Artistic illustration','Illustration artistique','*','an artistic vignette of a person performing the occupation',0,2,20,0,CURDATE()),

-- ---- Characters (Wikidata via P674; all-synthetic class) ----
('character','portrait','Character bust','Buste du personnage','*','a bust portrait of the fictional character, centered, encyclopedic',1,1,10,0,CURDATE()),
('character','artistic','Full-figure illustration','Illustration en pied','*','a full-figure artistic illustration of the fictional character',0,2,20,0,CURDATE()),

-- ---- Awards (last-winner portrait dropped per user: that is a Wikipedia-image representation,
--      not a synthetic one. Award object is IP-sensitive for trademarked trophies, §13) ----
('award','award-object','Award object','Objet de la recompense','*','an encyclopedic illustration of a generic award trophy/medal in the locked style (avoid depicting trademarked real trophies such as the Oscar statuette or Palme d''Or, §13)',1,1,10,0,CURDATE()),

-- ---- Nominations (mirror awards) ----
('nomination','award-object','Award object','Objet de la recompense','*','an encyclopedic illustration of a generic award trophy/medal in the locked style (avoid depicting trademarked real trophies, §13)',1,1,10,0,CURDATE()),

-- ---- Movements (the poster/snapshot is of an ICONIC FILM that exemplifies the movement) ----
('movement','movie-poster','Iconic film poster','Affiche de film emblematique','*','a stylized poster of an iconic film that exemplifies the movement (e.g. Bonnie and Clyde for New Hollywood)',1,1,10,0,CURDATE()),
('movement','movie-snapshot','Iconic film snapshot','Photogramme de film emblematique','*','a film still from an iconic film that exemplifies the movement (e.g. Casablanca for Film noir)',0,2,20,0,CURDATE()),
('movement','group-photo','Group portrait','Photo de groupe','*','a group portrait of the figures associated with the movement',0,3,30,0,CURDATE()),
('movement','timeline','Evolution timeline','Frise chronologique','*','a horizontal evolution timeline of the movement as the central composition',0,4,40,0,CURDATE()),

-- ---- Topics (keyword-derived; CLASSIFIER_CATEGORY / Wikidata INSTANCE_OF refine the pick, §9.1) ----
('topic','plate','Encyclopedic plate','Plaque encyclopedique','other, theme, concept, object, prop, event','an encyclopedic plate illustrating the topic as the central subject',1,1,10,0,CURDATE()),
('topic','portrait','Representative portrait','Portrait representatif','job_occupation, character_type, role','a portrait representative of the topic as the central subject',0,2,20,0,CURDATE()),
('topic','map','Map view','Vue cartographique','location, place','a clean encyclopedic map plate for the location topic',0,3,30,0,CURDATE()),
('topic','artistic','Artistic illustration','Illustration artistique','religion_mythology, genre, style, animal','an artistic illustration evoking the topic',0,4,40,0,CURDATE()),

-- ---- Technicals (TECHNICAL_TYPE drives sub-type; timeline added per §13) ----
('technical','plate','Technical plate','Plaque technique','*','an encyclopedic technical-illustration plate of the apparatus/process as the central subject',1,1,10,0,CURDATE()),
('technical','timeline','Evolution timeline','Frise chronologique','*','a horizontal evolution timeline of the technique as the central composition (e.g. the evolution of Technicolor)',0,2,20,0,CURDATE()),

-- ---- Genres (closed set) ----
('genre','plate','Genre plate','Plaque de genre','*','an encyclopedic plate of iconic objects/scenes representative of the film genre',1,1,10,0,CURDATE()),
('genre','artistic','Artistic illustration','Illustration artistique','*','an artistic montage evoking the film genre',0,2,20,0,CURDATE()),

-- ---- Countries (T2S reference set, 249 closed) ----
('country','flag','Flag','Drapeau','*','the official national flag rendered as the central subject',1,1,10,0,CURDATE()),
('country','map','Map view','Vue cartographique','*','a clean encyclopedic map plate locating the country',0,2,20,0,CURDATE()),
('country','day-landscape','Day landscape','Paysage de jour','*','a representative daytime landscape of the country',0,3,30,0,CURDATE()),

-- ---- Languages (distinct spoken languages, ~184; script/typographic, NOT flags, §4.1) ----
('language','typographic','Script plate','Plaque typographique','*','a typographic plate showing the writing system / characteristic script of the language',1,1,10,0,CURDATE()),
('language','globe','Globe plate','Plaque globe','*','a globe-style plate highlighting the regions where the language is spoken (no national flag)',0,2,20,0,CURDATE()),

-- ---- Collections (no Wikipedia source; composite from member posters, §13) ----
('collection','poster-mix','Composite poster mix','Montage d''affiches','*','a composite illustration blending the posters of the collection members into one cohesive plate',1,1,10,0,CURDATE()),

-- ---- Lists (composite, like collections) ----
('list','poster-mix','Composite poster mix','Montage d''affiches','*','a composite illustration blending the posters of the list members into one cohesive plate',1,1,10,0,CURDATE()),

-- ---- Groups ----
('group','group-photo','Group portrait','Photo de groupe','*','a group portrait representative of the group as the central subject',1,1,10,0,CURDATE()),
('group','artistic','Artistic emblem','Embleme artistique','*','an artistic emblem/illustration representing the group',0,2,20,0,CURDATE()),

-- ---- Deaths (a death = a CAUSE/MANNER of death, e.g. lung cancer, plane crash; NOT a person) ----
('death','plate','Cause-of-death plate','Plaque de cause de deces','*','an encyclopedic, tasteful, non-graphic symbolic plate illustrating the cause or manner of death as a concept (no person, no graphic or distressing imagery)',1,1,10,0,CURDATE()),
('death','artistic','Artistic symbol','Symbole artistique','*','an artistic, restrained symbolic illustration evoking the cause or manner of death',0,2,20,0,CURDATE()),

-- ---- Companies / Networks (PAD the real logo onto a 2:3 canvas; no synthesis, decision #7) ----
('company','logo-pad','Padded logo','Logo encadre','*','letterbox/pad the real company logo onto a 2:3 off-white canvas (no synthesis)',1,1,10,0,CURDATE()),
('network','logo-pad','Padded logo','Logo encadre','*','letterbox/pad the real network logo onto a 2:3 off-white canvas (no synthesis)',1,1,10,0,CURDATE());
