# YouTube Academy Standalone

Standalone repo that provides a **reproducible MySQL database** and a **SkillsFuture ingestion + mapping seed pipeline**.
This is intended as a clean foundation that other teammates can build on (e.g., YouTube metadata ingestion).

---

## What’s in the DB (current)

Seeded from the Excel files in `data/raw/` (this folder is gitignored by default — provide the files locally):

- **Canonical skills** from `Unique Skills List.xlsx` → `cat_skill`
- **SkillsFuture Skills Framework dataset** from `SkillsFuture Skills Framework Dataset.xlsx` → `sf_*` tables
- **Mapping** from `Skills Mapping Framework.xlsx` → `map_sf_to_cat_skill`
- **Reports** about mapping mismatches → `out/`

---

## Repo layout

```
.
├── docker-compose.yml
├── .env.example
├── .env
├── db/
│   └── init/
│       └── 001_schema.sql
├── data/
│   └── raw/
│       ├── Skills Mapping Framework.xlsx
│       ├── SkillsFuture Skills Framework Dataset.xlsx
│       └── Unique Skills List.xlsx
├── etl/
│   ├── skillsfuture/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── youtube/
│       ├── Dockerfile
│       └── requirements.txt
├── pipelines/
│   └── skillsfuture/
│       ├── seed_mapping.py
│       ├── seed_skillsfuture.py
│       └── seed_unique_skills.py
├── out/
│   ├── fixed_sf_skill_variants.csv
│   └── missing_sf_skill_lookups.csv
├── scripts/
│   └── mysql.sh
```

---

## Prerequisites

- Docker + Docker Compose

Optional (only if you want to run scripts locally):
- Python 3.10+ (venv)

---

## Quick start (recommended: seed via Docker)

### 1) Create `.env`

```bash
cp .env.example .env
# edit values if needed
```

Your `.env` controls the MySQL container and the seed job.

### 2) Start the database

```bash
docker compose up -d mysql adminer
```

Adminer (DB web UI) is available at:

- `http://localhost:${ADMINER_PORT}` (default `8081`)

Adminer login:
- System: `MySQL`
- Server: `mysql`
- Username: `yta` (or `root`)
- Password: from `.env`
- Database: `yta`

### 3) Seed SkillsFuture data

Make sure the output folder exists (so reports persist on your host):

```bash
mkdir -p out
```

Then run the seed job:

```bash
docker compose run --rm seed_skillsfuture
```

Expected console output includes:
- `✅ cat_skill upserted: ...`
- `✅ SkillsFuture seeded.`
- `✅ map_sf_to_cat_skill inserted: ...`

### 4) Connect to MySQL (inside container)

```bash
./scripts/mysql.sh
```

---

## MongoDB Setup

This project now includes MongoDB for youtube Ingestion Data.

### Prerequisites
- Docker + Docker Compose (same as MySQL)

### Quick Start for MongoDB

1. **Configure Environment Variables**  
   Your `.env` file should include:  
   ```
   MONGO_PORT=27017
   MONGO_ROOT_USERNAME=yta
   MONGO_ROOT_PASSWORD=your_password
   MONGO_DATABASE=yta
   ```

2. **Start the MongoDB Container**  
   ```bash
   docker compose up -d mongodb
   ```  
   Verify with: `docker compose ps`

3. **Connect to MongoDB**  
   - **Via Docker Exec (Interactive)**:  
     ```bash
     docker compose exec mongodb mongosh -u your_username -p your_password --authenticationDatabase admin
     ```  


---

## Reset everything (wipe DB and re-seed)

⚠️ This deletes the MySQL volume and all data.

```bash
docker compose down -v
docker compose up -d mysql adminer
mkdir -p out
docker compose run --rm seed_skillsfuture
```

---

## Outputs / reports (`out/`)

After seeding, the following reports may be produced:

- `out/missing_sf_skill_lookups.csv`  
  Mapping rows where `tsc_code` exists in the mapping file but does not exist in the SkillsFuture dataset release you have.

- `out/fixed_sf_skill_variants.csv`  
  Mapping rows that were auto-fixed by code-variant rules (e.g., appending `-1` when present in SkillsFuture codes).

These are **not fatal**; they help reconcile dataset-version differences.

---

## Validation queries

You can sanity check the seed with:

```sql
USE yta;

SELECT 'cat_skill' AS table_name, COUNT(*) AS n FROM cat_skill
UNION ALL SELECT 'sf_skill', COUNT(*) FROM sf_skill
UNION ALL SELECT 'sf_skill_level', COUNT(*) FROM sf_skill_level
UNION ALL SELECT 'sf_competency_item', COUNT(*) FROM sf_competency_item
UNION ALL SELECT 'sf_sector', COUNT(*) FROM sf_sector
UNION ALL SELECT 'sf_track', COUNT(*) FROM sf_track
UNION ALL SELECT 'sf_job_role', COUNT(*) FROM sf_job_role
UNION ALL SELECT 'sf_role_skill_req', COUNT(*) FROM sf_role_skill_req
UNION ALL SELECT 'map_sf_to_cat_skill', COUNT(*) FROM map_sf_to_cat_skill;
```

Common integrity checks (should be 0):

```sql
-- mapping rows referencing missing sf_skill
SELECT COUNT(*) FROM map_sf_to_cat_skill m
LEFT JOIN sf_skill s ON s.sf_skill_id = m.sf_skill_id
WHERE s.sf_skill_id IS NULL;

-- mapping rows referencing missing cat_skill
SELECT COUNT(*) FROM map_sf_to_cat_skill m
LEFT JOIN cat_skill c ON c.skill_id = m.skill_id
WHERE c.skill_id IS NULL;
```

---

## Notes on “Track” values

Some SkillsFuture job role records encode **multiple tracks in one cell** (e.g., separated by ` / `).
This repo currently stores those as a single `sf_track.track_name` string. If you later need true
many-to-many relationships (job role ↔ multiple tracks), introduce a junction table like
`sf_job_role_track(job_role_id, track_id)` and split during seeding.

---
