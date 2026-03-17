from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pipelines.youtube.youtube_vector_index import embed_and_upsert_videos, load_videos_from_mongo


def main() -> None:
    print("Loading videos from MongoDB...")
    docs = load_videos_from_mongo()
    if not docs:
        print("No documents found; exiting.")
        return

    summary = embed_and_upsert_videos(docs)
    print(
        "YouTube embedding + upsert job completed. "
        f"Indexed {summary['embedding_indexed']}/{summary['embedding_requested']} documents "
        f"into {summary['embedding_collection']}."
    )


if __name__ == "__main__":
    main()
