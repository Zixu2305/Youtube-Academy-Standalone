from __future__ import annotations

import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import mysql.connector
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_VECTOR_DIM = 768
DEFAULT_COLLECTION_NAME = "sf_skill_level_docs__bge_base__768"
DEFAULT_BATCH_SIZE = 32
SQL_PATH = ROOT_DIR / "sql" / "get_sf_skill_level_docs.sql"


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_mysql_connection():
    host = env("DB_HOST", "127.0.0.1")
    if env("DB_PORT"):
        port = int(env("DB_PORT", "3306"))
    else:
        port = 3306 if host == "mysql" else int(env("MYSQL_PORT", "3306"))

    return mysql.connector.connect(
        host=host,
        port=port,
        user=env("DB_USER", env("MYSQL_USER", "yta")),
        password=env("DB_PASSWORD", env("MYSQL_PASSWORD", "")),
        database=env("DB_NAME", env("MYSQL_DATABASE", "yta")),
        autocommit=False,
    )


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


def load_skill_level_docs():
    if not SQL_PATH.exists():
        raise FileNotFoundError(f"SQL file not found: {SQL_PATH}")

    sql = SQL_PATH.read_text(encoding="utf-8")
    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SET SESSION group_concat_max_len = %s;", (1024 * 1024,))
        cur.execute(sql)
        rows = cur.fetchall()
        return rows
    finally:
        cur.close()
        conn.close()


def ensure_collection_compatibility(
    client: QdrantClient, collection_name: str, expected_dim: int
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


def build_weighted_sf_text(row: dict) -> str:
    """Build field-weighted embedding text.

    Weighting: skill_title 3x, category 2x, everything else 1x.
    Repeating high-priority fields nudges the encoder to give them
    more influence in the resulting vector.
    """
    skill_title = row.get("skill_title") or ""
    category = row.get("category") or ""

    parts: list[str] = []
    # 3x weight on skill_title
    for _ in range(3):
        parts.append(f"Skill title: {skill_title}")
    # 2x weight on category
    for _ in range(2):
        parts.append(f"Category: {category}")
    # 1x for remaining metadata
    parts.append(f"Skill type: {row.get('skill_type', '')}")
    parts.append(f"TSC/CCS code: {row.get('tsc_ccs_code', '')}")
    parts.append(f"Proficiency level: {row.get('proficiency_level', '')}")
    prof_desc = row.get("proficiency_description") or ""
    parts.append(f"Proficiency description: {prof_desc}")
    knowledge = row.get("knowledge_block") or ""
    if knowledge:
        parts.append(f"Knowledge items:\n{knowledge}")
    ability = row.get("ability_block") or ""
    if ability:
        parts.append(f"Ability items:\n{ability}")
    return "\n\n".join(parts)


def build_payload(row: dict) -> dict:
    return {
        "point_id": row["point_id"],
        "sf_skill_id": int(row["sf_skill_id"]),
        "proficiency_level": row["proficiency_level"],
        "tsc_ccs_code": row["tsc_ccs_code"] or "",
        "skill_title": row["skill_title"] or "",
        "category": row["category"] or "",
        "skill_type": row["skill_type"] or "",
        "is_retired": bool(row["is_retired"]),
    }


def upsert_docs(
    client: QdrantClient,
    collection_name: str,
    model: SentenceTransformer,
    rows: list[dict],
    batch_size: int,
) -> None:
    total = len(rows)
    upserted = 0

    for start in range(0, total, batch_size):
        batch = rows[start : start + batch_size]
        texts = [build_weighted_sf_text(row) for row in batch]
        vectors = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        points = []
        for row, vector in zip(batch, vectors):
            point_id = str(row["point_id"])
            points.append(
                models.PointStruct(
                    # Qdrant IDs must be either unsigned ints or UUIDs.
                    id=str(uuid5(NAMESPACE_URL, f"sf_skill_level::{point_id}")),
                    vector=vector.tolist(),
                    payload=build_payload(row),
                )
            )

        client.upsert(
            collection_name=collection_name,
            wait=True,
            points=points,
        )
        upserted += len(points)
        print(f"Upserted {upserted}/{total} points")


def main() -> None:
    model_name = env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
    expected_dim = int(env("EMBEDDING_VECTOR_DIM", str(DEFAULT_VECTOR_DIM)))
    collection_name = env("QDRANT_COLLECTION", DEFAULT_COLLECTION_NAME)
    batch_size = int(env("EMBEDDING_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))

    print("Loading skill-level docs from MySQL...")
    rows = load_skill_level_docs()
    print(f"Loaded {len(rows)} rows.")
    if not rows:
        print("No rows found; exiting.")
        return

    print(f"Loading embedding model: {model_name}")
    model = SentenceTransformer(model_name)
    model_dim = model.get_sentence_embedding_dimension()
    if model_dim != expected_dim:
        raise RuntimeError(
            f"Model dimension is {model_dim}, but EMBEDDING_VECTOR_DIM is {expected_dim}."
        )

    client = get_qdrant_client()
    ensure_collection_compatibility(client, collection_name, expected_dim)
    print(f"Upserting into Qdrant collection: {collection_name}")

    upsert_docs(
        client=client,
        collection_name=collection_name,
        model=model,
        rows=rows,
        batch_size=batch_size,
    )
    print("Embedding + upsert job completed.")


if __name__ == "__main__":
    main()
