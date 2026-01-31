-- MySQL 8+ DDL (InnoDB, utf8mb4). Core 9 tables.
-- Creation order matters because of FKs.

-- 1) Canonical catalog skills (Unique Skills List)
CREATE TABLE IF NOT EXISTS cat_skill (
  skill_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  title           VARCHAR(255)     NOT NULL,
  title_norm      VARCHAR(255)     NOT NULL,
  description     TEXT            NULL,
  skill_type      ENUM('tsc','ccs') NOT NULL,
  is_emerging     TINYINT(1)       NOT NULL DEFAULT 0,
  is_casl         TINYINT(1)       NOT NULL DEFAULT 0,
  created_at      DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  -- source trace (optional but recommended)
  source_file     VARCHAR(128)     NULL,
  source_sheet    VARCHAR(128)     NULL,
  source_row      INT              NULL,
  ingested_at     DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (skill_id),
  UNIQUE KEY uk_cat_skill_title_norm (title_norm),
  KEY idx_cat_skill_title (title)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 2) SkillsFuture sector/track/job role hierarchy
CREATE TABLE IF NOT EXISTS sf_sector (
  sector_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sector_name  VARCHAR(512)     NOT NULL,
  PRIMARY KEY (sector_id),
  UNIQUE KEY uk_sf_sector_name (sector_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS sf_track (
  track_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sector_id    BIGINT UNSIGNED NOT NULL,
  track_name   VARCHAR(512)     NOT NULL,

  PRIMARY KEY (track_id),
  UNIQUE KEY uk_sf_track_sector_track (sector_id, track_name),
  KEY idx_sf_track_sector (sector_id),

  CONSTRAINT fk_sf_track_sector
    FOREIGN KEY (sector_id) REFERENCES sf_sector(sector_id)
    ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS sf_job_role (
  job_role_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  track_id         BIGINT UNSIGNED NOT NULL,
  job_role_name    VARCHAR(512)     NOT NULL,
  role_description TEXT             NULL,

  PRIMARY KEY (job_role_id),
  UNIQUE KEY uk_sf_job_role_track_role (track_id, job_role_name),
  KEY idx_sf_job_role_track (track_id),

  CONSTRAINT fk_sf_job_role_track
    FOREIGN KEY (track_id) REFERENCES sf_track(track_id)
    ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 3) SkillsFuture skill nodes (TSC/CCS codes)
CREATE TABLE IF NOT EXISTS sf_skill (
  sf_skill_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  tsc_ccs_code  VARCHAR(64)      NOT NULL,
  title         VARCHAR(512)     NOT NULL,
  description   TEXT             NULL,
  category      VARCHAR(255)     NULL,
  sector_name   VARCHAR(512)     NULL,
  skill_type    ENUM('tsc','ccs') NULL,
  is_retired    TINYINT(1)       NOT NULL DEFAULT 0,

  -- source trace
  source_file   VARCHAR(128)     NULL,
  source_sheet  VARCHAR(128)     NULL,
  source_row    INT              NULL,
  ingested_at   DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (sf_skill_id),
  UNIQUE KEY uk_sf_skill_code (tsc_ccs_code),
  KEY idx_sf_skill_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 4) SkillsFuture skill levels (composite PK)
CREATE TABLE IF NOT EXISTS sf_skill_level (
  sf_skill_id             BIGINT UNSIGNED NOT NULL,
  proficiency_level       VARCHAR(32)      NOT NULL,
  proficiency_description TEXT             NULL,

  -- source trace
  source_file             VARCHAR(128)     NULL,
  source_sheet            VARCHAR(128)     NULL,
  source_row              INT              NULL,
  ingested_at             DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (sf_skill_id, proficiency_level),
  KEY idx_sf_skill_level_skill (sf_skill_id),

  CONSTRAINT fk_sf_skill_level_skill
    FOREIGN KEY (sf_skill_id) REFERENCES sf_skill(sf_skill_id)
    ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 5) Competency items (composite FK enforced to sf_skill_level)
CREATE TABLE IF NOT EXISTS sf_competency_item (
  item_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sf_skill_id       BIGINT UNSIGNED NOT NULL,
  proficiency_level VARCHAR(32)      NOT NULL,
  item_type         ENUM('knowledge','ability') NOT NULL,
  item_text         TEXT             NOT NULL,

  -- source trace
  source_file       VARCHAR(128)     NULL,
  source_sheet      VARCHAR(128)     NULL,
  source_row        INT              NULL,
  ingested_at       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (item_id),
  KEY idx_sf_comp_item_level (sf_skill_id, proficiency_level),
  KEY idx_sf_comp_item_type (item_type),

  CONSTRAINT fk_sf_comp_item_level
    FOREIGN KEY (sf_skill_id, proficiency_level)
    REFERENCES sf_skill_level(sf_skill_id, proficiency_level)
    ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 6) Role → SF skill requirements (composite FK enforced to sf_skill_level)
CREATE TABLE IF NOT EXISTS sf_role_skill_req (
  job_role_id       BIGINT UNSIGNED NOT NULL,
  sf_skill_id       BIGINT UNSIGNED NOT NULL,
  proficiency_level VARCHAR(32)      NOT NULL,
  req_type          VARCHAR(64)      NULL,

  -- source trace
  source_file       VARCHAR(128)     NULL,
  source_sheet      VARCHAR(128)     NULL,
  source_row        INT              NULL,
  ingested_at       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (job_role_id, sf_skill_id, proficiency_level),
  KEY idx_sf_role_skill_req_skill (sf_skill_id),
  KEY idx_sf_role_skill_req_role (job_role_id),

  CONSTRAINT fk_sf_role_skill_req_role
    FOREIGN KEY (job_role_id) REFERENCES sf_job_role(job_role_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,

  CONSTRAINT fk_sf_role_skill_req_skill
    FOREIGN KEY (sf_skill_id) REFERENCES sf_skill(sf_skill_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,

  CONSTRAINT fk_sf_role_skill_req_level
    FOREIGN KEY (sf_skill_id, proficiency_level)
    REFERENCES sf_skill_level(sf_skill_id, proficiency_level)
    ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 7) Bridge: SF skill+level (+sector) → canonical catalog skill
CREATE TABLE IF NOT EXISTS map_sf_to_cat_skill (
  map_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sf_skill_id       BIGINT UNSIGNED NOT NULL,
  proficiency_level VARCHAR(32)      NOT NULL,
  skill_id          BIGINT UNSIGNED NOT NULL,
  sector_id         BIGINT UNSIGNED NULL,       -- normalized
  sector_name_raw   VARCHAR(255)     NULL,       -- traceability
  source_skill_title VARCHAR(255)    NULL,       -- debug

  -- source trace
  source_file       VARCHAR(128)     NULL,
  source_sheet      VARCHAR(128)     NULL,
  source_row        INT              NULL,
  ingested_at       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (map_id),
  KEY idx_map_skill (skill_id),
  KEY idx_map_sf_skill_level (sf_skill_id, proficiency_level),
  KEY idx_map_sector (sector_id),

  -- Uniqueness: sector-specific mapping (recommended)
  UNIQUE KEY uk_map_sf_level_sector (sf_skill_id, proficiency_level, sector_id),

  CONSTRAINT fk_map_sf_level
    FOREIGN KEY (sf_skill_id, proficiency_level)
    REFERENCES sf_skill_level(sf_skill_id, proficiency_level)
    ON UPDATE CASCADE ON DELETE RESTRICT,

  CONSTRAINT fk_map_cat_skill
    FOREIGN KEY (skill_id) REFERENCES cat_skill(skill_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,

  CONSTRAINT fk_map_sector
    FOREIGN KEY (sector_id) REFERENCES sf_sector(sector_id)
    ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- NOTE:
-- If you decide mapping is NOT sector-specific, replace the UNIQUE KEY with:
--   UNIQUE KEY uk_map_sf_level (sf_skill_id, proficiency_level)
-- and drop sector_id from the unique constraint.
