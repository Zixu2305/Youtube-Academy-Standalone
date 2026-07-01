# Feature and Technical Change Summary

This document gives third-party teams one consolidated view of the feature and technical changes made from the OpenAI provider switch through the current `main` head.

## Scope

- Inclusive commit range: `2c9e76b` through `d4564f6`
- Date range: 16 June 2026 to 29 June 2026
- Commits reviewed: 4
- Combined change: 32 files, 7,287 insertions, 817 deletions

The main outcome is broader than an LLM-provider change. The application now has a review-and-approval workflow for unmapped YouTube videos, multi-label competency curation, richer Qdrant mapping metadata, and updated recommendation behavior.

## Feature Overview

| Feature area | What changed | Third-party impact |
|---|---|---|
| LLM integration | Unconfigured runtime default changed to OpenAI with `gpt-4.1-nano` | Configure the provider explicitly; quiz, query-enhancement, and mapping-suggestion features share this client |
| Admin tools | Docker service renamed to `admin_tools`; mapping-review and multi-label pages added | Deployment commands and admin navigation changed |
| Direct video search | Saved-library and YouTube searches are separated and de-duplicated | New YouTube submissions may enter review instead of becoming immediately searchable |
| Mapping review | Pending, approve, reject, unpublish, reopen, and retry flows added | Integrators must distinguish “submitted” from “approved and indexed” |
| AI mapping | Candidate scoring, fallback confidence, failure messages, and retry behavior improved | Weak matches now require manual admin mapping |
| Multi-label curation | Videos can receive additional proficiency and competency mappings | Additional mappings update MongoDB and Qdrant payload without re-embedding |
| Recommendations | Additional mappings affect candidate filtering and bounded metadata boosts | A video can match more than its primary proficiency/competency |
| Vector identity | YouTube point IDs now include proficiency and competency | Existing Qdrant collections require an explicit migration or clean rebuild |

## 1. Shared LLM Provider Support

The shared client in `pipelines/llm_client.py` continues to support both Groq and OpenAI through OpenAI-compatible chat-completion APIs.

The code-level fallback changed to:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-nano
```

Configuration precedence is:

1. `LLM_PROVIDER` selects `groq` or `openai`.
2. `LLM_MODEL` and `LLM_API_KEY` act as shared overrides.
3. If shared values are empty, the client uses `GROQ_MODEL`/`GROQ_API_KEY` or `OPENAI_MODEL`/`OPENAI_API_KEY`.
4. If no provider is configured, the runtime falls back to OpenAI.

The client is used by:

- quiz generation
- YouTube query enhancement
- AI-assisted SkillsFuture mapping suggestions

The runtime does not restrict OpenAI to a particular model family. `LLM_MODEL` is passed to the selected provider, so the configured name must be supported by that provider and API key.

Important configuration note: `.env.example` still explicitly selects Groq and `llama-3.1-8b-instant`. Copying it unchanged overrides the new OpenAI fallback. Third-party deployments that intend to use OpenAI must set the three shared variables explicitly.

## 2. Admin Tools Surface

The Docker Compose service previously named `seed_youtube` is now `admin_tools`.

```bash
docker compose up -d --build --remove-orphans admin_tools
```

The Flask admin application now includes:

- `/` — YouTube ingestion console
- `/mapping_review` — pending and completed mapping requests
- `/multi_label` — additional competency/proficiency mappings
- `/mongo_browser` — MongoDB document browser with richer mapping display
- `/quiz_gen` — quiz generation
- `/delete` — soft-delete tools

The admin image and bind mounts now include the shared LLM client. The service also has a `host.docker.internal` host-gateway entry so it can call a FastAPI process running on the Docker host.

## 3. Direct Video Search and Submission

`POST /api/public/videos/search` supports two sources:

- `source: "library"` searches videos already indexed in Qdrant.
- `source: "youtube"` queries YouTube for new candidates.

Saved-library search uses:

1. BGE query embeddings and Qdrant semantic retrieval
2. BM25 keyword retrieval over stored title/tag payloads
3. Reciprocal Rank Fusion
4. a title-match boost
5. de-duplication by `video_id`

YouTube search excludes videos already found in MongoDB or the saved indexed library. The learner UI now requests up to 24 YouTube results and leaves all results unselected by default; the user explicitly chooses which videos to submit.

There are now two ingestion outcomes:

- A skill-scoped video with sector, skill, and competency mapping is ingested and indexed immediately.
- An unmapped direct-search video is saved as a pending review request and is not indexed until admin approval.

`POST /api/public/videos/ingest` reports the split through fields including:

- `pending_review_count`
- `approved_ingested_count`
- `embedding_status`
- `embedding_requested`
- `embedding_indexed`

Third-party clients should not treat a successful submission as proof that a video is already available in library search or recommendations. Check the pending and approved counts.

## 4. Video Mapping Review Lifecycle

Unmapped videos are stored in the MongoDB `videos` collection as review requests. The request returns promptly, while mapping suggestions are prepared in a background task.

| State/action | Behavior | Search/index impact |
|---|---|---|
| Pending | Stores video metadata and suggestion status | No Qdrant point is created |
| Approve | Validates seeded mappings and creates live MongoDB mapping documents | Embeds and upserts approved mapping points |
| Reject | Preserves the request as an audit/blocklist record | No embedding is created |
| Reopen | Returns a rejected request to pending | Still not indexed until approval |
| Unpublish | Removes approved mapping documents and all Qdrant points for the video | Video is removed from indexed results |
| Retry suggestion | Calls FastAPI to regenerate an AI/fallback suggestion | Does not approve or index the video |

Suggestion processing uses these statuses:

- `queued`
- `running`
- `ready`
- `failed`
- `manual`
- `cancelled`

FastAPI admin endpoints added in this range:

- `GET /api/admin/video-mapping-requests`
- `GET /api/admin/video-mapping-requests/{video_id}`
- `PUT /api/admin/video-mapping-requests/{video_id}`
- `POST /api/admin/video-mapping-requests/{video_id}/approve`
- `POST /api/admin/video-mapping-requests/{video_id}/reject`
- `POST /api/admin/video-mapping-requests/{video_id}/unpublish`
- `POST /api/admin/video-mapping-requests/{video_id}/reopen`

The Flask `admin_tools` service exposes equivalent review operations for its own UI and additionally provides:

- `POST /api/admin/video-mapping-requests/{video_id}/retry-suggestion`

Admin routes should be protected by the integrating platform or gateway before external exposure.

## 5. AI-Assisted Mapping Suggestions

`POST /api/public/videos/suggest-mapping` proposes a mapping only from seeded SkillsFuture sector, skill, proficiency, and competency data. It does not allow the LLM to invent mapping values.

Candidate selection now:

- weights the video title four times
- weights the channel and search query twice
- uses description and tags as supporting evidence
- considers up to 12 candidates
- initially limits selection to two candidates per sector for broader coverage
- sends at most two proficiency levels and four competencies per level for each candidate

The response identifies the suggestion source as `ai` or `fallback`.

If the LLM is unavailable or returns an invalid candidate, the system can use a deterministic seeded-data fallback. The fallback must now meet a confidence threshold of `0.70`. Weaker evidence returns `No reliable suggestion`, and the admin must map the video manually.

The mapping-review UI presents concise failure text and allows retry only while the request is pending.

## 6. Multi-Label Video Curation

Each indexed video still has one primary mapping stored on the root MongoDB document:

- `sector`
- `skill_name`
- `proficiency_level`
- `proficiency_description`
- `item_type`
- `competency`

Admins can now assign additional mappings through `/multi_label`. The supporting FastAPI endpoints are:

- `POST /api/public/multi-label/search-videos`
- `GET /api/public/multi-label/competencies`
- `POST /api/public/multi-label/update-video`

The update flow:

1. finds a live mapped MongoDB video document
2. adds or removes normalized proficiency/competency pairs
3. stores the mapping list under `additional_mappings`
4. synchronizes mapping metadata to the existing Qdrant point
5. clears recommendation retrieval and reranking caches after a successful sync

The displayed proficiency choices now include levels `1` through `6`, plus `Basic`, `Intermediate`, and `Advanced`. The competency lookup still validates the selected combination against seeded MySQL data.

Additional mappings are intentionally payload-only. They do not change the embedding vector, BM25 corpus text, or cross-encoder document text.

## 7. Recommendation and Qdrant Changes

Qdrant payloads now include:

- `competency_mappings`
- `mapped_proficiency_levels`
- `mapped_competencies`
- `mapped_competency_keys`
- `mapping_count`

The recommendation pipeline can include a video when the requested proficiency matches either its primary proficiency or an additional mapped proficiency. Competency matches affect ranking through bounded metadata boosts.

Current mapping-related boost values are:

| Match | Multiplier |
|---|---:|
| Primary proficiency | `1.15` |
| Additional proficiency | `1.08` |
| Primary competency | `1.12` |
| Additional competency | `1.08` |
| Combined mapping boost cap | `1.25` |

Sector alignment remains a separate `1.20` multiplier. The mapping boost cap prevents curated metadata from overwhelming semantic and reranker relevance.

The deterministic YouTube point ID changed from:

```text
UUID5(videoId, skill_name)
```

to:

```text
UUID5(videoId, skill_name, proficiency_level, competency)
```

This allows one video to have separate live points for multiple approved primary mappings.

## 8. Data Contract Changes

No MySQL schema file changed. MySQL remains the validation source for SkillsFuture sectors, skills, proficiency levels, competencies, and job-role data.

MongoDB review documents now use fields such as:

```text
review_status
suggested_mappings
mapping_review.is_request
mapping_review.status
mapping_review.suggestion_status
mapping_review.suggestion_error
mapping_review.approved_mappings
mapping_review.reviewer
mapping_review.created_at / updated_at / reviewed_at
```

Live video mapping documents may also contain `additional_mappings`.

MongoDB clients in the ingestion, quiz, and vector paths now accept `MONGO_AUTH_MECHANISM`, defaulting to `SCRAM-SHA-256`.

## 9. Integration and Upgrade Actions

Third-party teams adopting this range should complete the following actions.

### Configure the intended LLM explicitly

For OpenAI:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-nano
LLM_API_KEY=<openai-key>
```

Do not rely on the current `.env.example` values because they still select Groq.

### Configure admin-to-FastAPI connectivity

```env
ACADEMY_API_BASE_URL=http://host.docker.internal:8000
```

The Flask retry handler currently falls back to port `9000` if this value is absent, while the documented FastAPI runtime uses port `8000`. Set the value explicitly.

### Rebuild existing YouTube vector collections

The point-ID formula changed. Running `scripts/embed_yt_videos.py` in place creates new IDs but does not remove points written with the old formula. That can leave duplicate legacy results and prevent payload-only mapping updates from reaching old points.

For collections populated before `b9a89bc`, perform a clean YouTube collection rebuild or explicitly delete legacy points before re-indexing. Validate result counts and duplicate `video_id` values afterward.

### Update client expectations

- Treat direct YouTube ingestion as a submission for review unless the video already has a complete mapping.
- Use `pending_review_count` and `approved_ingested_count` to present the correct status.
- Expect `suggestion_status: "failed"` and manual mapping when there is insufficient evidence.
- Do not assume additional mapping updates require re-embedding.
- Protect `/api/admin/*` through the external platform's authentication and authorization layer.

### Add integration coverage

No automated test file changed in this commit range. Third-party validation should cover:

- configured provider/model selection
- direct library search versus YouTube search
- pending, approve, reject, reopen, retry, and unpublish transitions
- Qdrant point migration and duplicate prevention
- multi-label MongoDB and Qdrant payload synchronization
- recommendation results using primary and additional mappings


## Commit Traceability

| Commit | Date | Contribution to this feature set |
|---|---|---|
| `2c9e76b` | 16 June 2026 | OpenAI fallback/model change, `admin_tools` rename, shared LLM client packaging, EC2 snapshot documentation |
| `b9a89bc` | 23 June 2026 | Mapping review, multi-label curation, Qdrant payload/identity changes, recommendation and UI updates |
| `ff98a1c` | 23 June 2026 | API, integration, recommendation, embedding, and vector-versioning documentation |
| `d4564f6` | 29 June 2026 | Mapping candidate quality, reliable fallback threshold, retry action, opt-in YouTube selection, connectivity patch |

## Related Documentation

- [README](../README.md)
- [API Layers Integration Guide](./api_layers_integration_guide.md)
- [API Reference](./api_reference.md)
- [YouTube Embedding Pipeline](./youtube_embedding_pipeline.md)
- [Recommendation Engine](./rec.md)
- [Vector Index Versioning](./vector_index_versioning.md)
- [EC2 Deployment Supplement](./ec2_deployment_supplement.md)
