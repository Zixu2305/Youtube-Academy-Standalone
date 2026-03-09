# YouTube Academy Standalone

Standalone repo for four connected components:

1. Reproducible MySQL schema + SkillsFuture seeding.
2. YouTube ingestion app (Flask) that writes to MongoDB.
3. Vector indexing pipelines (Qdrant + BGE embeddings) for SkillsFuture and YouTube.
4. FastAPI endpoints for search/recommend/quiz APIs and the learner-facing academy portal.

## Current Status (What Is Done vs Next)

Implemented now:

1. SkillsFuture relational pipeline into MySQL from Excel sources.
2. Mapping table population (`map_sf_to_cat_skill`) + reconciliation reports in `out/`.
3. YouTube ingestion UI/backend with preview/upsert, Mongo browser, and soft-delete tools.
4. Ollama-assisted query enhancement during YouTube ingestion.
5. Quiz generation flow (UI + APIs) with cache + MongoDB storage.
6. SkillsFuture embedding pipeline into Qdrant.
7. YouTube embedding pipeline into Qdrant.
8. FastAPI endpoints:
   - `POST /api/search/skills`
   - `POST /api/search/videos`
   - `POST /api/recommend/videos`
   - `POST /api/quiz/generate`
   - `GET /api/quiz/{quiz_key}`
   - `POST /api/quiz/store`
   - `GET /api/public/sectors`
   - `GET /api/public/skills`
   - `GET /api/public/skill-map`
   - `POST /api/public/recommend/videos`
   - `GET /academy` (learner portal)

Reference docs:

- `docs/youtube_embedding_pipeline.md`

## Service Surfaces (Important)

This repo currently has two separate app surfaces:

1. Internal ingestion/admin tools (prototype ops UI)
   - Docker run: `http://localhost:5001`
   - Local run: `http://localhost:5000`
   - Includes `/mongo_browser`, `/quiz_gen`, `/delete`, and ingestion controls.
2. Learner-facing prototype portal
   - FastAPI host (default): `http://localhost:8000`
   - Learner page: `http://localhost:8000/academy`
   - Learner APIs: `/api/public/*`

## Prerequisites

- Docker + Docker Compose
- Ollama runtime (Docker service is included in `docker-compose.yml`; local install is optional if running Python directly)
- Excel files in `data/raw/`:
  - `Unique Skills List.xlsx`
  - `SkillsFuture Skills Framework Dataset.xlsx`
  - `Skills Mapping Framework.xlsx`
- YouTube Data API v3 key (for YouTube ingestion only)

Optional local runtime:

- Python 3.10+
- Ollama CLI/runtime (for local quiz/query features)

## Shared Setup (Required Before Any Workflow)

### 1) Configure environment

```bash
cp .env.example .env
# edit ports/passwords if needed
```

### 2) Start core services

```bash
docker compose up -d mysql mongodb adminer
```

### 2b) Start Ollama services (required for quiz + query features)

```bash
docker compose up -d ollama ollama-init
```

`ollama-init` pulls `llama3.2:3b` once and then exits.

Adminer:

- URL: `http://localhost:${ADMINER_PORT}` (default `8081`)
- System: `MySQL`
- Server: `mysql`
- Username: `yta` (or `root`)
- Password: from `.env`
- Database: `yta`

### 3) Seed SkillsFuture into MySQL

```bash
mkdir -p out
docker compose run --rm seed_skillsfuture
```

Expected logs include:

- `✅ cat_skill upserted: ...`
- `✅ SkillsFuture seeded.`
- `✅ map_sf_to_cat_skill inserted: ...`

### 4) Optional quick validation

```bash
./scripts/mysql.sh
```

Then run:

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

## Quickstart (Happy Path)

Use this when you want to run both internal ingestion/admin tools and the learner prototype end-to-end.

```bash
# 1) one-time env setup
cp .env.example .env

# 2) start core infra
docker compose up -d mysql mongodb adminer ollama ollama-init qdrant

# 3) seed SkillsFuture data
mkdir -p out
docker compose run --rm seed_skillsfuture

# 4) run internal ingestion UI (Docker)
docker compose up -d seed_youtube
# open: http://localhost:5001

# 5) local vector jobs + API (requires Python deps)
pip install -r requirements/vector_search.txt
python qdrant/create_collections.py
python scripts/embed_sf_skill_levels.py
python scripts/embed_yt_videos.py
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
# open: http://localhost:8000/academy
```

## Workflow A: YouTube Ingestion (Implemented)

Use this when you want to fetch YouTube video metadata/comments, generate quizzes, and manage MongoDB records.

### Run the app

Option A (Docker):

```bash
docker compose up -d ollama ollama-init seed_youtube
```

Open `http://localhost:5001`.

Option B (Local):

```bash
pip install -r requirements/youtube.txt
ollama pull llama3.2:3b
# start Ollama daemon if not already running (separate terminal):
# ollama serve
# if running all components locally:
export OLLAMA_HOST=http://127.0.0.1:11434
# if app is local but Ollama is in Docker:
# export OLLAMA_HOST=http://localhost:11434
python pipelines/youtube/Youtube_API_Ingestion_Prototype_GUI.py
```

Open `http://localhost:5000`.

### What this workflow currently does

- Reads sectors/skills from MySQL mapping data.
- Calls YouTube Data API v3 for videos/comments.
- Uses Ollama to enrich query generation (sector/skill/competency/requirement aware).
- Upserts documents into MongoDB (`videos`, `ingestion_runs`).
- Supports quiz generation/storage and soft-delete operations.
- Provides pages at `/mongo_browser`, `/quiz_gen`, and `/delete`.

## Workflow B: Vector Indexing + Search/Recommend API + Learner Portal (Implemented)

Use this when you want semantic retrieval across SkillsFuture and YouTube data.

### 1) Start Qdrant

```bash
docker compose up -d qdrant
curl http://localhost:6333/healthz
```

### 2) Install vector-search dependencies

```bash
pip install -r requirements/vector_search.txt
```

### 3) Create/validate collections

```bash
python qdrant/create_collections.py
```

Defaults:

- SkillsFuture collection: `sf_skill_level_docs__bge_base__768`
- YouTube collection: `youtube_videos__bge_base__768`
- Model: `BAAI/bge-base-en-v1.5`
- Dimension: `768`
- Distance: cosine

### 4) Build SkillsFuture embeddings and upsert

```bash
python scripts/embed_sf_skill_levels.py
```

### 5) Build YouTube embeddings and upsert (after YouTube ingestion)

```bash
python scripts/embed_yt_videos.py
```

### 6) Smoke test SkillsFuture vector index (optional)

```bash
python scripts/smoke_test_qdrant_skills.py
```

### 7) Run FastAPI

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- Docs: `http://localhost:8000/docs`
- Learner portal: `http://localhost:8000/academy`
- Endpoints:
  - `POST /api/search/skills`
  - `POST /api/search/videos`
  - `POST /api/recommend/videos`
  - `POST /api/quiz/generate`
  - `GET /api/quiz/{quiz_key}`
  - `POST /api/quiz/store`
  - `GET /api/public/sectors`
  - `GET /api/public/skills`
  - `GET /api/public/skill-map`
  - `POST /api/public/recommend/videos`

Example request:

```json
{
  "query": "data analysis",
  "top_k": 10,
  "exclude_retired": true,
  "skill_type": null,
  "category": null,
  "include_details": true
}
```

Versioning notes:

- `docs/vector_index_versioning.md`

## Ollama Notes (Quiz + Query Features)

Ollama setup is already covered in **Shared Setup → 2b**.  
Quick check:

```bash
curl http://localhost:11434/api/tags
```

You should see `llama3.2:3b`.

## Outputs

Generated by SkillsFuture seed pipeline:

- `out/missing_sf_skill_lookups.csv`
- `out/fixed_sf_skill_variants.csv`

These are reconciliation reports and not fatal by themselves.

## Reset and Rebuild

Reset MySQL + MongoDB and reseed:

```bash
docker compose down -v
docker compose up -d mysql mongodb adminer
mkdir -p out
docker compose run --rm seed_skillsfuture
```

Reset everything including Qdrant and rebuild vectors:

```bash
docker compose down -v
docker compose up -d mysql mongodb adminer qdrant
mkdir -p out
docker compose run --rm seed_skillsfuture
python qdrant/create_collections.py
python scripts/embed_sf_skill_levels.py
```

## Troubleshooting

SkillsFuture seed fails with missing source files:

- Confirm the three Excel files exist in `data/raw/` with exact names.

Local YouTube run cannot connect to MongoDB:

- Check `.env` values like `MONGO_HOST=127.0.0.1` and `MONGO_PORT=27017`.

FastAPI docs not reachable:

- Ensure `uvicorn` is running on port `8000`.
- Qdrant is separate on port `6333`.
- Learner portal should be at `http://localhost:8000/academy`.

Quiz generation or query enhancement fails:

- Confirm Ollama is running and reachable at `OLLAMA_HOST`.
- Confirm model `llama3.2:3b` is available (`curl http://localhost:11434/api/tags`).

Embedding model/dimension mismatch:

- Ensure `.env` values match collection config:
  - `EMBEDDING_MODEL_NAME=BAAI/bge-base-en-v1.5`
  - `EMBEDDING_VECTOR_DIM=768`

## Repo Layout (Current)

```text
.
├── api/
│   ├── frontend/
│   │   └── academy/
│   │       ├── index.html
│   │       └── static/
│   │           ├── app.js
│   │           └── styles.css
│   ├── main.py
│   └── routes/
│       ├── learner_portal.py
│       ├── search_skills.py
│       ├── search_videos.py
│       ├── recommend.py
│       └── quiz.py
├── db/init/001_schema.sql
├── docker-compose.yml
├── docs/vector_index_versioning.md
├── docs/youtube_embedding_pipeline.md
├── etl/
│   ├── skillsfuture/
│   └── youtube/
├── pipelines/
│   ├── skillsfuture/
│   ├── youtube/
│   └── quiz_gen/
├── qdrant/create_collections.py
├── requirements/
│   ├── dev.txt
│   ├── skillsfuture.txt
│   ├── vector_search.txt
│   └── youtube.txt
├── scripts/
│   ├── embed_sf_skill_levels.py
│   ├── embed_yt_videos.py
│   ├── mysql.sh
│   ├── batch_ingest_yt.py
│   └── smoke_test_qdrant_skills.py
├── sql/get_sf_skill_level_docs.sql
└── README.md
```
