# API Layers Integration Guide

This guide documents the full system boundaries and API-layer integration strategy for teams that need to consume the recommendation engine as an external service.

Intended audience:
- external platform teams integrating by contract only
- internal developers who need the public/internal boundary clarified

Not intended as the main developer onboarding guide for the repo. For local setup and workflow entry points, start with `README.md`.

Primary scenario:
- The recommendation system is hosted and operated by this project team.
- Partner platform team may not have codebase access.
- Integration is API-first and contract-driven.

## 1. Integration Objective

Expose recommendation capabilities as stable HTTP APIs so another platform can:
- discover sectors and skills
- obtain mapped proficiency and competency context
- retrieve personalized recommendations
- submit user feedback (votes)
- optionally trigger content curation workflows

## 2. System Architecture (Layered)

Primary architecture diagram:

![System architecture](sys_architecture.png)

## 3. API Layer Responsibilities

Quick boundary summary:
- `/api/public/*` is the preferred integration surface for partners and frontend clients
- `/api/search/*`, `/api/recommend/*`, and `/api/quiz/*` are engine-level APIs for internal use and diagnostics
- `/academy` is the learner demo page, not a platform integration surface

### 3.1 Public Layer (`/api/public/*`)
Purpose:
- Partner-safe contract for UI and platform integration
- Exposes domain-level operations (sector, skill, map, recommend, vote)

Characteristics:
- Input payloads are business-facing (sector/skill/proficiency)
- Returns curated response payloads ready for frontend consumption
- Includes caching and ranking logic behind the API boundary

Recommended for:
- external product teams
- frontend clients
- partner systems that should not depend on internals

### 3.2 Core Layer (`/api/search/*`, `/api/recommend/*`, `/api/quiz/*`)
Purpose:
- engine-level capabilities and diagnostics
- direct semantic retrieval and quiz generation controls

Characteristics:
- closer to internal data model and retrieval mechanics
- useful for experimentation and quality evaluation

Recommended for:
- internal team and advanced integration users
- model/retrieval QA pipelines

### 3.3 Page Layer (`/academy`)
Purpose:
- serves local learner demo frontend

Recommended for:
- demonstration only, not platform-to-platform integration

## 4. Data and Dependency Boundaries

### 4.1 Storage Roles

- MySQL:
  - SkillsFuture relational schema and mapping tables
  - source of truth for sectors, skills, proficiency, competency text

- Qdrant:
  - vector indexes for skill and YouTube content retrieval
  - semantic and hybrid candidate retrieval

- MongoDB:
  - ingested video documents
  - quiz storage
  - video vote counters

### 4.2 External Dependencies

- YouTube Data API:
  - used by preview/ingest routes
  - constrained by daily quota

- LLM Provider (Groq/OpenAI):
  - used in quiz generation and some enrichment paths

## 5. End-to-End Request Paths

### 5.1 Recommendation path (partner-facing)
1. Partner calls `POST /api/public/recommend/videos`
2. API builds query context from skill/proficiency/competency
3. Retrieval stage executes semantic + BM25 + RRF + metadata boosts
4. Cross-encoder reranks candidate set
5. Vote-aware adjustment is applied
6. Top K results returned with metadata

### 5.2 Discovery path
1. `GET /api/public/sectors`
2. `GET /api/public/skills?sector=...`
3. `GET /api/public/skill-map?sector=...&skill=...`

### 5.3 Optional content curation path
1. `POST /api/public/videos/preview`
2. user selects approved videos
3. `POST /api/public/videos/ingest`
4. videos are upserted and indexed for retrieval

## 6. Integration Without Partner Codebase Access

Use a contract-first collaboration model:

1. API contract package:
- share OpenAPI from `/docs`
- share `docs/api_reference.md`
- version endpoint and schema changes

2. Shared test collection:
- provide curl/Postman collection for public endpoints
- include happy-path and error-path examples

3. Environment handoff:
- provide base URL, auth method, and SLA expectations
- provide staging and production endpoint matrix

4. Change management:
- add change log for response schema additions/removals
- agree deprecation window for breaking changes

## 7. Recommended External Integration Contract

Minimal required endpoints for partner launch:
- `GET /api/public/sectors`
- `GET /api/public/skills`
- `GET /api/public/skill-map`
- `POST /api/public/recommend/videos`

Optional engagement endpoints:
- `POST /api/public/videos/{video_id}/vote`
- `GET /api/public/videos/votes`

Optional curation endpoints:
- `POST /api/public/videos/preview`
- `POST /api/public/videos/ingest`

## 8. Security and Gateway Recommendations

Current project state:
- no explicit API auth/authorization middleware is configured in `api/main.py`
- intended for standalone demo/local deployment

For cross-team production integration, place APIs behind a gateway with:
- OAuth2/JWT or signed service token auth
- rate limits by endpoint and partner client ID
- request/response logging with PII redaction
- CORS policy restricted to approved origins
- WAF policy for abuse filtering

## 9. Operational Runbook

### 9.1 Required services
- MySQL
- MongoDB
- Qdrant
- FastAPI app
- optional: ingestion Flask app for curation workflow

### 9.2 Key environment variables
- database: `DB_*`, `MYSQL_*`, `MONGO_*`
- vector store: `QDRANT_*`, `EMBEDDING_*`
- YouTube: `YOUTUBE_API_KEY`, quota settings
- LLM: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`

### 9.3 Health validation checklist
1. FastAPI starts and `/docs` loads
2. `GET /api/public/sectors` returns non-empty list
3. `POST /api/public/recommend/videos` returns results for known skill
4. vote endpoints can increment and fetch counters
5. optional: preview/ingest flow succeeds with valid YouTube API key

### 9.4 What to check first when debugging

- If public discovery calls fail, confirm MySQL and the SkillsFuture seed are loaded
- If recommendation calls fail, confirm Qdrant is running and collections were created
- If preview or ingest fails, confirm the YouTube API key and quota state
- If quiz generation fails, confirm the LLM configuration and MongoDB connectivity

## 10. API Versioning Guidance

Recommended next step for production collaboration:
- introduce explicit version prefix, for example `/api/v1/public/...`
- freeze response contracts per version
- publish migration notes before contract changes

## 11. Collaboration Handoff Checklist

Before partner implementation starts:
1. Share API docs and OpenAPI export
2. Confirm endpoint ownership and support contacts
3. Provide test dataset and deterministic test cases
4. Align on error handling and retry semantics
5. Finalize release cadence and schema change process

## 12. Related Project Docs

- `docs/api_reference.md`
- `docs/rec.md`
- `docs/youtube_embedding_pipeline.md`
- `docs/vector_index_versioning.md`

This guide plus `docs/api_reference.md` should be treated as the primary integration handoff package.
