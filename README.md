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
│   ├── skillsfuture/
│   │   ├── seed_mapping.py
│   │   ├── seed_skillsfuture.py
│   │   └── seed_unique_skills.py
│   └── youtube/
│       ├── Youtube_API_Ingestion_Prototype_GUI.py
│       ├── youtube_config.py
│       ├── youtube_data_access.py
│       ├── youtube_ingestion_service.py
│       ├── youtube_web_app.py
│       ├── static/
│       ├── templates/
│       └── tests/
├── out/
│   ├── fixed_sf_skill_variants.csv
│   └── missing_sf_skill_lookups.csv
├── scripts/
│   └── mysql.sh
```

---

## Setup order (new users)

1. Start the MySQL + MongoDB containers (and Adminer).
2. Seed SkillsFuture data into MySQL.
3. Start the YouTube ingestion app (reads MySQL, writes MongoDB).

---

## Prerequisites

- Docker + Docker Compose
- Local Excel files in `data/raw/` (see list above) for seeding
- YouTube Data API v3 key (only needed when you run ingestion)

Optional (only if you want to run the GUI locally):
- Python 3.10+

---

## Quick start (new user setup)

### 1) Create `.env` and add data files

```bash
cp .env.example .env
# edit values if needed (passwords/ports)
```

Place the Excel files in `data/raw/` (this folder is gitignored).

### 2) Start MySQL + MongoDB + Adminer

```bash
docker compose up -d mysql mongodb adminer
```

Adminer (DB web UI) is available at:

- `http://localhost:${ADMINER_PORT}` (default `8081`)

Adminer login:
- System: `MySQL`
- Server: `mysql`
- Username: `yta` (or `root`)
- Password: from `.env`
- Database: `yta`

### 3) Seed SkillsFuture data (MySQL)

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

### 4) Run the YouTube ingestion app

**Option A: Docker (recommended)**

```bash
docker compose up -d seed_youtube
```

Open `http://localhost:5001`.

**Option B: Local**

```bash
pip install -r etl/youtube/requirements.txt
python pipelines/youtube/Youtube_API_Ingestion_Prototype_GUI.py
```

Open `http://localhost:5000`.

If running locally, ensure `.env` includes `MONGO_HOST=127.0.0.1` (or `localhost`) and your ports match Docker.

See **YouTube API Ingestion** below for details on the UI and fields.

### 5) Connect to MySQL (optional)

```bash
./scripts/mysql.sh
```

---

## MongoDB Notes

Default connection settings come from `.env`. For a local Docker setup, MongoDB is available at:

- Host: `localhost`
- Port: `${MONGO_PORT}` (default `27017`)
- Username/Password: from `.env`

Connect via Docker exec:

```bash
docker compose exec mongodb mongosh -u your_username -p your_password --authenticationDatabase admin
```

---

## Reset everything (wipe DB and re-seed)

⚠️ This deletes the MySQL and MongoDB volumes and all data.

Remove '-v' arg in compose down, if you want to keep volumes and all data

```bash
docker compose down -v
docker compose up -d mysql mongodb adminer
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

## YouTube API Ingestion

This repo includes a Flask-based GUI application for ingesting YouTube video metadata and comments using the YouTube Data API v3.
It reads sectors/skills from the seeded MySQL mapping table (`map_sf_to_cat_skill`) and upserts results into MongoDB.

### Features

- **Sector and Skill Selection**: Fetches sectors and skills from the seeded database (`map_sf_to_cat_skill` table).
- **Search and Filter**: Search skills within selected sectors (substring match).
- **YouTube API Integration**: Searches for videos, fetches video details (statistics, tags, duration), and retrieves top comments.
- **Direct MongoDB Ingestion**: Fetches data from the YouTube API and directly upserts it into MongoDB for storage and querying.
- **Mongo Browser Page**: Browse MongoDB documents and use a dedicated comment-thread lookup by `videoId`.

### Requirements (for the GUI)

- Seeded MySQL database (run SkillsFuture seeding first).
- Running MongoDB instance.
- YouTube Data API v3 key.
- Python 3.10+ if running locally (Docker option below doesn't need local Python).

### Running the GUI

After completing the setup steps above, run one of:

**Option A: Docker (recommended)**

```bash
docker compose up -d seed_youtube
```

Open `http://localhost:5001`.

**Option B: Local**

```bash
pip install -r etl/youtube/requirements.txt
python pipelines/youtube/Youtube_API_Ingestion_Prototype_GUI.py
```

Open `http://localhost:5000`.

If running locally, make sure `.env` includes `MONGO_HOST=127.0.0.1` (or `localhost`) and your MySQL/Mongo ports match the Docker ports.

### Usage (Ingestion page)

1. Open the app:
   - Docker: `http://localhost:5001`
   - Local: `http://localhost:5000`
2. Select a `sector`.
3. Search and select one or more skills.
4. Paste your YouTube API key.
5. Set ingestion parameters:
   - `Search Max Results`, `Search Order`
   - `Comments Max Results`
   - Optional constraints (`published_after`, `published_before`, `region_code`, `relevance_language`, `video_duration`, minimum views/likes)
6. Click `Fetch and Upsert Data`.
7. Read run output from:
   - run state banner (`running/success/error/warning`)
   - run summary (`Requested Skills`, `Processed Skills`, `Upserts Attempted`, `Inserted`, `Updated`, `Unchanged`, `Error Count`, `Quota Exceeded`)
8. Check the `MongoDB Status` panel for total docs, duplicate groups, recent rows, and recent run history.

### Usage (Mongo Browser page)

1. Open `/mongo_browser` from the navigation link.
2. In `Browse Documents`:
   - Choose collection, limit, and optional field filter.
   - Click `Load` (or page with `Prev`/`Next`).
3. For a video row, use:
   - `View comments` to load its comments in the dedicated lookup section.
   - `Copy ID` to copy `videoId`.
   - `Open on YouTube` to open `https://www.youtube.com/watch?v=<videoId>`.
4. In `Comment Thread Lookup`:
   - Paste a `videoId` and click `View comments`.
   - Review comment entries grouped by matching video documents.

### API Parameters

- **Search Type**: `video`.
- **Videos Part**: `snippet, statistics, contentDetails`.
- **Comments Part**: `snippet` (plaintext format).

### MongoDB Document Structure

Each document in the `videos` collection includes fields in the following order:
- `sector`: The sector name (e.g., "Accountancy").
- `skill_name`: The associated skill.
- `videoId`: YouTube video ID.
- `publishedAt`: Video publication timestamp.
- `title`: Video title.
- `description`: Video description.
- `viewCount`: Number of views.
- `likeCount`: Number of likes.
- `tags`: List of video tags.
- `comments`: List of comment objects (each with `textDisplay` and `textOriginal`).
- `duration`: Parsed video duration (e.g., "19:58").
- `ingested_timing`: Timestamp of ingestion.

Documents are upserted (updated if exists, inserted if not) to prevent duplicates.

---
