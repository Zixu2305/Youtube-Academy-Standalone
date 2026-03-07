# Recommendation Engine: Hybrid Retrieval + Reranking Pipeline

## Overview

The `/api/recommend/videos` endpoint was refactored from a simple two-hop cosine similarity lookup into a multi-stage **Hybrid Retrieval + Reranking** pipeline. This document explains every stage and the rationale behind each design decision.

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
    |              Proficiency Boost (1.15x if match)
    |                               |
    |                               v
    |                   Top 50 Candidates
    |                               |
    |                               v
    |            [BGE-Reranker-Base Cross-Encoder]
    |             (SF_metadata, YT_metadata) pairs
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

**YT Video Weighting** (`scripts/embed_yt_videos.py`):
- `title`: repeated **3x**
- `skill_name`: repeated **2x**
- `tags`: repeated **2x**
- All other fields (`description`, `channel`, `sector`, `competency`, `proficiency_level`, `proficiency_description`): **1x**

**Why text repetition works:** Transformer-based encoders (like BGE) use attention over all input tokens. Repeating a field increases the number of tokens associated with that concept, giving it proportionally more influence in the pooled output vector. This is a well-known technique in IR called "field boosting via text expansion."

**Important:** After updating the weighting, you must re-run the embedding scripts to regenerate vectors:
```bash
python scripts/embed_sf_skill_levels.py
python scripts/embed_yt_videos.py
```

### 2. Hybrid Retrieval (Semantic + BM25)

**Problem:** Pure semantic search can miss results where the query and document share exact keyword overlap but differ in paraphrase space. For example, "Data Analytics" and a video titled "Data Analytics Tutorial" are an exact lexical match that cosine similarity might rank below a semantically similar but differently-worded result.

**Solution:** Run two retrieval paths in parallel and fuse them.

**Path A — Semantic Search:**
Uses the matched SF skill's stored embedding vector to search the YT collection via Qdrant cosine similarity. Returns top 50 candidates.

**Path B — BM25 Lexical Search:**
Tokenizes the matched `skill_title` and queries a BM25Okapi index built over all YT video `title + tags` text. Returns top 50 candidates by BM25 score.

The BM25 index is **lazy-loaded** on the first recommendation request and cached in memory via `@lru_cache`. It scrolls all YT payloads from Qdrant (not MongoDB) to stay consistent with the vector store.

### 3. Reciprocal Rank Fusion (RRF)

**Problem:** Semantic and BM25 scores are on different scales and not directly comparable.

**Solution:** RRF is a rank-based fusion method that is score-agnostic. It only uses the position (rank) of each document in each list.

**Formula:** For each document appearing at rank `r` in a ranked list:
```
RRF_score += 1 / (k + r + 1)
```

Where `k = 60` (standard value from the original RRF paper by Cormack et al., 2009). Documents appearing in both lists get contributions from both, naturally boosting results that are strong in both semantic and lexical relevance.

### 4. Sector & Proficiency Boosts

**Problem:** The retrieval stage is purely about text similarity. It doesn't account for categorical alignment between the matched skill and candidate videos.

**Solution:** After RRF fusion, apply multiplicative boosts:

- **Sector alignment (1.2x):** If the matched SF skill's `category` exactly matches a YT video's `sector` field. This ensures videos from the same domain are preferred.
- **Proficiency alignment (1.15x):** If the matched SF skill's `proficiency_level` exactly matches a YT video's `proficiency_level`. This ensures videos at the appropriate difficulty level are ranked higher.

These are conservative multipliers that nudge relevance without overwhelming the core retrieval signal.

### 5. Cross-Encoder Reranking

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

### 6. Updated Response Schema

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
| `PROFICIENCY_BOOST` | 1.15 | Multiplier when proficiency levels match |

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
