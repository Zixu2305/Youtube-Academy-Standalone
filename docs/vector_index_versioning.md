# Vector Index Versioning Plan

## Fixed principles

1. Never mix embeddings from different models in the same collection.
2. Never mix embeddings with different vector dimensions in the same collection.
3. Collection names must include model family and dimension.

Current baseline:

- Model: `BAAI/bge-base-en-v1.5`
- Dimension: `768`
- SkillsFuture collection: `sf_skill_level_docs__bge_base__768`
  - Config variable: `QDRANT_COLLECTION`
  - Embedding writer: `scripts/embed_sf_skill_levels.py`
- YouTube collection: `youtube_videos__bge_base__768`
  - Config variable: `QDRANT_YT_COLLECTION`
  - Embedding writer: `scripts/embed_yt_videos.py`

## Naming convention

Use collection names that include the document family, model slug, and vector dimension:

`sf_skill_level_docs__{model_slug}__{dim}`
`youtube_videos__{model_slug}__{dim}`

Examples:

- `sf_skill_level_docs__bge_base__768`
- `sf_skill_level_docs__bge_large__1024`
- `youtube_videos__bge_base__768`
- `youtube_videos__bge_large__1024`

## Upgrade procedure (model change)

1. Pick the new model and confirm its embedding dimension.
2. Decide whether the change affects SkillsFuture embeddings, YouTube embeddings, or both.
3. Create new collection names with the new model slug and dimension.
4. Run `python qdrant/create_collections.py` with the new `QDRANT_COLLECTION` and/or `QDRANT_YT_COLLECTION` values.
5. Re-run the affected embedding jobs:
   - SkillsFuture: `python scripts/embed_sf_skill_levels.py`
   - YouTube: `python scripts/embed_yt_videos.py`
6. Run smoke tests and representative API checks against the new collections.
7. Update API config to point to the new collection names.
8. Keep old collections for rollback until acceptance is complete.
9. Delete old collections only after the rollback window closes.

## Rollback

If search quality or latency regresses:

1. Set `QDRANT_COLLECTION` and/or `QDRANT_YT_COLLECTION` back to the previous collection names.
2. Restart API.
3. Investigate model/data/index settings offline.

## Operational notes

- Collection creation should be done with `qdrant/create_collections.py`; it creates or validates both the SkillsFuture and YouTube collections.
- Embedding writes should be done only by the owned embedding jobs:
  - `scripts/embed_sf_skill_levels.py` for SkillsFuture skill-level documents.
  - `scripts/embed_yt_videos.py` for approved YouTube video mappings.
- Use `scripts/smoke_test_qdrant_skills.py` after every full re-index.
- Validate YouTube search/recommend behavior after every YouTube collection rebuild, for example with `/api/search/videos`, `/api/recommend/videos`, `/api/public/recommend/videos`, and `/api/public/videos/search` in library mode.
- Additional video mappings assigned through the multi-label flow are payload-only updates. They do not require re-embedding unless the underlying embedded text, model, or vector dimension changes.
