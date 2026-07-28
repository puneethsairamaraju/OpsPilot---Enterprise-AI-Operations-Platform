import re
import time
from dataclasses import dataclass

from app.retrieval import Hit

SENTENCE = re.compile(r"(?<=[.!?])\s+")
PII_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
]


def mask_pii(text: str) -> str:
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


@dataclass
class WorkflowResult:
    answer: str
    citations: list[dict]
    confidence: float
    latency_ms: float
    estimated_cost: float


def grounded_answer(question: str, hits: list[Hit]) -> WorkflowResult:
    """Provider-neutral synthesis node with an extractive local implementation."""
    started = time.perf_counter()
    if not hits or hits[0].score < 0.08:
        return WorkflowResult(
            answer="I could not find enough authorized evidence to answer that question.",
            citations=[],
            confidence=0.12,
            latency_ms=(time.perf_counter() - started) * 1000,
            estimated_cost=0,
        )
    question_terms = set(re.findall(r"\w+", question.lower()))
    candidates = []
    for hit in hits[:3]:
        for sentence in SENTENCE.split(hit.content):
            terms = set(re.findall(r"\w+", sentence.lower()))
            overlap = len(question_terms & terms) / max(1, len(question_terms))
            candidates.append((overlap + hit.score, sentence.strip(), hit))
    selected = sorted(candidates, reverse=True, key=lambda item: item[0])[:3]
    answer = " ".join(mask_pii(sentence) for _, sentence, _ in selected if sentence)
    citations = []
    seen = set()
    for _, _, hit in selected:
        if hit.chunk_id not in seen:
            citations.append(
                {
                    "document_id": hit.document_id,
                    "chunk_id": hit.chunk_id,
                    "title": hit.title,
                    "score": round(hit.score, 3),
                }
            )
            seen.add(hit.chunk_id)
    confidence = min(0.98, 0.35 + hits[0].score * 0.7 + min(len(citations), 2) * 0.05)
    return WorkflowResult(
        answer=answer,
        citations=citations,
        confidence=round(confidence, 3),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        estimated_cost=0,
    )

