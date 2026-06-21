# YouTube Academy API Reference

This document is the contract reference for integrating this repository as an external recommendation and learning service.

Base URL (local default):
- `http://localhost:8000`

OpenAPI UI (generated from FastAPI):
- `/docs`

Service scope:
- Core retrieval APIs for skill search, video search, and recommendation
- Quiz generation and retrieval APIs
- Public learner APIs used by the `/academy` frontend
- Public multi-label APIs used by the admin curation flow
- Public job role lookup APIs used by the `/academy/job-roles` frontend

## API Layer Map

1. Core engine APIs (`/api/search/*`, `/api/recommend/*`, `/api/quiz/*`)
2. Public learner APIs (`/api/public/*`)
3. Learner page route (`/academy`)

## Common Behaviors

- Content type for POST requests: `application/json`
- Empty input in manually validated handlers generally returns `400`
- Schema validation errors for request body/query params are returned by FastAPI as `422`
- Missing resource generally returns `404`
- Backend dependency failure (Qdrant/MySQL/Mongo/LLM) generally returns `500` or `503`
- LLM rate limiting in quiz generation returns `429`

## First Verification Calls

If you just brought the service up and want a quick sanity check, run these in order:

1. `GET /api/public/sectors` - confirms MySQL-backed discovery data is available
2. `GET /api/public/skills?sector=Infocomm` - confirms sector-scoped skill lookup works
3. `GET /api/public/skill-map?sector=Infocomm&skill=Data%20Analysis` - confirms mapped proficiency data is available
4. `GET /api/public/job-roles?limit=5` - confirms job role lookup data is available
5. `POST /api/public/recommend/videos` - confirms retrieval and ranking are wired end to end
6. `POST /api/search/skills` - confirms direct vector search against Qdrant is available
7. `POST /api/public/videos/search` with `"source": "library"` - confirms saved library semantic search is available

Example curl commands:

```bash
curl http://localhost:8000/api/public/sectors
curl "http://localhost:8000/api/public/skills?sector=Infocomm"
curl "http://localhost:8000/api/public/skill-map?sector=Infocomm&skill=Data%20Analysis"
curl "http://localhost:8000/api/public/job-roles?limit=5"
curl -X POST http://localhost:8000/api/public/recommend/videos \
  -H "Content-Type: application/json" \
  -d '{"sector":"Infocomm","skill":"Data Analysis","proficiency_level":"2","competency":"knowledge: Understand data distributions","top_k":3,"strict_skill_match":true}'
curl -X POST http://localhost:8000/api/search/skills \
  -H "Content-Type: application/json" \
  -d '{"query":"data analysis","top_k":5}'
curl -X POST http://localhost:8000/api/public/videos/search \
  -H "Content-Type: application/json" \
  -d '{"query":"simple tutorial for accountancy basics","max_results":8,"source":"library"}'
```

---

## 1) Core Engine APIs

### POST `/api/search/skills`
Semantic search against SkillsFuture vectors in Qdrant, with optional filtering and MySQL detail expansion.

Request body:
```json
{
  "query": "data analysis",
  "top_k": 10,
  "exclude_retired": true,
  "skill_type": "tsc",
  "category": "Infocomm",
  "include_details": true
}
```

Request fields:
- `query` (string, required, min length 1)
- `top_k` (integer, optional, default 10, range 1-50)
- `exclude_retired` (boolean, optional, default true)
- `skill_type` (optional enum: `tsc` or `ccs`)
- `category` (string, optional)
- `include_details` (boolean, optional, default true)

Response shape:
```json
{
  "query": "data analysis",
  "collection": "sf_skill_level_docs__bge_base__768",
  "model_name": "BAAI/bge-base-en-v1.5",
  "count": 2,
  "results": [
    {
      "point_id": "123:2",
      "score": 0.83,
      "payload": {
        "sf_skill_id": 123,
        "proficiency_level": "2",
        "tsc_ccs_code": "DSN-DAT-2017-1.1",
        "skill_title": "Data Analysis",
        "category": "Infocomm",
        "skill_type": "tsc",
        "is_retired": false
      },
      "details": {
        "sf_skill_id": 123,
        "proficiency_level": "2",
        "proficiency_description": "...",
        "skill_description": "...",
        "sector_name": "...",
        "knowledge_items": ["..."],
        "ability_items": ["..."]
      }
    }
  ]
}
```

Example:
```bash
curl -X POST http://localhost:8000/api/search/skills \
  -H "Content-Type: application/json" \
  -d '{"query":"data analysis","top_k":5}'
```

---

### POST `/api/search/videos`
Semantic search against YouTube vectors in Qdrant with optional metadata filters.

Request body:
```json
{
  "query": "python pandas tutorial",
  "top_k": 10,
  "sector": "Infocomm",
  "skill_name": "Data Analysis",
  "channel_title": "freeCodeCamp.org"
}
```

Request fields:
- `query` (string, required)
- `top_k` (integer, optional, default 10, range 1-50)
- `sector` (string, optional)
- `skill_name` (string, optional)
- `channel_title` (string, optional)

Response shape:
```json
{
  "query": "python pandas tutorial",
  "collection": "youtube_videos__bge_base__768",
  "model_name": "BAAI/bge-base-en-v1.5",
  "count": 2,
  "results": [
    {
      "score": 0.81,
      "video_id": "abcd1234",
      "title": "...",
      "description": "...",
      "channel_title": "...",
      "thumbnail_url": "...",
      "published_at": "...",
      "duration": "PT12M10S",
      "view_count": 1000,
      "like_count": 120,
      "comment_count": 34,
      "tags": ["python", "pandas"],
      "sector": "Infocomm",
      "skill_name": "Data Analysis",
      "competency": "...",
      "proficiency_level": "2"
    }
  ]
}
```

---

### POST `/api/recommend/videos`
Two-hop hybrid recommendation pipeline.

Pipeline summary:
1. Query -> top SF skill match
2. Skill -> YT candidates via semantic retrieval + BM25
3. Reciprocal rank fusion + metadata boosts
4. Cross-encoder rerank
5. Vote-aware post-adjustment

Request body:
```json
{
  "query": "how to clean messy data",
  "num_videos": 3
}
```

Request fields:
- `query` (string, required)
- `num_videos` (integer, optional, default 3, range 1-20)

Response shape:
```json
{
  "query": "how to clean messy data",
  "retrieval_method": "hybrid_rrf_reranked",
  "matched_skill": {
    "score": 0.76,
    "skill_title": "Data Analysis",
    "proficiency_level": "2",
    "category": "Infocomm",
    "tsc_ccs_code": "...",
    "skill_type": "tsc"
  },
  "recommended_videos": [
    {
      "rrf_score": 0.03,
      "reranker_score": 0.92,
      "video_id": "abcd1234",
      "title": "...",
      "description": "...",
      "channel_title": "...",
      "thumbnail_url": "...",
      "published_at": "...",
      "duration": "PT12M10S",
      "view_count": 1000,
      "like_count": 120,
      "comment_count": 34,
      "tags": ["python", "pandas"],
      "sector": "Infocomm",
      "skill_name": "Data Analysis",
      "competency": "...",
      "proficiency_level": "2",
      "user_votes": 5
    }
  ]
}
```

Key failures:
- `404` if no skill match is found
- `503` if embedding/reranker model is unavailable
- `500` if Qdrant call fails

---

### POST `/api/quiz/generate`
Generates (or fetches cached) quiz questions for a selected path and mode.

Behavior notes:
- Target behavior is to return 5 questions.
- If a cached quiz exists with fewer than 5 questions and backfill generation fails, the endpoint may return fewer than 5 so existing content is still available.

Request body:
```json
{
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "competency": "knowledge: Understand data distributions",
  "proficiency_level": "2",
  "proficiency_description": "Can perform intermediate analysis",
  "quiz_mode": "competency"
}
```

Request fields:
- `sector` (string, required)
- `skill` (string, required)
- `competency` (string, required)
- `proficiency_level` (string, required)
- `proficiency_description` (string, optional)
- `quiz_mode` (string, required): `competency`, `knowledge`, `ability`, `proficiency`, `skill`

Response shape:
```json
{
  "quiz_key": "<stable-key>",
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "competency": "knowledge: Understand data distributions",
  "proficiency_level": "2",
  "proficiency_description": "Can perform intermediate analysis",
  "questions": [
    {
      "question_number": 1,
      "question": "...",
      "options": {
        "A": "...",
        "B": "...",
        "C": "...",
        "D": "..."
      },
      "correct": "A",
      "explanation": "..."
    }
  ]
}
```

Key failures:
- `429` when LLM provider rate limit is hit
- `500` for generation/storage dependency errors

---

### GET `/api/quiz/{quiz_key}`
Fetches previously stored quiz by key.

Response:
- Same shape as `POST /api/quiz/generate`

Failures:
- `404` when not found

---

### POST `/api/quiz/store`
Persists a generated quiz payload into MongoDB.

Request body:
```json
{
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "competency": "knowledge: Understand data distributions",
  "proficiency_level": "2",
  "proficiency_description": "Can perform intermediate analysis",
  "item_type": "knowledge",
  "questions": [
    {
      "question_number": 1,
      "question": "...",
      "options": {
        "A": "...",
        "B": "...",
        "C": "...",
        "D": "..."
      },
      "correct": "A",
      "explanation": "..."
    }
  ]
}
```

Response:
```json
{
  "success": true,
  "mongo_id": "67f...",
  "message": "Quiz stored successfully"
}
```

---

## 2) Public Learner APIs

These are the preferred APIs for external platform integration.

### GET `/api/public/sectors`
Returns available sectors and mapped skill counts.

Response item:
```json
{
  "sector": "Infocomm",
  "skill_count": 42
}
```

---

### GET `/api/public/skills`
Returns skills (optionally filtered by sector and free-text query).

Query params:
- `sector` (optional)
- `q` (optional, max length 120)
- `limit` (optional, default 400, range 1-1000)

Response item:
```json
{
  "skill": "Data Analysis",
  "mapped_proficiency_count": 8
}
```

---

### GET `/api/public/skill-suggestions`
Typeahead/fuzzy skill suggestions.

Query params:
- `q` (required, min length 2)
- `limit` (optional, default 6, range 1-12)

Response item:
```json
{
  "skill": "Data Analysis",
  "sector_count": 2,
  "total_mapped_proficiency_count": 8,
  "match_score": 8.145,
  "sectors": [
    {
      "sector": "Infocomm",
      "mapped_proficiency_count": 6
    }
  ]
}
```

---

### GET `/api/public/skill-map`
Returns mapped proficiency levels and competency items for a sector+skill pair.

Query params:
- `sector` (required)
- `skill` (required)

Response shape:
```json
{
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "count": 2,
  "mappings": [
    {
      "proficiency_level": "1",
      "proficiency_description": "...",
      "mapped_skill_ids": [1001, 1002],
      "knowledge_items": ["..."],
      "ability_items": ["..."]
    }
  ]
}
```

Failures:
- `404` when no mapped data exists

---

### POST `/api/public/multi-label/search-videos`
Searches ingested MongoDB videos so an admin can review and edit their competency mappings.

Request body:
```json
{
  "query": "ASEAN Guide on AI Governance",
  "limit": 20,
  "search_type": "title"
}
```

Request fields:
- `query` (string, required, min length 1)
- `limit` (integer, optional, default 20, range 1-100)
- `search_type` (optional enum: `title`, `video_id`, `skill`; default `title`)

Response shape:
```json
{
  "count": 1,
  "results": [
    {
      "video_id": "OwAY8fc1uOY",
      "title": "Session on ASEAN Guide on AI Governance and Ethics...",
      "sector": "Infocomm Technology",
      "skill_name": "Artificial Intelligence Ethics and Governance",
      "current_mappings": [
        {
          "competency": "knowledge: AI Ethics and Governance frameworks",
          "item_type": "knowledge",
          "proficiency_level": "2",
          "proficiency_description": "..."
        }
      ],
      "description": "...",
      "channel_title": "..."
    }
  ]
}
```

`current_mappings` is normalized from the root primary mapping plus `additional_mappings` in MongoDB.

---

### GET `/api/public/multi-label/competencies`
Returns available competency items for a selected sector, skill, and proficiency level.

Query params:
- `sector` (required)
- `skill` (required)
- `proficiency_level` (required)

Response shape:
```json
{
  "proficiency_level": "2",
  "proficiency_description": "...",
  "knowledge_items": ["knowledge: AI Ethics and Governance frameworks"],
  "ability_items": ["ability: Apply AI governance controls"]
}
```

Failures:
- `404` when no competency data exists for the selected path

---

### POST `/api/public/multi-label/update-video`
Adds or removes additional competency mappings for an already ingested video.

Request body:
```json
{
  "video_id": "OwAY8fc1uOY",
  "skill_name": "Artificial Intelligence Ethics and Governance",
  "sector": "Infocomm Technology",
  "mappings_to_add": [
    {
      "competency": "knowledge: AI Ethics and Governance frameworks",
      "item_type": "knowledge",
      "proficiency_level": "2",
      "proficiency_description": "..."
    }
  ],
  "mappings_to_remove": []
}
```

Response shape:
```json
{
  "video_id": "OwAY8fc1uOY",
  "message": "Successfully updated video mappings.",
  "total_mappings": 4,
  "added": 1,
  "removed": 0,
  "qdrant_synced": true,
  "qdrant_error": null
}
```

Behavior notes:
- The root MongoDB fields remain the primary mapping used for embedding.
- Saved mappings are stored under `additional_mappings`; the legacy `mappings` field is removed on update.
- The endpoint syncs normalized mapping fields to the existing Qdrant point payload (`competency_mappings`, `mapped_proficiency_levels`, `mapped_competencies`, `mapped_competency_keys`, `mapping_count`).
- This is a payload-only update. It does not re-embed the video and does not change the vector.
- If `qdrant_synced` is `false`, MongoDB was updated but the recommendation index payload should be checked or resynced.

Failures:
- `400` when `video_id`/`skill_name` are missing or there are no mappings to add/remove
- `404` when the ingested video cannot be found
- `500` when MongoDB update fails

---

### GET `/api/public/job-roles`
Returns read-only SkillsFuture job role summaries for lookup.

Query params:
- `sector` (optional, exact match)
- `track` (optional, exact match)
- `q` (optional, max length 120)
- `limit` (optional, default 2500, range 1-5000)

Response item:
```json
{
  "job_role_id": 101,
  "sector": "Accountancy",
  "track": "Assurance",
  "job_role_name": "Audit Manager",
  "skill_requirement_count": 24,
  "critical_work_function_count": 4,
  "has_skill_requirements": true
}
```

---

### GET `/api/public/job-roles/{job_role_id}`
Returns job role description, performance expectation, critical work functions, key tasks, and linked TSC/CCS competency mappings.

Response shape:
```json
{
  "job_role_id": 101,
  "sector": "Accountancy",
  "track": "Assurance",
  "job_role_name": "Audit Manager",
  "role_description": "...",
  "performance_expectation": "...",
  "critical_work_functions": [
    {
      "name": "Perform assurance engagement activities",
      "key_tasks": ["Gather evidence", "Review findings"]
    }
  ],
  "skills": [
    {
      "skill_title": "Audit Frameworks",
      "skill_type": "tsc",
      "tsc_ccs_codes": ["ACC-AUD-4003-1.1"],
      "proficiency_level": "4",
      "proficiency_description": "...",
      "knowledge_items": ["Relevant auditing standards"],
      "ability_items": ["Review and assess audit findings"]
    }
  ]
}
```

Failures:
- `404` when the job role ID does not exist

---

### POST `/api/public/videos/preview`
Previews candidate videos from YouTube API before ingestion.

Request body:
```json
{
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "proficiency_level": "2",
  "competency": "knowledge: Understand data distributions",
  "extra_context": "beginner friendly"
}
```

Response shape:
```json
{
  "query": "...",
  "count": 5,
  "already_ingested_count": 2,
  "quota_exceeded": false,
  "quota_message": null,
  "quota": {
    "daily_limit": 10000,
    "warning_threshold": 8000
  },
  "results": [
    {
      "sector": "Infocomm",
      "skill_name": "Data Analysis",
      "competency": "...",
      "item_type": "knowledge",
      "proficiency_level": "2",
      "proficiency_description": "...",
      "video_id": "abcd1234",
      "published_at": "...",
      "title": "...",
      "description": "...",
      "view_count": 1000,
      "like_count": 120,
      "comment_count": 34,
      "tags": ["python", "pandas"],
      "duration": "PT12M10S",
      "channel_title": "...",
      "thumbnail_url": "...",
      "already_ingested": true
    }
  ]
}
```

Key failures:
- `503` when YouTube API key is not configured
- `429` when quota is exceeded and no results can be returned
- `200` with `quota_exceeded=true` when partial results are still available

---

### POST `/api/public/videos/ingest`
Ingests selected preview videos into MongoDB and Qdrant.

Request body:
```json
{
  "videos": [
    {
      "sector": "Infocomm",
      "skill_name": "Data Analysis",
      "competency": "knowledge: Understand data distributions",
      "item_type": "knowledge",
      "proficiency_level": "2",
      "proficiency_description": "...",
      "videoId": "abcd1234",
      "publishedAt": "...",
      "title": "...",
      "description": "...",
      "viewCount": 1000,
      "likeCount": 120,
      "commentCount": 34,
      "tags": ["python", "pandas"],
      "duration": "PT12M10S",
      "channelTitle": "...",
      "thumbnailUrl": "..."
    }
  ]
}
```

Response:
```json
{
  "message": "Selected videos ingested and indexed successfully.",
  "count": 1,
  "inserted": 1,
  "updated": 0,
  "unchanged": 0,
  "error_count": 0,
  "embedding_status": "completed",
  "embedding_indexed": 1
}
```

Embedding note:
- New ingested videos are embedded with their primary mapping fields.
- Additional mappings added later use the multi-label update endpoint and only sync Qdrant payload.

---

### POST `/api/public/videos/search`
Direct video search with two modes controlled by the `source` field: YouTube Data API search and saved library semantic search. Both modes share the same endpoint and response shape.

The `source` field determines which retrieval path is used:
- `"youtube"` searches YouTube directly via the YouTube Data API and returns fresh candidates for review and ingestion
- `"library"` performs a semantic search over saved and indexed videos in Qdrant without calling the YouTube API or consuming quota

Request body:
```json
{
  "query": "simple tutorial for accountancy basics",
  "max_results": 8,
  "order": "relevance",
  "source": "youtube"
}
```

Request fields:
- `query` (string, required, min length 2, max length 240)
- `max_results` (integer, optional, default 8, range 1-8)
- `order` (string, optional, default `"relevance"`): YouTube sort order, one of `date`, `rating`, `relevance`, `title`, `videoCount`, `viewCount`. Ignored when `source` is `"library"`.
- `source` (string, optional, default `"youtube"`): `"youtube"` or `"library"`

**YouTube mode (`source: "youtube"`):**

Calls the YouTube Data API with the query and returns candidate videos. Results include `already_ingested: true` for videos already in the saved library. Consumes YouTube API quota. Requires `YOUTUBE_API_KEY` to be configured.

**Library mode (`source: "library"`):**

Runs a hybrid semantic search over the saved video Qdrant index using the full recommendation pipeline: BGE embedding → Qdrant cosine ANN → BM25 keyword retrieval → Reciprocal Rank Fusion → cross-encoder reranking. Does not require sector, skill, or competency context. Only videos previously ingested and embedded are searchable. Results with a reranker score below the configured minimum threshold are excluded. Returns `already_ingested: true` for all results.

Response shape (shared by both modes):
```json
{
  "query": "simple tutorial for accountancy basics",
  "count": 5,
  "already_ingested_count": 5,
  "quota_exceeded": false,
  "quota_message": null,
  "quota": null,
  "results": [
    {
      "source": "library",
      "score": 0.847,
      "sector": "Accountancy",
      "skill_name": "Financial Accounting",
      "competency": "...",
      "item_type": "",
      "proficiency_level": "1",
      "proficiency_description": "",
      "video_id": "abcd1234",
      "published_at": "2024-01-01T00:00:00Z",
      "title": "Introduction to Financial Statements",
      "description": "...",
      "view_count": 12000,
      "like_count": 500,
      "comment_count": 30,
      "tags": ["accounting", "finance"],
      "duration": "PT14M20S",
      "channel_title": "Example Channel",
      "thumbnail_url": "https://...",
      "already_ingested": true
    }
  ]
}
```

Response field notes:
- `source` on each result is `"library"` for library mode and `"youtube"` for YouTube mode
- `score` is the cross-encoder reranker score (present on library results only, `null` on YouTube results)
- `quota_message` is populated with `"No relevant videos found. Try a different search term or lower the relevance threshold."` when library mode returns zero results after score filtering
- `quota` is populated with YouTube API credit context for YouTube mode; `null` for library mode

Key failures:
- `400` when `query` is empty or below minimum length
- `503` when embedding or reranker model is unavailable (library mode)
- `503` when YouTube API key is not configured (YouTube mode)
- `429` when YouTube quota is exceeded and no results can be returned (YouTube mode)
- `500` when Qdrant search fails (library mode)

Examples:

```bash
# Library semantic search
curl -X POST http://localhost:8000/api/public/videos/search \
  -H "Content-Type: application/json" \
  -d '{"query":"simple tutorial for accountancy basics","max_results":8,"source":"library"}'

# YouTube direct search
curl -X POST http://localhost:8000/api/public/videos/search \
  -H "Content-Type: application/json" \
  -d '{"query":"beginner accounting standards tutorial","max_results":8,"source":"youtube","order":"relevance"}'
```

---

### POST `/api/public/recommend/videos`
Public recommendation endpoint with explicit filtering and vote-aware ranking.

Request body:
```json
{
  "sector": "Infocomm",
  "skill": "Data Analysis",
  "proficiency_level": "2",
  "competency": "knowledge: Understand data distributions",
  "top_k": 6,
  "strict_skill_match": true
}
```

Response shape:
```json
{
  "query": "Data Analysis. Proficiency level 2. knowledge: Understand data distributions",
  "applied_filters": {
    "sector": "Infocomm",
    "skill": "Data Analysis",
    "proficiency_level": "2"
  },
  "count": 3,
  "results": [
    {
      "score": 0.91,
      "rrf_score": 0.03,
      "reranker_score": 0.91,
      "video_id": "abcd1234",
      "title": "...",
      "description": "...",
      "channel_title": "...",
      "thumbnail_url": "...",
      "published_at": "...",
      "duration": "PT12M10S",
      "view_count": 1000,
      "like_count": 120,
      "comment_count": 34,
      "tags": ["python", "pandas"],
      "sector": "Infocomm",
      "skill_name": "Data Analysis",
      "competency": "...",
      "proficiency_level": "2",
      "user_votes": 5
    }
  ]
}
```

Mapping behavior:
- `sector` is always used as a Qdrant payload filter.
- When `strict_skill_match` is true, `skill` must match the video's root `skill_name`.
- `proficiency_level` matches either the root `proficiency_level` or `mapped_proficiency_levels` from additional mapping payload.
- `competency` is used in metadata boosting. A root competency match receives the primary competency boost; an additional mapped competency match receives the smaller additional mapping boost.
- Additional mappings can bring a video into the candidate pool for a mapped proficiency without re-embedding the video.

---

### POST `/api/public/videos/{video_id}/vote`
Casts vote delta for a video.

Request body:
```json
{
  "vote": 1
}
```

Rules:
- `1` upvote
- `-1` downvote
- `0` no-op (net vote change is zero)

Response:
```json
{
  "video_id": "abcd1234",
  "votes": 7
}
```

---

### GET `/api/public/videos/votes`
Bulk vote totals for comma-separated video IDs.

Example:
```bash
curl "http://localhost:8000/api/public/videos/votes?ids=abcd1234,efgh5678"
```

Response:
```json
{
  "votes": {
    "abcd1234": 7,
    "efgh5678": 0
  }
}
```

---

## 3) Learner Page Route

### GET `/academy`, `/academy/`, `/academy/job-roles`, and `/academy/job-roles/`
Serves learner portal frontend pages.

Notes:
- Static assets are mounted at `/academy/static`
- `/academy` is the existing skill-first learner flow
- `/academy/job-roles` is the separate read-only job role lookup page
- These page routes are not intended as data integration APIs

---

## Integration Notes for External Partner Teams

For cross-platform integration where partner codebase access is not available, use this API consumption order:

1. `GET /api/public/sectors`
2. `GET /api/public/skills`
3. `GET /api/public/skill-map`
4. `POST /api/public/recommend/videos`
5. `POST /api/public/videos/{video_id}/vote` and `GET /api/public/videos/votes` (optional feedback loop)

Optional ingestion and search flow:
1. `POST /api/public/videos/search` with `"source": "youtube"` to preview YouTube candidates
2. `POST /api/public/videos/ingest` to add selected videos to the library
3. `POST /api/public/videos/search` with `"source": "library"` to search the saved library semantically

For full architecture and operational guidance, see `docs/api_layers_integration_guide.md`.

## Quick Troubleshooting

If an endpoint returns an error, the cause usually maps to one of these layers:

- `400` means the request payload or query params are invalid
- `404` usually means the sector, skill, or quiz key was not found
- `429` usually means the LLM provider rate limit was hit during quiz generation, or the YouTube API quota was exceeded
- `500` or `503` usually means a dependency is missing or unhealthy

Likely dependency checks:

- MySQL for sector, skill, and mapping discovery endpoints
- Qdrant for search and recommendation endpoints; also required for library mode of `/api/public/videos/search`
- MongoDB for quiz storage and ingested videos
- YouTube API key for preview, ingest, and YouTube mode of `/api/public/videos/search`
- LLM provider configuration for quiz generation and query enhancement
- Embedding and reranker models for library search and recommendation endpoints
