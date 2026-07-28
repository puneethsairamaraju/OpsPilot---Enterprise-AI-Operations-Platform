import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Chunk, Document

TOKEN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]+")
VECTOR_SIZE = 96


def tokenize(text: str) -> list[str]:
    return [word.lower() for word in TOKEN.findall(text)]


def embed(text: str) -> list[float]:
    """Deterministic feature-hash vector; swap for a provider embedding in production."""
    vector = [0.0] * VECTOR_SIZE
    for word in tokenize(text):
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        index = int.from_bytes(digest, "big") % VECTOR_SIZE
        vector[index] += -1.0 if digest[0] & 1 else 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def chunk_text(text: str, size: int = 110, overlap: int = 20) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    return [" ".join(words[start : start + size]) for start in range(0, len(words), step)]


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    title: str
    content: str
    score: float


def hybrid_search(
    db: Session,
    tenant_id: str,
    query: str,
    top_k: int = 5,
    role: str | None = None,
) -> list[Hit]:
    chunks = db.scalars(
        select(Chunk)
        .options(joinedload(Chunk.document))
        .join(Document)
        .where(Chunk.tenant_id == tenant_id)
    ).all()
    if role:
        chunks = [
            chunk
            for chunk in chunks
            if not chunk.document.allowed_roles or role in chunk.document.allowed_roles
        ]
    if not chunks:
        return []
    query_tokens = tokenize(query)
    query_counts = Counter(query_tokens)
    q_vector = embed(query)
    document_frequency = Counter()
    tokenized = {}
    for chunk in chunks:
        tokens = tokenize(chunk.content)
        tokenized[chunk.id] = tokens
        document_frequency.update(set(tokens))
    scored = []
    total = len(chunks)
    for chunk in chunks:
        counts = Counter(tokenized[chunk.id])
        lexical = 0.0
        for term, q_count in query_counts.items():
            tf = counts[term] / (counts[term] + 1.2) if counts[term] else 0
            idf = math.log(1 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            lexical += q_count * tf * idf
        lexical_norm = lexical / (lexical + 1.0)
        dense = max(0.0, cosine(q_vector, chunk.embedding))
        phrase_bonus = 0.08 if query.lower() in chunk.content.lower() else 0.0
        score = min(1.0, 0.58 * lexical_norm + 0.34 * dense + phrase_bonus)
        scored.append(
            Hit(chunk.id, chunk.document_id, chunk.document.title, chunk.content, score)
        )
    return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]
