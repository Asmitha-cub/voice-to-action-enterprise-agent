"""Enterprise knowledge retrieval.

Knowledge documents live in the database (dynamic). When Qdrant is reachable the
docs are indexed there for vector search; otherwise a keyword score over the
same DB-backed documents is used so retrieval always works.
"""
import logging

from sqlalchemy import select

from .config import settings
from .db import KnowledgeDoc, session_scope

log = logging.getLogger(__name__)


def _load_docs() -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(KnowledgeDoc)).all()
        return [{'id': d.doc_key, 'title': d.title, 'text': d.text} for d in rows]


def _hash_vector(text: str, size: int = 384) -> list[float]:
    vec = [0.0] * size
    for token in text.lower().split():
        vec[hash(token) % size] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def _keyword_search(query: str, docs: list[dict], limit: int) -> list[dict]:
    q = query.lower()
    scored = []
    for doc in docs:
        score = sum(1 for word in q.split() if len(word) > 2 and word in doc['text'].lower())
        scored.append((score, doc))
    ranked = [doc for score, doc in sorted(scored, key=lambda x: x[0], reverse=True) if score]
    return ranked[:limit] if ranked else [doc for _, doc in scored[:limit]]


def search_knowledge(query: str, limit: int = 3) -> list[dict]:
    docs = _load_docs()
    if not docs:
        return []
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        client = QdrantClient(url=settings().qdrant_url, api_key=settings().qdrant_api_key, timeout=5)
        collection = settings().qdrant_collection
        if not client.collection_exists(collection):
            client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            )
        # Keep the index in sync with the DB (idempotent upsert).
        points = [PointStruct(id=i + 1, vector=_hash_vector(d['text']), payload=d)
                  for i, d in enumerate(docs)]
        client.upsert(collection_name=collection, points=points)
        hits = client.query_points(
            collection_name=collection, query=_hash_vector(query), limit=limit, with_payload=True,
        ).points
        results = [h.payload for h in hits if h.payload]
        if results:
            return results
    except Exception as exc:  # noqa: BLE001
        log.warning('Qdrant unavailable, using DB keyword fallback: %s', exc)

    return _keyword_search(query, docs, limit)
