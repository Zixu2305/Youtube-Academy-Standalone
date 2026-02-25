# YouTube Video Embedding Pipeline

## What This Is

A pipeline that takes YouTube videos stored in MongoDB (via the ingestion web app or batch script) and makes them semantically searchable through Qdrant vector database and a FastAPI endpoint.

**Flow:** YouTube API → MongoDB → SentenceTransformer (BGE) → Qdrant → FastAPI search API

---

## What Was Changed

### New Files

| File | Purpose |
|------|---------|
| `scripts/embed_yt_videos.py` | CLI script that reads videos from MongoDB, encodes them with the BGE embedding model, and upserts vectors into Qdrant. Idempotent — safe to re-run. |
| `scripts/batch_ingest_yt.py` | CLI script that batch-ingests YouTube videos for all competencies across 8 Infocomm Technology skills. Fetches 5 videos per competency, minimum 5 minutes each. |
| `api/routes/search_videos.py` | FastAPI `POST /api/search/videos` endpoint for semantic video search with optional filters (sector, skill_name, channel_title). |

## How to Use

### Prerequisites
- Docker services running: `docker compose up -d mysql mongodb qdrant`
- MongoDB is on port **27018** (changed from 27017 to avoid conflict with local MongoDB)

### Option A: Batch Ingestion (recommended)
Automatically ingests videos for all competencies across 8 Infocomm Technology skills:
```bash
python scripts/batch_ingest_yt.py --api-key YOUR_YOUTUBE_API_KEY
```
- Fetches 5 videos per competency, minimum 5 minutes each
- 350 competencies total — will take multiple days due to YouTube API daily quota (10,000 units)
- Idempotent — safe to re-run; picks up where it left off

### Option B: Manual Ingestion (via Flask web app)
```bash
docker compose up -d seed_youtube
# Open http://localhost:5001
```

### Embedding & Search Setup

#### Step 1: Create Qdrant Collections
```bash
python qdrant/create_collections.py
```
Creates both `sf_skill_level_docs__bge_base__768` and `youtube_videos__bge_base__768` collections.

#### Step 2: Embed Videos
```bash
python scripts/embed_yt_videos.py
```
Reads all videos from MongoDB, encodes them, and upserts into Qdrant. Re-run after ingesting more videos.

#### Step 3: Start the Search API
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```
API docs at `http://localhost:8000/docs`.

#### Step 4: Search

**YouTube videos:**
```bash
curl -X POST http://localhost:8000/api/search/videos \
  -H "Content-Type: application/json" \
  -d '{"query": "AI ethics governance", "top_k": 5}'
```

**SkillsFuture skills:**
```bash
curl -X POST http://localhost:8000/api/search/skills \
  -H "Content-Type: application/json" \
  -d '{"query": "Python", "top_k": 10}'
```

**Note on Swagger UI:** Optional filter fields show `"string"` as a placeholder. Delete any filter field you don't need — leaving `"string"` will filter for a literal match and return 0 results.

---

## Search Score Interpretation

The score is the **cosine similarity** between your query embedding and each video/skill embedding.

| Score Range | Interpretation |
|---|---|
| 0.85+ | Very strong semantic match |
| 0.70–0.85 | Good relevance |
| 0.50–0.70 | Loosely related |
| Below 0.50 | Weak/unrelated |

Longer, more specific queries produce better scores than short generic ones (e.g. "AI ethics governance tutorial" → 0.82 vs "AI" → 0.72).

---

## Technical Details

- **Embedding model:** BAAI/bge-base-en-v1.5 (768 dimensions, cosine similarity)
- **Point IDs:** Deterministic UUID5 from `(videoId, skill_name)` — matches MongoDB unique key
- **Embedded text includes:** title, description (500 chars), tags, channel, sector, skill, competency, proficiency info
- **Qdrant payload indexes:** sector, skill_name, video_id, channel_title (for fast filtering)
- **Batch size:** 32 (configurable via `EMBEDDING_BATCH_SIZE` env var)
- **Data persistence:** All data (MongoDB, Qdrant, MySQL) stored in Docker volumes — survives restarts. Only the FastAPI server needs to be started each session.

---

## Next: Video Recommender Endpoint

### What It Does
A recommender endpoint that uses SkillsFuture skill embeddings as a "semantic bridge" between user queries and YouTube videos.

**Flow:** User query → embed → search SF skills → get top skill's stored vector → search YT videos with that vector → return top 3 videos

The SF skill vector is much richer than the raw user query (it contains skill title, description, all knowledge items, all ability items). This makes it a better "search probe" into the video collection.

### Endpoint
`POST /api/recommend/videos`

**Request:**
```json
{
  "query": "how to clean messy datasets",
  "num_videos": 3
}
```

**Response:**
```json
{
  "query": "how to clean messy datasets",
  "matched_skill": {
    "score": 0.84,
    "skill_title": "Data Analytics",
    "proficiency_level": "2",
    "category": "...",
    "tsc_ccs_code": "...",
    "skill_type": "tsc"
  },
  "recommended_videos": [
    {
      "score": 0.79,
      "video_id": "abc123",
      "title": "Data Cleaning with Python",
      "sector": "Infocomm Technology",
      "skill_name": "Data Analytics",
      "duration": "15:30",
      "view_count": 50000,
      "..."
    }
  ]
}
```

### How It Works (Vector-Based Two-Hop)
1. **Encode** the user query with BGE model (same as existing endpoints)
2. **Search SF skills collection** with the query vector → get top 1 match **with its stored vector** (`with_vectors=True`)
3. **Extract** the matched skill's 768-dim embedding vector from Qdrant
4. **Search YT videos collection** using that skill vector (not the original query vector) → get top N videos
5. **Return** the matched skill info + recommended videos

### Why This Approach
- A vague query like "how to clean data" produces a generic embedding
- The SF skill "Data Analytics, Level 2" embedding contains rich context: competency descriptions, knowledge items, ability items
- Using the skill vector to search videos produces better, more targeted recommendations than searching with the raw query
- The skill framework acts as a knowledge layer that translates user intent into structured competency context

### Files to Create/Modify
| File | Action |
|------|--------|
| `api/routes/recommend.py` | **Create** — recommender endpoint |
| `api/main.py` | **Modify** — register recommend router |

### Key Implementation Detail
The critical Qdrant call must use `with_vectors=True` to retrieve the SF skill's actual embedding:
```python
hits = qdrant_client.search(
    collection_name=sf_collection,
    query_vector=query_vector,
    limit=1,
    with_payload=True,
    with_vectors=True,  # retrieves the stored 768-dim vector
)
skill_vector = hits[0].vector  # use this to search YT collection
```
