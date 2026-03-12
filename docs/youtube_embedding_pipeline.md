# YouTube Video Embedding Pipeline

## What This Is

A pipeline that takes YouTube videos stored in MongoDB (via the ingestion web app or batch script), embeds them into Qdrant, and provides a **recommender endpoint** that uses SkillsFuture skill embeddings as a semantic bridge to find the most relevant videos for a user query.

**Flow:** YouTube API → MongoDB → SentenceTransformer (BGE) → Qdrant → FastAPI recommender API

---

## API Endpoints

### Video Recommender (primary)
`POST /api/recommend/videos`

Uses SF skill embeddings as a "semantic bridge" — a user query finds the best matching skill, then that skill's rich embedding vector finds the most relevant YouTube videos.

```bash
curl -X POST http://localhost:8000/api/recommend/videos \
  -H "Content-Type: application/json" \
  -d '{"query": "AI ethics", "num_videos": 3}'
```

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
    "score": 0.60,
    "skill_title": "Data Analysis and Interpretation",
    "proficiency_level": "2",
    "category": "Analytical Thinking",
    "tsc_ccs_code": "DSN-DAT-2017-1.1",
    "skill_type": "tsc"
  },
  "recommended_videos": [
    {
      "score": 0.65,
      "video_id": "yArm1xkyD2o",
      "title": "Introduction to Data Science Principles...",
      "sector": "Infocomm Technology",
      "skill_name": "...",
      "duration": "6:54",
      "view_count": 8
    }
  ]
}
```

### Skill Search
`POST /api/search/skills`

```bash
curl -X POST http://localhost:8000/api/search/skills \
  -H "Content-Type: application/json" \
  -d '{"query": "Python", "top_k": 10}'
```

---

## How to Use

### Prerequisites
- Docker services running: `docker compose up -d mysql mongodb qdrant`

### Step 1: Ingest YouTube Videos

**Manual Ingestion (via Flask web app)**
```bash
docker compose up -d seed_youtube
# Open http://localhost:5001
```

### Step 2: Create Qdrant Collections
```bash
python qdrant/create_collections.py
```
Creates both `sf_skill_level_docs__bge_base__768` and `youtube_videos__bge_base__768` collections.

### Step 3: Embed Videos
```bash
python scripts/embed_yt_videos.py
```
Reads all videos from MongoDB, encodes them, and upserts into Qdrant. Re-run after ingesting more videos.

### Step 4: Start the API
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```
API docs at `http://localhost:8000/docs`.

---

## How the Recommender Works (Two-Hop Semantic Bridge)

1. **Encode** the user query with BGE model
2. **Search SF skills collection** (11,991 points) with the query vector → get top 1 match **with its stored vector** (`with_vectors=True`)
3. **Extract** the matched skill's 768-dim embedding vector from Qdrant
4. **Search YT videos collection** using that skill vector (not the original query vector) → get top N videos
5. **Return** the matched skill info + recommended videos

### Why This Approach
- A vague query like "how to clean data" produces a generic embedding
- The SF skill embedding contains rich context: competency descriptions, knowledge items, ability items
- Using the skill vector to search videos produces better, more targeted recommendations than searching with the raw query
- The skill framework acts as a knowledge layer that translates user intent into structured competency context

---

## Files

| File | Purpose |
|------|---------|
| `scripts/batch_ingest_yt.py` | Batch-ingests YouTube videos for all competencies across 8 Infocomm Technology skills |
| `scripts/embed_yt_videos.py` | Reads videos from MongoDB, encodes with BGE model, upserts vectors into Qdrant |
| `api/routes/recommend.py` | `POST /api/recommend/videos` — recommender endpoint (semantic bridge) |
| `api/routes/search_skills.py` | `POST /api/search/skills` — direct skill search |
| `api/routes/search_videos.py` | `POST /api/search/videos` — direct video search (useful for testing embeddings are retrievable) |
| `qdrant/create_collections.py` | Creates/validates both Qdrant collections with payload indexes |

---

## Search Score Interpretation

| Score Range | Interpretation |
|---|---|
| 0.85+ | Very strong semantic match |
| 0.70–0.85 | Good relevance |
| 0.50–0.70 | Loosely related |
| Below 0.50 | Weak/unrelated |

Longer, more specific queries produce better scores than short generic ones.

---

## Technical Details

- **Embedding model:** BAAI/bge-base-en-v1.5 (768 dimensions, cosine similarity)
- **Point IDs:** Deterministic UUID5 from `(videoId, skill_name)` — matches MongoDB unique key
- **Embedded text includes:** title, description (500 chars), tags, channel, sector, skill, competency, proficiency info
- **Qdrant payload indexes:** sector, skill_name, video_id, channel_title (for fast filtering)
- **Batch size:** 32 (configurable via `EMBEDDING_BATCH_SIZE` env var)
- **Data persistence:** All data (MongoDB, Qdrant, MySQL) stored in Docker volumes — survives restarts
