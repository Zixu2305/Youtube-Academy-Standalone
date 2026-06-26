# YouTube Video Embedding Pipeline

## What This Is

A pipeline that takes approved YouTube video mappings stored in MongoDB through the ingestion web app, learner portal, or admin review flow, embeds them into Qdrant, and supports recommendation/search endpoints over the saved video library.

The core recommender still starts with a SkillsFuture skill match, but it is no longer a simple cosine-only two-hop lookup. Current recommendation paths use BGE embeddings, Qdrant semantic retrieval, BM25 keyword retrieval, Reciprocal Rank Fusion, metadata boosts, cross-encoder reranking, and a small vote-aware ordering adjustment. The saved-library direct search path uses BGE embeddings, Qdrant semantic retrieval, BM25, RRF, and title-match boosting, but does not use the cross-encoder reranker.

**Mapped flow:** YouTube API -> MongoDB -> SentenceTransformer (BGE) -> Qdrant vector + payload -> FastAPI recommender API

**Direct-search review flow:** YouTube API -> pending MongoDB review request -> async AI/fallback mapping suggestions -> admin approval -> approved MongoDB docs -> Qdrant vector + payload

---

## API Endpoints

### Video Recommender (primary)
`POST /api/recommend/videos`

Uses SF skill embeddings as the first retrieval hop: a free-text user query finds the best matching skill, then the matched skill context drives hybrid retrieval and cross-encoder reranking over YouTube videos.

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
  "retrieval_method": "hybrid_rrf_reranked",
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
      "rrf_score": 0.032,
      "reranker_score": 0.91,
      "video_id": "yArm1xkyD2o",
      "title": "Introduction to Data Science Principles...",
      "description": "...",
      "channel_title": "Example Channel",
      "thumbnail_url": "https://...",
      "published_at": "2024-01-01T00:00:00Z",
      "duration": "PT6M54S",
      "view_count": 8,
      "like_count": 0,
      "comment_count": 0,
      "tags": ["data science"],
      "sector": "Infocomm Technology",
      "skill_name": "...",
      "competency": "...",
      "proficiency_level": "2",
      "user_votes": 0
    }
  ]
}
```

For explicit sector/skill/proficiency recommendations used by the learner portal, see `POST /api/public/recommend/videos` in `docs/api_reference.md`.

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

**Manual Ingestion (via `admin_tools` Flask app)**
```bash
docker compose up -d admin_tools
# Open http://localhost:5001
```

Direct learner/admin YouTube search submissions that do not include sector/skill/competency mappings are saved as pending mapping review requests. AI/fallback suggestions are generated asynchronously; the video is not embedded until an admin approves mappings from `/mapping_review`.

### Step 2: Create Qdrant Collections
```bash
python qdrant/create_collections.py
```
Creates both `sf_skill_level_docs__bge_base__768` and `youtube_videos__bge_base__768` collections.

### Step 3: Embed Videos
```bash
python scripts/embed_yt_videos.py
```
Reads videos from MongoDB, encodes the primary video mapping text, and upserts vectors plus payload into Qdrant. Use this for backfills, rebuilds, or MongoDB records that do not yet have Qdrant points. Mapped videos ingested through the app are embedded automatically after upsert; pending review requests are skipped until approved.

Additional mappings added through multi-labelling do not require this script. They are synchronized as Qdrant payload on the existing point.

### Step 4: Start the API
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```
API docs at `http://localhost:8000/docs`.

---

## How the Core Recommender Works

1. **Encode** the user query with the BGE model.
2. **Search the SF skills collection** with the query vector and return the top skill match with its stored vector.
3. **Search the YT videos collection semantically** using the matched skill vector.
4. **Run BM25 keyword retrieval** over saved video title/tag text using the matched skill title.
5. **Merge candidates with Reciprocal Rank Fusion** and apply metadata boosts for sector, proficiency, and mapped competency matches.
6. **Rerank the top candidates with the cross-encoder** using paired SkillsFuture and YouTube metadata text.
7. **Apply vote-aware adjustment** by swapping adjacent results when the lower-ranked item has at least three more votes.
8. **Return** matched skill info plus recommended videos with `rrf_score`, `reranker_score`, and `user_votes`.

### Why This Approach
- A vague query like "how to clean data" produces a generic embedding
- The SF skill embedding contains rich context: competency descriptions, knowledge items, ability items
- Using the skill vector to seed video retrieval produces better, more targeted candidates than searching with the raw query alone
- BM25, RRF, metadata boosts, and cross-encoder reranking improve ranking quality after the semantic candidate set is formed
- The skill framework acts as a knowledge layer that translates user intent into structured competency context

---

## Primary and Additional Mappings

Each ingested video has one primary mapping on the root MongoDB document:

- `sector`
- `skill_name`
- `competency`
- `item_type`
- `proficiency_level`
- `proficiency_description`

That primary mapping is included in the embedded text and therefore shapes the Qdrant vector. Future embeddings should continue to use only the primary mapping.

Manual multi-labelling adds curated mappings to `additional_mappings` in MongoDB. The vector does not change. Instead, the existing Qdrant point payload is updated with normalized mapping metadata:

- `competency_mappings`
- `mapped_proficiency_levels`
- `mapped_competencies`
- `mapped_competency_keys`
- `mapping_count`

The public recommendation endpoint uses these payload fields so a video can be included when the requested proficiency matches an additional mapping. Mapped competency matches are used as bounded metadata boosts. This avoids a full re-embed/reindex cycle and keeps the additional mapping update to a lightweight MongoDB write plus Qdrant payload update.

If the Qdrant point does not exist yet, run `python scripts/embed_yt_videos.py` to create the vector point first. After that, additional mapping changes can be handled by payload sync only.

---

## Admin Mapping Review Lifecycle

Unmapped direct-search submissions are stored in MongoDB `videos` documents with:

- `review_status: "pending"`
- `mapping_review.is_request: true`
- `mapping_review.suggestion_status`: `queued`, `running`, `ready`, `failed`, `manual`, or `cancelled`

The initial submit returns quickly after saving the pending request. AI/fallback mapping suggestions are filled asynchronously. Admin actions then control indexing:

- **Approve & Index**: validates seeded SkillsFuture mappings, writes approved non-request MongoDB docs, embeds them, and upserts Qdrant points.
- **Reject**: for pending requests only; marks the request rejected and creates no embeddings.
- **Reopen**: moves a rejected request back to pending.
- **Unpublish**: for approved requests; deletes approved MongoDB docs, deletes Qdrant points by `video_id`, and marks the request rejected.

Plain reject on an approved request is blocked because it would otherwise leave live Qdrant points behind.

---

## Files

| File | Purpose |
|------|---------|
| `scripts/embed_yt_videos.py` | Reads videos from MongoDB, encodes with BGE model, upserts vectors into Qdrant |
| `api/routes/recommend.py` | `POST /api/recommend/videos` — core hybrid recommendation endpoint |
| `api/routes/multi_label.py` | Public multi-label endpoints for adding/removing additional mappings and syncing Qdrant payload |
| `api/routes/search_skills.py` | `POST /api/search/skills` — direct skill search |
| `api/routes/search_videos.py` | `POST /api/search/videos` — direct video search (useful for testing embeddings are retrievable) |
| `qdrant/create_collections.py` | Creates/validates both Qdrant collections with payload indexes |

---

## Score Interpretation

Recommendation responses expose two ranking scores:

- `rrf_score`: hybrid retrieval score after RRF fusion and metadata boosts. It is mainly useful for debugging which candidates advanced to reranking.
- `reranker_score`: cross-encoder relevance score used as the primary ordering signal before vote-aware adjustment.

Saved-library direct search responses expose `score`, which is the final boosted retrieval score from Qdrant/BM25/RRF plus title-match boosting. It is not a cross-encoder score.

Scores are most useful for comparing results within the same query. Do not treat them as calibrated probabilities across unrelated queries.

---

## Technical Details

- **Embedding model:** BAAI/bge-base-en-v1.5 (768 dimensions, cosine similarity)
- **Point IDs:** Deterministic UUID5 from `(videoId, skill_name, proficiency_level, competency)` so one video can have separate live points for multiple approved mappings
- **Embedded text includes:** title, description (500 chars), tags, channel, sector, skill, primary competency, primary proficiency info
- **Qdrant payload includes:** primary video metadata plus normalized mapping fields for all primary/additional mappings
- **Qdrant payload indexes:** sector, skill_name, video_id, channel_title (for fast filtering)
- **Batch size:** 32 (configurable via `EMBEDDING_BATCH_SIZE` env var)
- **Data persistence:** All data (MongoDB, Qdrant, MySQL) stored in Docker volumes — survives restarts
