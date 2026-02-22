# Vector Index Versioning Plan

## Fixed principles

1. Never mix embeddings from different models in the same collection.
2. Never mix embeddings with different vector dimensions in the same collection.
3. Collection names must include model family and dimension.

Current baseline:

- Model: `BAAI/bge-base-en-v1.5`
- Dimension: `768`
- Collection: `sf_skill_level_docs__bge_base__768`

## Naming convention

Use:

`sf_skill_level_docs__{model_slug}__{dim}`

Example:

- `sf_skill_level_docs__bge_base__768`
- `sf_skill_level_docs__bge_large__1024`

## Upgrade procedure (model change)

1. Pick the new model and confirm its embedding dimension.
2. Create a new collection with the new name/dimension.
3. Re-run the full embedding batch job into the new collection.
4. Run smoke tests against the new collection.
5. Update API config (`QDRANT_COLLECTION`) to the new collection.
6. Keep old collection for rollback until acceptance is complete.
7. Delete old collection only after rollback window closes.

## Rollback

If search quality or latency regresses:

1. Set API `QDRANT_COLLECTION` back to previous collection name.
2. Restart API.
3. Investigate model/data/index settings offline.

## Operational notes

- Collection creation should be done with `qdrant/create_collections.py`.
- Embedding writes should be done only by `scripts/embed_sf_skill_levels.py`.
- Use `scripts/smoke_test_qdrant_skills.py` after every full re-index.
