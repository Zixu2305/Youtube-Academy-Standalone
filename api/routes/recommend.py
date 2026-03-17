from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_VECTOR_DIM = 768
DEFAULT_SF_COLLECTION = "sf_skill_level_docs__bge_base__768"
DEFAULT_YT_COLLECTION = "youtube_videos__bge_base__768"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"

# Hybrid retrieval constants
SEMANTIC_CANDIDATES = 50
BM25_CANDIDATES = 50
RRF_K = 60
RERANK_TOP_N = 10
SECTOR_BOOST = 1.2
PROFICIENCY_BOOST = 1.15

router = APIRouter()


# ---------------------------------------------------------------------------
# Singleton loaders
# ---------------------------------------------------------------------------

def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def resolve_cached_hf_model_path(model_name: str) -> Path | None:
    model_path = Path(model_name).expanduser()
    if model_path.exists():
        return model_path

    cache_roots: list[Path] = []
    for raw_path in (
        env("HUGGINGFACE_HUB_CACHE"),
        env("TRANSFORMERS_CACHE"),
        env("SENTENCE_TRANSFORMERS_HOME"),
    ):
        if raw_path:
            cache_roots.append(Path(raw_path).expanduser())

    hf_home = env("HF_HOME")
    if hf_home:
        cache_roots.append(Path(hf_home).expanduser() / "hub")

    cache_roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    repo_dir_name = f"models--{model_name.replace('/', '--')}"
    seen_roots: set[Path] = set()
    for cache_root in cache_roots:
        cache_root = cache_root.expanduser()
        if cache_root in seen_roots:
            continue
        seen_roots.add(cache_root)

        repo_dir = cache_root / repo_dir_name
        if not repo_dir.exists():
            continue

        ref_file = repo_dir / "refs" / "main"
        if ref_file.exists():
            snapshot_name = ref_file.read_text(encoding="utf-8").strip()
            if snapshot_name:
                snapshot_dir = repo_dir / "snapshots" / snapshot_name
                if snapshot_dir.exists():
                    return snapshot_dir

        snapshots_dir = repo_dir / "snapshots"
        if not snapshots_dir.exists():
            continue

        snapshots = sorted(
            (candidate for candidate in snapshots_dir.iterdir() if candidate.is_dir()),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        if snapshots:
            return snapshots[0]

    return None


def load_cached_sentence_transformer(model_name: str) -> SentenceTransformer:
    cached_path = resolve_cached_hf_model_path(model_name)
    load_target = str(cached_path) if cached_path else model_name

    try:
        return SentenceTransformer(load_target)
    except Exception as exc:
        if cached_path:
            raise RuntimeError(
                f"Unable to load embedding model from local cache '{cached_path}': {exc}"
            ) from exc
        raise RuntimeError(
            f"Unable to load embedding model '{model_name}'. Cache it locally or allow "
            "network access to Hugging Face."
        ) from exc


def load_cached_cross_encoder(model_name: str) -> CrossEncoder:
    cached_path = resolve_cached_hf_model_path(model_name)
    load_target = str(cached_path) if cached_path else model_name

    try:
        return CrossEncoder(load_target)
    except Exception as exc:
        if cached_path:
            raise RuntimeError(
                f"Unable to load reranker model from local cache '{cached_path}': {exc}"
            ) from exc
        raise RuntimeError(
            f"Unable to load reranker model '{model_name}'. Cache it locally or allow "
            "network access to Hugging Face."
        ) from exc


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    model_name = env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
    model = load_cached_sentence_transformer(model_name)
    expected_dim = int(env("EMBEDDING_VECTOR_DIM", str(DEFAULT_VECTOR_DIM)))
    model_dim = model.get_sentence_embedding_dimension()
    if model_dim != expected_dim:
        raise RuntimeError(
            f"Model dimension is {model_dim}, but EMBEDDING_VECTOR_DIM is {expected_dim}."
        )
    return model


@lru_cache(maxsize=1)
def get_reranker_model() -> CrossEncoder:
    model_name = env("RERANKER_MODEL_NAME", DEFAULT_RERANKER_MODEL)
    return load_cached_cross_encoder(model_name)


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    host = env("QDRANT_HOST", "127.0.0.1")
    port = int(env("QDRANT_PORT", "6333"))
    api_key = env("QDRANT_API_KEY")
    return QdrantClient(
        host=host,
        port=port,
        api_key=api_key if api_key else None,
        timeout=30,
    )


@lru_cache(maxsize=1)
def get_bm25_index() -> tuple[BM25Okapi, list[str], dict[str, dict]]:
    """Lazy-load BM25 index over all YT video payloads (title + tags).

    Returns (bm25_index, point_id_list, {point_id: payload}).
    """
    client = get_qdrant_client()
    yt_collection = env("QDRANT_YT_COLLECTION", DEFAULT_YT_COLLECTION)

    all_points = []
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=yt_collection,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(points)
        if next_offset is None:
            break
        offset = next_offset

    corpus: list[list[str]] = []
    point_ids: list[str] = []
    payloads: dict[str, dict] = {}

    for pt in all_points:
        p = pt.payload or {}
        title = p.get("title", "")
        tags = " ".join(p.get("tags", []))
        text = f"{title} {tags}".lower().split()
        corpus.append(text)
        pid = str(pt.id)
        point_ids.append(pid)
        payloads[pid] = p

    bm25 = BM25Okapi(corpus)
    return bm25, point_ids, payloads


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    semantic_ids: list[str],
    bm25_ids: list[str],
    k: int = RRF_K,
) -> dict[str, float]:
    """Merge two ranked ID lists using Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    for rank, pid in enumerate(semantic_ids):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
    for rank, pid in enumerate(bm25_ids):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
    return scores


def apply_metadata_boosts(
    rrf_scores: dict[str, float],
    payloads: dict[str, dict],
    skill_category: str,
    skill_proficiency: str,
) -> dict[str, float]:
    """Apply sector-alignment and proficiency-alignment multipliers."""
    boosted: dict[str, float] = {}
    for pid, score in rrf_scores.items():
        p = payloads.get(pid, {})
        if skill_category and p.get("sector", "") == skill_category:
            score *= SECTOR_BOOST
        if skill_proficiency and p.get("proficiency_level", "") == skill_proficiency:
            score *= PROFICIENCY_BOOST
        boosted[pid] = score
    return boosted


def build_sf_rerank_text(skill_payload: dict) -> str:
    """Build the query-side text for cross-encoder reranking."""
    title = skill_payload.get("skill_title", "")
    category = skill_payload.get("category", "")
    prof_level = skill_payload.get("proficiency_level", "")
    return f"Skill: {title}. Category: {category}. Proficiency level: {prof_level}."


def build_yt_rerank_text(video_payload: dict) -> str:
    """Build the document-side text for cross-encoder reranking."""
    title = video_payload.get("title", "")
    desc = (video_payload.get("description") or "")[:300]
    tags = ", ".join(video_payload.get("tags", [])[:15])
    skill = video_payload.get("skill_name", "")
    competency = video_payload.get("competency", "")
    return (
        f"Title: {title}. Description: {desc}. "
        f"Tags: {tags}. Skill: {skill}. Competency: {competency}."
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RecommendRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query text.")
    num_videos: int = Field(3, ge=1, le=20)


class MatchedSkill(BaseModel):
    score: float
    skill_title: str
    proficiency_level: str
    category: str
    tsc_ccs_code: str
    skill_type: str


class RecommendedVideo(BaseModel):
    rrf_score: float
    reranker_score: float
    video_id: str
    title: str
    description: str
    channel_title: str
    thumbnail_url: str
    published_at: str
    duration: str
    view_count: int
    like_count: int
    comment_count: int
    tags: list[str]
    sector: str
    skill_name: str
    competency: str
    proficiency_level: str


class RecommendResponse(BaseModel):
    query: str
    retrieval_method: str
    matched_skill: MatchedSkill
    recommended_videos: list[RecommendedVideo]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/recommend/videos", response_model=RecommendResponse)
def recommend_videos(payload: RecommendRequest):
    query_text = payload.query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Query text cannot be empty.")

    try:
        model = get_embedding_model()
        reranker = get_reranker_model()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    qdrant = get_qdrant_client()

    # ------------------------------------------------------------------
    # Hop 1: Query → top-1 SF skill match
    # ------------------------------------------------------------------
    query_vector = model.encode(
        query_text,
        normalize_embeddings=True,
    ).tolist()

    sf_collection = env("QDRANT_COLLECTION", DEFAULT_SF_COLLECTION)
    try:
        skill_hits = qdrant.search(
            collection_name=sf_collection,
            query_vector=query_vector,
            limit=1,
            with_payload=True,
            with_vectors=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Qdrant SF skill search failed: {exc}",
        ) from exc

    if not skill_hits:
        raise HTTPException(
            status_code=404,
            detail="No matching skill found for the given query.",
        )

    top_skill = skill_hits[0]
    skill_payload = top_skill.payload or {}

    matched_skill = MatchedSkill(
        score=float(top_skill.score),
        skill_title=skill_payload.get("skill_title", ""),
        proficiency_level=str(skill_payload.get("proficiency_level", "")),
        category=skill_payload.get("category", ""),
        tsc_ccs_code=skill_payload.get("tsc_ccs_code", ""),
        skill_type=skill_payload.get("skill_type", ""),
    )

    skill_vector = top_skill.vector
    if skill_vector is None:
        raise HTTPException(
            status_code=500,
            detail="Skill vector not returned from Qdrant.",
        )

    # ------------------------------------------------------------------
    # Hop 2 — Path A: Semantic search (skill vector → YT cosine)
    # ------------------------------------------------------------------
    yt_collection = env("QDRANT_YT_COLLECTION", DEFAULT_YT_COLLECTION)
    try:
        semantic_hits = qdrant.search(
            collection_name=yt_collection,
            query_vector=skill_vector,
            limit=SEMANTIC_CANDIDATES,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Qdrant YT semantic search failed: {exc}",
        ) from exc

    semantic_ids = [str(hit.id) for hit in semantic_hits]
    semantic_payloads = {str(hit.id): hit.payload or {} for hit in semantic_hits}

    # ------------------------------------------------------------------
    # Hop 2 — Path B: BM25 lexical search (skill_title → YT title+tags)
    # ------------------------------------------------------------------
    bm25, bm25_point_ids, bm25_payloads = get_bm25_index()

    skill_title_tokens = matched_skill.skill_title.lower().split()
    bm25_scores = bm25.get_scores(skill_title_tokens)
    top_bm25_indices = np.argsort(bm25_scores)[::-1][:BM25_CANDIDATES]
    bm25_ranked_ids = [bm25_point_ids[i] for i in top_bm25_indices if bm25_scores[i] > 0]

    # ------------------------------------------------------------------
    # Merge: Reciprocal Rank Fusion
    # ------------------------------------------------------------------
    rrf_scores = reciprocal_rank_fusion(semantic_ids, bm25_ranked_ids)

    # Collect payloads from both sources
    all_payloads: dict[str, dict] = {}
    all_payloads.update(bm25_payloads)       # BM25 has all YT payloads
    all_payloads.update(semantic_payloads)    # Semantic may have fresher data

    # ------------------------------------------------------------------
    # Metadata boosts
    # ------------------------------------------------------------------
    boosted_scores = apply_metadata_boosts(
        rrf_scores,
        all_payloads,
        skill_category=matched_skill.category,
        skill_proficiency=matched_skill.proficiency_level,
    )

    # ------------------------------------------------------------------
    # Select top candidates for reranking
    # ------------------------------------------------------------------
    sorted_candidates = sorted(boosted_scores.items(), key=lambda x: x[1], reverse=True)
    rerank_candidates = sorted_candidates[:RERANK_TOP_N]

    if not rerank_candidates:
        return RecommendResponse(
            query=query_text,
            retrieval_method="hybrid_rrf_reranked",
            matched_skill=matched_skill,
            recommended_videos=[],
        )

    # ------------------------------------------------------------------
    # Cross-encoder reranking
    # ------------------------------------------------------------------
    sf_text = build_sf_rerank_text(skill_payload)
    rerank_pairs = []
    rerank_pids = []
    rerank_rrf_scores = []

    for pid, rrf_score in rerank_candidates:
        yt_payload = all_payloads.get(pid, {})
        yt_text = build_yt_rerank_text(yt_payload)
        rerank_pairs.append((sf_text, yt_text))
        rerank_pids.append(pid)
        rerank_rrf_scores.append(rrf_score)

    reranker_scores = reranker.predict(rerank_pairs)

    # Sort by reranker score descending, take top num_videos
    ranked_indices = np.argsort(reranker_scores)[::-1]
    final_indices = ranked_indices[: payload.num_videos]

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------
    recommended_videos = []
    for idx in final_indices:
        pid = rerank_pids[idx]
        p = all_payloads.get(pid, {})
        recommended_videos.append(
            RecommendedVideo(
                rrf_score=float(rerank_rrf_scores[idx]),
                reranker_score=float(reranker_scores[idx]),
                video_id=p.get("video_id", ""),
                title=p.get("title", ""),
                description=p.get("description", ""),
                channel_title=p.get("channel_title", ""),
                thumbnail_url=p.get("thumbnail_url", ""),
                published_at=p.get("published_at", ""),
                duration=p.get("duration", ""),
                view_count=p.get("view_count", 0),
                like_count=p.get("like_count", 0),
                comment_count=p.get("comment_count", 0),
                tags=p.get("tags", []),
                sector=p.get("sector", ""),
                skill_name=p.get("skill_name", ""),
                competency=p.get("competency", ""),
                proficiency_level=p.get("proficiency_level", ""),
            )
        )

    return RecommendResponse(
        query=query_text,
        retrieval_method="hybrid_rrf_reranked",
        matched_skill=matched_skill,
        recommended_videos=recommended_videos,
    )
