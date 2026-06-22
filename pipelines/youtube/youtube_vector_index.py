from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from pymongo import MongoClient
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_VECTOR_DIM = 768
DEFAULT_COLLECTION_NAME = "youtube_videos__bge_base__768"
DEFAULT_BATCH_SIZE = 32


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


def get_mongo_client() -> MongoClient:
    host = env("MONGO_HOST", "127.0.0.1")
    port = int(env("MONGO_PORT", "27017"))
    username = env("MONGO_ROOT_USERNAME")
    password = env("MONGO_ROOT_PASSWORD")
    auth_source = env("MONGO_AUTH_SOURCE", "admin")
    auth_mechanism = env("MONGO_AUTH_MECHANISM", "SCRAM-SHA-256")
    if username and password:
        return MongoClient(
            host=host,
            port=port,
            username=username,
            password=password,
            authSource=auth_source,
            authMechanism=auth_mechanism,
        )
    return MongoClient(host=host, port=port)


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    host = env("QDRANT_HOST", "127.0.0.1")
    port = int(env("QDRANT_PORT", "6333"))
    api_key = env("QDRANT_API_KEY")
    return QdrantClient(
        host=host,
        port=port,
        api_key=api_key if api_key else None,
        timeout=60,
    )


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    model_name = env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
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


def resolve_vector_params(collection_info: models.CollectionInfo) -> models.VectorParams:
    vectors = collection_info.config.params.vectors
    if isinstance(vectors, models.VectorParams):
        return vectors
    if isinstance(vectors, dict):
        if "" in vectors:
            return vectors[""]
        first_key = next(iter(vectors.keys()))
        return vectors[first_key]
    raise RuntimeError("Unable to resolve vector params from collection info.")


def load_videos_from_mongo() -> list[dict]:
    client = get_mongo_client()
    db_name = env("MONGO_DATABASE", "yta")
    db = client[db_name]
    docs = list(db["videos"].find({}))
    client.close()
    print(f"Loaded {len(docs)} video documents from MongoDB.")
    return docs


def build_embed_text(doc: dict) -> str:
    """Build field-weighted embedding text for a YouTube video document."""
    title = (doc.get("title") or "").strip()
    skill = (doc.get("skill_name") or "").strip()
    tags = doc.get("tags") or []
    tags_str = ", ".join(tags[:20])

    parts: list[str] = []
    for _ in range(3):
        parts.append(f"Video title: {title}")
    for _ in range(2):
        parts.append(f"Skill: {skill}")
    for _ in range(2):
        parts.append(f"Tags: {tags_str}")

    description = (doc.get("description") or "").strip()
    parts.append(f"Description: {description[:500]}")
    channel = (doc.get("channelTitle") or "").strip()
    parts.append(f"Channel: {channel}")
    sector = (doc.get("sector") or "").strip()
    if sector:
        parts.append(f"Sector: {sector}")
    competency = (doc.get("competency") or "").strip()
    if competency:
        parts.append(f"Competency: {competency}")
    prof_level = (doc.get("proficiency_level") or "").strip()
    if prof_level:
        parts.append(f"Proficiency level: {prof_level}")
    prof_desc = (doc.get("proficiency_description") or "").strip()
    if prof_desc:
        parts.append(f"Proficiency description: {prof_desc[:300]}")
    return "\n\n".join(parts)


def _proficiency_sort_key(value: str) -> tuple[int, str]:
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits:
        return int(digits), value.lower()
    return 9999, value.lower()


def _mapping_key(mapping: dict) -> tuple[str, str]:
    return (
        str(mapping.get("proficiency_level") or "").strip(),
        str(mapping.get("competency") or "").strip(),
    )


def mapping_from_single_fields(doc: dict) -> dict | None:
    competency = str(doc.get("competency") or "").strip()
    if not competency:
        return None
    return {
        "competency": competency,
        "item_type": str(doc.get("item_type") or ""),
        "proficiency_level": str(doc.get("proficiency_level") or doc.get("proficiency") or ""),
        "proficiency_description": str(doc.get("proficiency_description") or ""),
    }


def normalize_video_mappings(doc: dict) -> list[dict]:
    mappings: list[dict] = []
    primary_mapping = mapping_from_single_fields(doc)
    if primary_mapping:
        mappings.append(primary_mapping)

    saved_mappings = doc.get("additional_mappings")
    if not isinstance(saved_mappings, list):
        saved_mappings = doc.get("mappings")

    if isinstance(saved_mappings, list):
        for mapping in saved_mappings:
            if not isinstance(mapping, dict):
                continue
            mappings.append(
                {
                    "competency": str(mapping.get("competency") or ""),
                    "item_type": str(mapping.get("item_type") or ""),
                    "proficiency_level": str(mapping.get("proficiency_level") or ""),
                    "proficiency_description": str(mapping.get("proficiency_description") or ""),
                }
            )

    normalized: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for mapping in mappings:
        key = _mapping_key(mapping)
        if not all(key) or key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "competency": key[1],
                "item_type": str(mapping.get("item_type") or ""),
                "proficiency_level": key[0],
                "proficiency_description": str(mapping.get("proficiency_description") or ""),
            }
        )
    return normalized


def build_mapping_payload(doc: dict) -> dict:
    mappings = normalize_video_mappings(doc)
    mapped_proficiency_levels = sorted(
        {mapping["proficiency_level"] for mapping in mappings if mapping.get("proficiency_level")},
        key=_proficiency_sort_key,
    )
    mapped_competencies = [
        mapping["competency"]
        for mapping in mappings
        if mapping.get("competency")
    ]
    mapping_keys = [
        f"{mapping['proficiency_level']}||{mapping['competency']}"
        for mapping in mappings
        if mapping.get("proficiency_level") and mapping.get("competency")
    ]
    primary_proficiency_level = str(
        doc.get("proficiency_level") or doc.get("proficiency") or ""
    ).strip()
    payload = {
        "competency_mappings": mappings,
        "mapped_proficiency_levels": mapped_proficiency_levels,
        "mapped_competencies": mapped_competencies,
        "mapped_competency_keys": mapping_keys,
        "mapping_count": len(mappings),
    }
    if primary_proficiency_level:
        payload["proficiency_level"] = primary_proficiency_level
    return payload


def build_payload(doc: dict) -> dict:
    payload = {
        "video_id": doc.get("videoId", ""),
        "title": doc.get("title", ""),
        "description": (doc.get("description") or "")[:500],
        "channel_title": doc.get("channelTitle", ""),
        "thumbnail_url": doc.get("thumbnailUrl", ""),
        "published_at": doc.get("publishedAt", ""),
        "duration": doc.get("duration", ""),
        "view_count": doc.get("viewCount", 0),
        "like_count": doc.get("likeCount", 0),
        "comment_count": doc.get("commentCount", 0),
        "tags": (doc.get("tags") or [])[:20],
        "sector": doc.get("sector", ""),
        "skill_name": doc.get("skill_name", ""),
        "competency": doc.get("competency", ""),
        "proficiency_level": doc.get("proficiency_level") or doc.get("proficiency", ""),
    }
    payload.update(build_mapping_payload(doc))
    return payload


def make_point_id(doc: dict) -> str:
    video_id = doc.get("videoId", "") or doc.get("video_id", "")
    skill_name = doc.get("skill_name", "")
    proficiency_level = doc.get("proficiency_level", "")
    competency = doc.get("competency", "")
    return str(uuid5(NAMESPACE_URL, f"yt_video::{video_id}::{skill_name}::{proficiency_level}::{competency}"))


def sync_video_mapping_payload(doc: dict, *, wait: bool = True) -> dict[str, object]:
    video_id = str(doc.get("videoId") or doc.get("video_id") or "").strip()
    skill_name = str(doc.get("skill_name") or "").strip()
    if not video_id or not skill_name:
        return {
            "qdrant_payload_status": "skipped",
            "qdrant_payload_updated": 0,
            "qdrant_payload_error": "Missing videoId or skill_name.",
        }

    collection_name = env("QDRANT_YT_COLLECTION", DEFAULT_COLLECTION_NAME)
    client = get_qdrant_client()
    result = client.set_payload(
        collection_name=collection_name,
        payload=build_mapping_payload(doc),
        points=[make_point_id(doc)],
        wait=wait,
    )
    return {
        "qdrant_payload_status": getattr(result, "status", "completed"),
        "qdrant_payload_updated": 1,
        "qdrant_payload_collection": collection_name,
    }


def ensure_collection_compatibility(
    client: QdrantClient,
    collection_name: str,
    expected_dim: int,
) -> None:
    if not client.collection_exists(collection_name=collection_name):
        raise RuntimeError(
            f"Collection '{collection_name}' does not exist. "
            "Run `python qdrant/create_collections.py` first."
        )

    info = client.get_collection(collection_name=collection_name)
    vector_params = resolve_vector_params(info)
    if vector_params.size != expected_dim:
        raise RuntimeError(
            f"Collection '{collection_name}' has vector dim {vector_params.size}, "
            f"expected {expected_dim}."
        )
    if vector_params.distance != models.Distance.COSINE:
        raise RuntimeError(
            f"Collection '{collection_name}' has distance {vector_params.distance}, "
            "expected cosine."
        )


def _dedupe_docs(docs: list[dict]) -> tuple[list[dict], int]:
    unique_docs: dict[str, dict] = {}
    skipped_invalid = 0
    for doc in docs:
        video_id = str(doc.get("videoId") or "").strip()
        skill_name = str(doc.get("skill_name") or "").strip()
        if not video_id or not skill_name:
            skipped_invalid += 1
            continue
        unique_docs[make_point_id(doc)] = doc
    return list(unique_docs.values()), skipped_invalid


def embed_and_upsert_videos(
    docs: list[dict],
    *,
    batch_size: int | None = None,
    wait: bool = True,
) -> dict[str, object]:
    resolved_batch_size = batch_size or int(env("EMBEDDING_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
    collection_name = env("QDRANT_YT_COLLECTION", DEFAULT_COLLECTION_NAME)
    expected_dim = int(env("EMBEDDING_VECTOR_DIM", str(DEFAULT_VECTOR_DIM)))

    prepared_docs, skipped_invalid = _dedupe_docs(docs)
    summary: dict[str, object] = {
        "embedding_status": "skipped",
        "embedding_requested": len(prepared_docs),
        "embedding_indexed": 0,
        "embedding_skipped_invalid": skipped_invalid,
        "embedding_batch_size": resolved_batch_size,
        "embedding_collection": collection_name,
    }
    if not prepared_docs:
        return summary

    model = get_embedding_model()
    model_dim = model.get_sentence_embedding_dimension()
    if model_dim != expected_dim:
        raise RuntimeError(
            f"Model dimension is {model_dim}, but EMBEDDING_VECTOR_DIM is {expected_dim}."
        )

    client = get_qdrant_client()
    ensure_collection_compatibility(client, collection_name, expected_dim)

    total = len(prepared_docs)
    indexed = 0
    for start in range(0, total, resolved_batch_size):
        batch = prepared_docs[start : start + resolved_batch_size]
        texts = [build_embed_text(doc) for doc in batch]
        vectors = model.encode(
            texts,
            batch_size=resolved_batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        points = [
            models.PointStruct(
                id=make_point_id(doc),
                vector=vector.tolist(),
                payload=build_payload(doc),
            )
            for doc, vector in zip(batch, vectors)
        ]
        client.upsert(
            collection_name=collection_name,
            wait=wait,
            points=points,
        )
        indexed += len(points)

    summary["embedding_status"] = "completed"
    summary["embedding_indexed"] = indexed
    return summary
