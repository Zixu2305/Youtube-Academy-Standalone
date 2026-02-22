from __future__ import annotations

import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_COLLECTION_NAME = "sf_skill_level_docs__bge_base__768"
DEFAULT_VECTOR_DIM = 768
SQL_PATH = ROOT_DIR / "sql" / "get_sf_skill_level_docs.sql"

SAMPLE_QUERIES = [
    "data analysis",
    "python pandas",
    "cybersecurity incident response",
    "digital marketing strategy",
    "project management stakeholder communication",
]


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
        autocommit=True,
    )


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


def load_extraction_sql() -> str:
    if not SQL_PATH.exists():
        raise FileNotFoundError(f"SQL file not found: {SQL_PATH}")
    return SQL_PATH.read_text(encoding="utf-8")


def count_mysql_docs(sql: str) -> int:
    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SET SESSION group_concat_max_len = %s;", (1024 * 1024,))
        cur.execute(sql)
        rows = cur.fetchall()
        return len(rows)
    finally:
        cur.close()
        conn.close()


def count_qdrant_points(client: QdrantClient, collection_name: str) -> int:
    result = client.count(collection_name=collection_name, exact=True)
    return int(result.count)


def build_filter(exclude_retired: bool) -> models.Filter | None:
    if not exclude_retired:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="is_retired",
                match=models.MatchValue(value=False),
            )
        ]
    )


def run_query(
    client: QdrantClient,
    model: SentenceTransformer,
    collection_name: str,
    query: str,
    top_k: int = 5,
    exclude_retired: bool = True,
):
    vector = model.encode(query, normalize_embeddings=True).tolist()
    return client.search(
        collection_name=collection_name,
        query_vector=vector,
        query_filter=build_filter(exclude_retired),
        limit=top_k,
        with_payload=True,
        with_vectors=False,
    )


def print_sample_query_results(
    client: QdrantClient,
    model: SentenceTransformer,
    collection_name: str,
) -> None:
    print("\nSample query results:")
    for query in SAMPLE_QUERIES:
        hits = run_query(
            client=client,
            model=model,
            collection_name=collection_name,
            query=query,
            top_k=5,
            exclude_retired=True,
        )
        print(f"\nQuery: {query}")
        if not hits:
            print("  (no results)")
            continue

        for hit in hits:
            payload = hit.payload or {}
            point_id = payload.get("point_id", str(hit.id))
            print(
                "  "
                f"{point_id} | score={hit.score:.4f} | "
                f"{payload.get('skill_title', '')} | "
                f"level={payload.get('proficiency_level', '')} | "
                f"retired={payload.get('is_retired', '')}"
            )


def verify_retired_filter(
    client: QdrantClient,
    model: SentenceTransformer,
    collection_name: str,
) -> bool:
    test_query = "data analysis"
    filtered_hits = run_query(
        client=client,
        model=model,
        collection_name=collection_name,
        query=test_query,
        top_k=20,
        exclude_retired=True,
    )
    unfiltered_hits = run_query(
        client=client,
        model=model,
        collection_name=collection_name,
        query=test_query,
        top_k=20,
        exclude_retired=False,
    )

    filtered_has_retired = any(
        bool((hit.payload or {}).get("is_retired")) for hit in filtered_hits
    )
    retired_in_unfiltered = sum(
        1 for hit in unfiltered_hits if bool((hit.payload or {}).get("is_retired"))
    )

    print("\nRetired filter check:")
    print(f"  filtered_hits={len(filtered_hits)} retired_hits={int(filtered_has_retired)}")
    print(
        f"  unfiltered_hits={len(unfiltered_hits)} "
        f"retired_hits={retired_in_unfiltered}"
    )
    return not filtered_has_retired


def main() -> int:
    collection_name = env("QDRANT_COLLECTION", DEFAULT_COLLECTION_NAME)
    model_name = env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
    expected_dim = int(env("EMBEDDING_VECTOR_DIM", str(DEFAULT_VECTOR_DIM)))

    extraction_sql = load_extraction_sql()
    mysql_count = count_mysql_docs(extraction_sql)
    print(f"MySQL extracted docs: {mysql_count}")

    qdrant_client = get_qdrant_client()
    qdrant_count = count_qdrant_points(qdrant_client, collection_name)
    print(f"Qdrant indexed points: {qdrant_count}")

    model = SentenceTransformer(model_name)
    model_dim = model.get_sentence_embedding_dimension()
    print(f"Embedding model dimension: {model_dim}")

    failures = 0
    if model_dim != expected_dim:
        print(
            f"ERROR: model dim mismatch. model={model_dim}, expected={expected_dim}"
        )
        failures += 1

    if mysql_count != qdrant_count:
        print("ERROR: extracted doc count does not match Qdrant point count.")
        failures += 1

    print_sample_query_results(qdrant_client, model, collection_name)
    filter_ok = verify_retired_filter(qdrant_client, model, collection_name)
    if not filter_ok:
        print("ERROR: retired filter failed.")
        failures += 1

    if failures:
        print(f"\nSmoke test finished with {failures} failure(s).")
        return 1

    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
