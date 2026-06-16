# YouTube Academy Standalone

Standalone repo for four connected components:

1. Reproducible MySQL schema + SkillsFuture seeding.
2. Admin tools app (Flask) for YouTube ingestion, MongoDB browsing, quiz generation, and soft-delete operations.
3. Vector indexing pipelines (Qdrant + BGE embeddings) for SkillsFuture and YouTube.
4. FastAPI endpoints for search/recommend/quiz APIs and the learner-facing academy portal.

## New Developer Start Here

If you are onboarding to the repo for the first time, follow this order:

1. Read this README first for the runtime picture and startup flow.
2. Read `docs/api_layers_integration_guide.md` for system boundaries and API ownership.
3. Read `docs/api_reference.md` for request and response contracts.
4. Start the shared services, seed SkillsFuture, then run either the ingestion app or FastAPI depending on the feature you want to work on.

Suggested working path by area:

- Admin tools, ingestion, and quiz work: `api/main.py`, `api/routes/*`, `pipelines/youtube/*`, `pipelines/quiz_gen/*`
- Search and recommendation work: `api/routes/search_videos.py`, `api/routes/search_skills.py`, `api/routes/recommend.py`, `pipelines/youtube/youtube_vector_index.py`
- Learner portal work: `api/frontend/academy/`, `api/main.py`
- Data and schema work: `db/init/001_schema.sql`, `pipelines/skillsfuture/*`, `scripts/embed_sf_skill_levels.py`

## Current Status (What Is Done vs Next)

Implemented now:

1. SkillsFuture relational pipeline into MySQL from Excel sources.
2. Mapping table population (`map_sf_to_cat_skill`) + reconciliation reports in `out/`.
3. Admin tools UI/backend with YouTube preview/upsert, Mongo browser, quiz generation, and soft-delete tools.
4. Groq-assisted query enhancement during YouTube ingestion.
5. Quiz generation flow (admin UI at `/quiz_gen` + APIs) with MongoDB storage.
   - Hierarchical filtering: sector → skill → proficiency level → competency.
   - Five quiz modes: competency, knowledge, ability, proficiency, skill.
  - Groq-assisted question generation.
   - Button state management (generate → store → reset).
6. Learner quiz taking interface (FastAPI academy portal at `/academy`).
   - Interactive quiz taking with question navigation.
   - Visual feedback: question count, selected answers, result display.
   - Quiz mode filtering applied at retrieval time.
7. SkillsFuture embedding pipeline into Qdrant.
8. YouTube embedding pipeline into Qdrant.
9. FastAPI endpoints:
   - `POST /api/search/skills`
   - `POST /api/search/videos`
   - `POST /api/recommend/videos`
   - `POST /api/quiz/generate` (hierarchical filtering by sector/skill/proficiency/competency)
   - `GET /api/quiz/{quiz_key}` (fetch previously stored quiz)
   - `POST /api/quiz/store` (persist quiz to MongoDB)
   - `GET /api/public/sectors`
   - `GET /api/public/skills`
   - `GET /api/public/skill-suggestions`
   - `GET /api/public/skill-map`
   - `POST /api/public/recommend/videos`
   - `POST /api/public/videos/preview`
   - `POST /api/public/videos/ingest`
   - `GET /academy` (learner portal)

Reference docs:

- `docs/youtube_embedding_pipeline.md`

## Documentation Index (Integration Handoff)

Use these documents as the primary package for cross-team API integration:

- `docs/api_layers_integration_guide.md` - system boundaries, API layers, and integration strategy.
- `docs/api_reference.md` - endpoint-by-endpoint contract reference with request/response examples.
- `docs/rec.md` - recommendation engine retrieval/reranking internals.
- `docs/youtube_embedding_pipeline.md` - ingestion to embedding/indexing flow.
- `docs/vector_index_versioning.md` - vector collection naming/versioning and rollback process.

If the external team does not have codebase access, start with:

1. `docs/api_layers_integration_guide.md`
2. `docs/api_reference.md`

## Repo Map

This is the quickest way to find the main entry points:

- `api/main.py` - FastAPI application entrypoint and route mounting
- `api/routes/` - FastAPI route handlers for search, recommend, quiz, and learner APIs
- `api/frontend/academy/` - learner-facing static UI served by FastAPI
- `pipelines/youtube/` - YouTube ingestion, storage, and vector indexing logic
- `pipelines/quiz_gen/` - quiz generation, storage, and MongoDB helpers
- `pipelines/skillsfuture/` - SkillsFuture seeding and mapping scripts
- `qdrant/create_collections.py` - Qdrant collection setup
- `scripts/` - one-off embedding and validation helpers
- `db/init/001_schema.sql` - MySQL schema definition

## Service Surfaces (Important)

This repo currently has two separate app surfaces:

1. Internal admin tools (prototype ops UI, Docker service: `admin_tools`)
   - Docker run: `http://localhost:5001`
   - Local run: `http://localhost:5000`
   - Includes `/` for YouTube ingestion, `/mongo_browser` for MongoDB browsing, `/quiz_gen` for generated quiz questions, and `/delete` for soft deletes.
2. Learner-facing prototype portal
   - FastAPI host (default): `http://localhost:8000`
   - Learner page: `http://localhost:8000/academy`
   - Learner APIs: `/api/public/*`

## Prerequisites

- Docker + Docker Compose
- LLM API key (Groq or OpenAI; used for quiz generation and query keyword enhancement)
- Excel files in `data/raw/`:
  - `Unique Skills List.xlsx`
  - `SkillsFuture Skills Framework Dataset.xlsx`
  - `Skills Mapping Framework.xlsx`
- YouTube Data API v3 key (used by the ingestion console and learner-portal video preview/ingest)

Optional local runtime:

- Python 3.10+
- LLM config in `.env` (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`)

## Shared Setup (Required Before Any Workflow)

### 1) Configure environment

```bash
cp .env.example .env
# edit ports/passwords if needed
# set YOUTUBE_API_KEY if you want YouTube preview/ingest from the console or learner portal
# set LLM_PROVIDER / LLM_MODEL / LLM_API_KEY for quiz generation + query keyword enhancement
```

### 2) Start core services

```bash
docker compose up -d mysql mongodb adminer
```

### 2b) Configure LLM provider, model, and API key (required for quiz + query features)

Set these in `.env`:

```env
LLM_PROVIDER=groq
LLM_MODEL=llama-3.1-8b-instant
LLM_API_KEY=<your_key>

# Alternative OpenAI setup:
# LLM_PROVIDER=openai
# LLM_MODEL=gpt-4o-mini
# LLM_API_KEY=<your_openai_key>
```

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
docker compose up -d mysql mongodb adminer qdrant

# 3) seed SkillsFuture data
mkdir -p out
docker compose run --rm seed_skillsfuture

# 4) run admin tools UI (Docker)
docker compose up -d admin_tools
# open: http://localhost:5001

# 5) local vector jobs + API (requires Python deps)
pip install -r requirements/vector_search.txt
python qdrant/create_collections.py
python scripts/embed_sf_skill_levels.py
# optional backfill/reindex for videos already stored in MongoDB
python scripts/embed_yt_videos.py
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
# open: http://localhost:8000/academy
```

New videos ingested from the console or learner portal are embedded into Qdrant automatically after upsert. `scripts/embed_yt_videos.py` remains useful for backfills or full rebuilds.

## Troubleshooting

If startup fails, check these first:

- `.env` exists and contains `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, and `YOUTUBE_API_KEY` when needed
- MySQL, MongoDB, adminer, and Qdrant are running before you start the app
- the Excel source files are present in `data/raw/`
- `python qdrant/create_collections.py` has been run before vector search or recommend flows
- `scripts/embed_sf_skill_levels.py` has been run before trying SkillsFuture search or recommendation
- the FastAPI app is running on the expected port before opening `/docs` or `/academy`

Common symptoms:

- empty recommendation results usually mean Qdrant collections or embeddings are missing
- quiz generation failures usually mean the LLM provider or API key is not configured
- preview and ingest failures usually mean the YouTube API key is missing or quota is exhausted

## Workflow A: Admin Tools / YouTube Ingestion (Implemented)

Use this when you want to fetch YouTube video metadata/comments, generate quizzes, and manage MongoDB records.

### Run the app

Option A (Docker):

```bash
docker compose up -d admin_tools
```

Open `http://localhost:5001`.

If this deployment previously used the old `seed_youtube` service name, start with:

```bash
docker compose up -d --build --remove-orphans admin_tools
```

Option B (Local):

```bash
pip install -r requirements/youtube.txt
# ensure LLM_PROVIDER / LLM_MODEL / LLM_API_KEY are set in your environment or .env
# optional: override model
# export LLM_MODEL=llama-3.1-8b-instant
python pipelines/youtube/Youtube_API_Ingestion_Prototype_GUI.py
```

Open `http://localhost:5000`.

### What this workflow currently does

- Reads sectors/skills from MySQL mapping data.
- Calls YouTube Data API v3 for videos/comments.
- Uses Groq to enrich query generation (sector/skill/competency/requirement aware).
- Upserts documents into MongoDB (`videos`, `ingestion_runs`).
- Embeds touched video-skill documents into Qdrant after successful upsert.
- Provides Mongo browser, quiz generation, and soft-delete tools at `/mongo_browser`, `/quiz_gen`, and `/delete`.

### Admin Quiz Generation (Included in this Workflow)

Access the admin quiz interface at `http://localhost:5001/quiz_gen`.

**Steps:**
1. Select **Sector** (e.g., "Infocomm")
2. Select **Skill** within that sector (e.g., "Data Analysis")
3. Select **Proficiency Level** (e.g., "Intermediate")
4. (Optional) Select **Competency** to narrow further
5. Select **Quiz Mode** to control filtering:
   - **Competency Mode**: Filter by sector → skill → proficiency → competency (all 4 levels)
   - **Knowledge Mode**: Filter by sector → skill → proficiency (excludes competency; returns all knowledge questions at that level)
   - **Ability Mode**: Filter by sector → skill → proficiency (excludes competency; returns all ability questions at that level)
   - **Proficiency Mode**: Filter by sector → skill → proficiency (includes both knowledge and ability)
   - **Skill Mode**: Filter by sector → skill only (returns all questions for that skill across all proficiency levels)
6. Click **Generate Quiz** → Groq generates 5 questions
7. Review generated questions
8. Click **Store Quiz in MongoDB** → Button shows "✓ Stored" (disabled) on success

**Button State Management:**
- **Generate**: Enabled by default; disabled while generating
- **Store**: Disabled until quiz is generated; enabled when generation completes
- **Reset States**: Automatically resets when generating a new quiz

**Quiz Data Storage (MongoDB):**
Quiz questions are stored in the `Quiz_Generation` collection with this structure:

```json
{
  "_id": ObjectId,
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "proficiency_level": "Intermediate",
  "competency": "Statistical Analysis",
  "question_type": "knowledge",
  "question": "What is a hypothesis test?",
  "options": ["A option", "B option", "C option", "D option"],
  "correct_answer": "A option",
  "deleted": false,
  "created_at": ISODate,
  "updated_at": ISODate
}
```

**Soft Delete Pattern**: Documents are marked with `deleted: true` instead of being removed.

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

### 5) Optional: backfill/reindex YouTube embeddings already stored in MongoDB

```bash
python scripts/embed_yt_videos.py
```

New videos ingested from the console or learner portal are already embedded automatically after upsert. Use this script when you need to rebuild the YouTube collection or backfill older MongoDB records.

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
  - `GET /api/public/sectors`
  - `GET /api/public/skills`
  - `GET /api/public/skill-suggestions`
  - `GET /api/public/skill-map`
  - `POST /api/public/recommend/videos`
  - `POST /api/public/videos/preview`
  - `POST /api/public/videos/ingest`

### Learner Quiz Taking (Included in this Workflow)

The learner portal at `http://localhost:8000/academy` includes:

- Fuzzy industry search and mapped skill suggestions on the main page.
- Video recommendation with an optional strict skill filter.
- A `Find Another Video` flow that previews YouTube candidates and ingests selected videos into the library.
- Interactive quiz taking.

For learner-side video preview/ingest, set `YOUTUBE_API_KEY` in `.env` and restart the FastAPI process.

**Steps:**
1. Select **Sector** and **Skill**
2. Select **Proficiency Level** and **Competency**
3. Click **Recommend Videos** to retrieve current library matches
4. If needed, click **Find Another Video** to preview and ingest fresh YouTube candidates
5. Select **Quiz Mode** and click **Take Quiz** → Loads questions from MongoDB via `/api/quiz/generate`
6. Navigate questions using arrow buttons or selector
7. Select answers (visual feedback on selection)
8. Click **Submit** → Displays results with score
9. Optionally click **Regenerate** to get a new quiz for the same filters

**Quiz Mode Filtering at Retrieval Time:**
The learner's selected quiz mode determines which MongoDB documents are retrieved:
- **Competency Mode**: `{sector, skill, proficiency_level, competency}` must all match
- **Knowledge Mode**: Filters by sector → skill → proficiency; returns only knowledge-type questions (excludes competency filter)
- **Ability Mode**: Filters by sector → skill → proficiency; returns only ability-type questions (excludes competency filter)
- **Proficiency Mode**: Filters by sector → skill → proficiency; mixes knowledge + ability questions
- **Skill Mode**: Filters by sector → skill only; ignores proficiency and competency

**Quiz API Endpoints (called by learner portal):**
- `POST /api/quiz/generate` (retrieve questions with hierarchical filtering)
- `GET /api/quiz/{quiz_key}` (fetch previously stored quiz)
- `POST /api/quiz/store` (persist quiz to MongoDB)

Example request (search skills):

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

### Quiz API Examples

**Generate Quiz (Hierarchical Filtering):**

```bash
curl -X POST "http://localhost:8000/api/quiz/generate" \
  -H "Content-Type: application/json" \
  -d "{
    \"sector\": \"Infocomm\",
    \"skill\": \"Data Analysis\",
    \"proficiency_level\": \"Intermediate\",
    \"competency\": \"Statistical Analysis\",
    \"quiz_mode\": \"competency\"
  }"
```

Response:

```json
{
  "quiz_key": "infocomm_data-analysis_intermediate_statistical-analysis_competency",
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "proficiency_level": "Intermediate",
  "competency": "Statistical Analysis",
  "quiz_mode": "competency",
  "questions": [
    {
      "question_id": 0,
      "question": "What is a hypothesis test?",
      "options": ["..."],
      "correct_answer": "..."
    }
  ],
  "generated_at": "2026-03-10T10:30:00Z"
}
```

**Retrieve Stored Quiz:**

```bash
curl "http://localhost:8000/api/quiz/infocomm_data-analysis_intermediate_statistical-analysis_competency"
```

**Store Quiz:**

```bash
curl -X POST "http://localhost:8000/api/quiz/store" \
  -H "Content-Type: application/json" \
  -d "{
    \"quiz_key\": \"infocomm_data-analysis_intermediate_statistical-analysis_competency\",
    \"user_answers\": {\"0\": \"...\", \"1\": \"...\"}
  }"
```

Versioning notes:

- `docs/vector_index_versioning.md`

## LLM Provider Notes (Quiz + Query Features)

LLM setup is already covered in **Shared Setup → 2b**.  
Groq rate limits reference: https://console.groq.com/docs/rate-limits
OpenAI rate limits reference: https://developers.openai.com/api/docs/guides/rate-limits
Quick check:

```bash
python - <<'PY'
import os
print('LLM_PROVIDER:', os.getenv('LLM_PROVIDER', 'groq'))
print('LLM_MODEL:', os.getenv('LLM_MODEL', '(provider default)'))
print('LLM_API_KEY set:', bool(os.getenv('LLM_API_KEY')))
PY
```

`LLM_API_KEY set: True` means credentials are available to the process.

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

- Confirm `LLM_PROVIDER` is `groq` or `openai`.
- Confirm `LLM_API_KEY` is set and non-empty.
- Confirm `LLM_MODEL` is valid for the selected provider.
- Confirm outbound internet access to provider API endpoint.

Quiz not found when taking ("Quiz not found, please contact admin"):

- The selected sector/skill/proficiency/competency combination may not have stored questions.
- Check MongoDB `Quiz_Generation` collection for matching documents (non-deleted).
- Ensure admin has generated and stored a quiz for this exact hierarchy.
- If quiz mode is restrictive (e.g., knowledge-only), ensure questions of that type exist.

Quiz not found when storing to MongoDB:

- Check that MongoDB connection is configured in `.env` (`MONGO_HOST`, `MONGO_PORT`, `MONGO_DB`).
- Verify MongoDB service is running (`docker compose ps`).
- Quiz store endpoint requires valid `quiz_key` matching generated quiz.

Embedding model/dimension mismatch:

- Ensure `.env` values match collection config:
  - `EMBEDDING_MODEL_NAME=BAAI/bge-base-en-v1.5`
  - `EMBEDDING_VECTOR_DIM=768`

## Repo Layout (Current)

```text
.
├── README.md
├── docker-compose.yml
├── api/
│   ├── __init__.py
│   ├── Dockerfile
│   ├── frontend/
│   │   └── academy/
│   │       ├── index.html
│   │       └── static/
│   │           ├── app.js
│   │           └── styles.css
│   ├── main.py
│   └── routes/
│       ├── learner_portal.py
│       ├── quiz.py
│       ├── recommend.py
│       ├── search_skills.py
│       ├── search_videos.py
├── data/
│   └── raw/
├── db/
│   └── init/
│       └── 001_schema.sql
├── docs/
│   ├── api_layers_integration_guide.md
│   ├── api_reference.md
│   ├── rec.md
│   ├── vector_index_versioning.md
│   └── youtube_embedding_pipeline.md
├── etl/
│   ├── skillsfuture/
│   │   └── Dockerfile
│   └── youtube/
│       └── Dockerfile
├── pipelines/
│   ├── __init__.py
│   ├── llm_client.py
│   ├── quiz_gen/
│   │   ├── __init__.py
│   │   ├── quiz_data_access.py
│   │   ├── quiz_engine.py
│   │   ├── quiz_mongo.py
│   │   ├── quiz_store.py
│   │   └── data/
│   │       └── quiz_cache.json
│   ├── skillsfuture/
│   │   ├── seed_mapping.py
│   │   ├── seed_skillsfuture.py
│   │   └── seed_unique_skills.py
│   └── youtube/
│       ├── Youtube_API_Ingestion_Prototype_GUI.py
│       ├── youtube_config.py
│       ├── youtube_data_access.py
│       ├── youtube_ingestion_service.py
│       ├── youtube_vector_index.py
│       ├── youtube_web_app.py
│       ├── static/
│       ├── templates/
│       └── tests/
├── qdrant/
│   └── create_collections.py
├── requirements/
│   ├── dev.txt
│   ├── skillsfuture.txt
│   ├── vector_search.txt
│   └── youtube.txt
├── scripts/
│   ├── embed_sf_skill_levels.py
│   ├── embed_yt_videos.py
│   ├── mysql.sh
│   └── smoke_test_qdrant_skills.py
├── sql/
│   └── get_sf_skill_level_docs.sql
```
