# Recommendation Engine: Hybrid Retrieval + Reranking Pipeline

## Overview

The `/api/recommend/videos` endpoint was refactored from a simple two-hop cosine similarity lookup into a multi-stage **Hybrid Retrieval + Reranking** pipeline. The public learner endpoint, `/api/public/recommend/videos`, uses the same retrieval primitives with explicit sector/skill/proficiency filters. This document explains every stage and the rationale behind each design decision.

## Architecture

```
User Query
    |
    v
[BGE-Base Bi-Encoder (768d)] --encode--> query_vector
    |
    v
[Qdrant SF Collection] --cosine--> Top-1 Skill Match
    |                                 |
    |                    +------------+------------+
    |                    v                         v
    |            Path A: Semantic           Path B: BM25
    |            (skill vector -->          (skill_title -->
    |             YT cosine, top 50)        YT title+tags, top 50)
    |                    |                         |
    |                    +----------+--------------+
    |                               v
    |                      RRF Fusion (k=60)
    |                               |
    |                               v
    |                Sector Boost (1.2x if match)
    |       Mapping Boosts (primary/additional, capped)
    |                               |
    |                               v
    |                   Top 50 Candidates
    |                               |
    |                               v
    |            [BGE-Reranker-Base Cross-Encoder]
    |             (SF_metadata, YT_metadata) pairs
    |                               |
    |                               v
    |              Vote-Based Re-ranking
    |           (swap adjacent if vote diff >= 3)
    |                               |
    |                               v
    |                   Final Top-N Videos
```

## Changes Made

### 1. Field-Weighted Embeddings

**Problem:** The original embedding text treated all metadata fields equally. A video's `title` and a video's `channel` name had the same influence on the final vector, even though `title` is far more semantically important for retrieval.

**Solution:** Repeat high-priority fields in the embedding text to increase their weight in the resulting vector.

**SF Skill Weighting** (`scripts/embed_sf_skill_levels.py`):
- `skill_title`: repeated **3x**
- `category`: repeated **2x**
- All other fields (`skill_type`, `tsc_ccs_code`, `proficiency_level`, `proficiency_description`, knowledge/ability items): **1x**

**YT Video Weighting** (`pipelines/youtube/youtube_vector_index.py`, used by `scripts/embed_yt_videos.py`):
- `title`: repeated **3x**
- `skill_name`: repeated **2x**
- `tags`: repeated **2x**
- All other fields (`description`, `channel`, `sector`, `competency`, `proficiency_level`, `proficiency_description`): **1x**

The video embedding text is built from the video's primary mapping fields. Additional mappings assigned later are not appended to the embedding text.

**Why text repetition works:** Transformer-based encoders (like BGE) use attention over all input tokens. Repeating a field increases the number of tokens associated with that concept, giving it proportionally more influence in the pooled output vector. This is a well-known technique in IR called "field boosting via text expansion."

**Important:** Re-run the embedding scripts only when the embedded text, model, or vector dimension changes:
```bash
python scripts/embed_sf_skill_levels.py
python scripts/embed_yt_videos.py
```

Assigning or removing additional mappings does not require re-embedding. Those changes are synchronized into the existing Qdrant point payload.

### 2. Primary vs Additional Video Mappings

Videos have one primary mapping, stored on the root MongoDB video document (`sector`, `skill_name`, `competency`, `item_type`, `proficiency_level`, `proficiency_description`). That primary mapping is the mapping used when the video vector is created.

Manual multi-labelling stores additional mappings in MongoDB under `additional_mappings` and synchronizes them to the existing Qdrant payload. The payload fields used by recommendation are:

- `competency_mappings`: normalized list containing the primary mapping plus additional mappings.
- `mapped_proficiency_levels`: all mapped proficiency levels for the video.
- `mapped_competencies`: all mapped competency strings for the video.
- `mapped_competency_keys`: `{proficiency_level}||{competency}` keys for exact mapping checks.
- `mapping_count`: total normalized mapping count.

These payload fields let a video enter the recommendation candidate pool when it matches the requested proficiency through an additional mapping, even though its vector still represents the original primary mapping. This is intentionally cheap: Qdrant updates only the payload attached to the existing point, so no encoder forward pass or vector upsert is needed.

Additional mappings currently affect filtering and metadata boosts. They do not change the BM25 corpus text or the cross-encoder document text, both of which still use the primary video metadata.

### 3. Hybrid Retrieval (Semantic + BM25)

**Problem:** Pure semantic search can miss results where the query and document share exact keyword overlap but differ in paraphrase space. For example, "Data Analytics" and a video titled "Data Analytics Tutorial" are an exact lexical match that cosine similarity might rank below a semantically similar but differently-worded result.

**Solution:** Run two retrieval paths in parallel and fuse them.

**Path A — Semantic Search:**
Uses the matched SF skill's stored embedding vector to search the YT collection via Qdrant cosine similarity. Returns top 50 candidates.

**Path B — BM25 Lexical Search:**
Tokenizes the matched `skill_title` and queries a BM25Okapi index built over all YT video `title + tags` text. Returns top 50 candidates by BM25 score.

The BM25 index is **lazy-loaded** on the first recommendation request and cached in memory via `@lru_cache`. It scrolls all YT payloads from Qdrant (not MongoDB) to stay consistent with the vector store.

### 4. Reciprocal Rank Fusion (RRF)

**Problem:** Semantic and BM25 scores are on different scales and not directly comparable.

**Solution:** RRF is a rank-based fusion method that is score-agnostic. It only uses the position (rank) of each document in each list.

**Formula:** For each document appearing at rank `r` in a ranked list:
```
RRF_score += 1 / (k + r + 1)
```

Where `k = 60` (standard value from the original RRF paper by Cormack et al., 2009). Documents appearing in both lists get contributions from both, naturally boosting results that are strong in both semantic and lexical relevance.

### 5. Sector & Mapping Boosts

**Problem:** The retrieval stage is mostly about text similarity. It needs to account for categorical alignment and for human-curated additional mappings without letting metadata overpower semantic relevance.

**Solution:** After RRF fusion, apply multiplicative boosts:

- **Sector alignment (1.2x):** If the matched SF skill's `category` exactly matches a YT video's `sector` field. This ensures videos from the same domain are preferred.
- **Primary proficiency alignment (1.15x):** If the requested proficiency exactly matches the video's root `proficiency_level`.
- **Additional proficiency alignment (1.08x):** If the requested proficiency is present in `mapped_proficiency_levels` but is not the root proficiency.
- **Primary competency alignment (1.12x):** If the requested competency exactly matches the video's root `competency`.
- **Additional competency alignment (1.08x):** If the requested competency is present in `mapped_competencies` but is not the root competency.
- **Mapping boost cap (1.25x):** Proficiency and competency mapping boosts are capped before being applied to the RRF score.

These are conservative multipliers that recognize human curation while keeping semantic retrieval and reranking as the main ranking signals.

### 6. Cross-Encoder Reranking

**Problem:** Bi-encoder (BGE) representations encode query and document independently. They cannot capture fine-grained token-level interactions between the skill description and video metadata.

**Solution:** Take the top 50 candidates after boosting and pass each as a `(query, document)` pair through the `BAAI/bge-reranker-base` cross-encoder.

**Query side (SF metadata):**
```
"Skill: {title}. Category: {category}. Proficiency level: {level}."
```

**Document side (YT metadata):**
```
"Title: {title}. Description: {desc}. Tags: {tags}. Skill: {skill}. Competency: {competency}."
```

The cross-encoder processes both texts jointly through all transformer layers, producing a single relevance score. This is significantly more accurate than bi-encoder similarity but too slow to run on all candidates (hence the two-stage retrieve-then-rerank architecture).

### 7. Vote-Based Re-ranking (User Input)

**Problem:** The pipeline ranks videos purely by algorithmic relevance. Users who watch the recommended videos have no way to signal which ones are actually useful.

**Solution:** After the cross-encoder produces the final ranking, apply a vote-based re-ranking step using global user votes stored in MongoDB (`video_votes` collection).

**Algorithm:** Bubble-sort style adjacent swaps — a lower-ranked video overtakes the one directly above it if its vote total exceeds the upper video's by >= 3. This is applied iteratively until no more swaps occur. The threshold of 3 prevents a single vote from disrupting the algorithmic ranking while allowing clear community consensus to surface better content.

**Vote storage:** MongoDB collection `video_votes` with schema `{ video_id: str, votes: int }`. Votes are cast via `POST /api/public/videos/{video_id}/vote` with atomic `$inc` operations.

**Response field:** `user_votes: int` is included in each video result so the frontend can display the current vote total and provide optimistic UI updates.

### 8. Updated Response Schema

New fields added to the API response:

- `retrieval_method: str` — Always `"hybrid_rrf_reranked"` to identify the pipeline version.
- `RecommendedVideo.rrf_score: float` — The hybrid retrieval score after RRF fusion + metadata boosts. Determines which candidates advance to the reranking stage.
- `RecommendedVideo.reranker_score: float` — The cross-encoder relevance score. **This is the final ranking signal** that determines the order of returned videos.

## Configuration

All constants are defined at the top of `api/routes/recommend.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `SEMANTIC_CANDIDATES` | 50 | Number of candidates from Qdrant cosine search |
| `BM25_CANDIDATES` | 50 | Number of candidates from BM25 lexical search |
| `RRF_K` | 60 | RRF smoothing parameter (standard value) |
| `RERANK_TOP_N` | 10 | Max candidates passed to cross-encoder |
| `SECTOR_BOOST` | 1.2 | Multiplier when SF.category == YT.sector |
| `PROFICIENCY_BOOST` | 1.15 | Multiplier when requested level matches the primary video proficiency |
| `ADDITIONAL_PROFICIENCY_BOOST` | 1.08 | Multiplier when requested level matches an additional mapped proficiency |
| `PRIMARY_COMPETENCY_BOOST` | 1.12 | Multiplier when requested competency matches the primary video competency |
| `ADDITIONAL_COMPETENCY_BOOST` | 1.08 | Multiplier when requested competency matches an additional mapped competency |
| `MAPPING_BOOST_CAP` | 1.25 | Maximum combined proficiency/competency mapping multiplier |

## Dependencies Added

```
rank-bm25>=0.2.2    # BM25Okapi implementation
numpy                # Array operations for score sorting
scikit-learn         # General ML utilities
```

## Models Used

| Component | Model | Purpose |
|-----------|-------|---------|
| Bi-Encoder | `BAAI/bge-base-en-v1.5` (768d) | Query/document embedding for semantic search |
| Cross-Encoder | `BAAI/bge-reranker-base` | Pairwise relevance scoring for reranking |
| Lexical | BM25Okapi (rank-bm25) | Keyword-based retrieval over title + tags |
