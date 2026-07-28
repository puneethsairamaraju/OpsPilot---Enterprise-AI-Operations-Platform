"""Provider-neutral, idempotent connector ingestion pipeline.

Provider adapters translate Slack/Drive/SharePoint/etc. payloads into SourceEvent.
This module owns the shared correctness rules: tenant isolation, idempotency,
upsert/delete behavior, indexing, checkpoints, and ACL metadata.
"""

import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Chunk, Document, SourceConnection, SyncEvent, User
from app.retrieval import chunk_text, embed
from app.schemas import SourceEvent


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def verify_webhook_secret(connection: SourceConnection, provided: str | None) -> None:
    if not provided or not secrets.compare_digest(
        connection.webhook_secret_hash, hash_secret(provided)
    ):
        raise HTTPException(status_code=401, detail="Invalid connector secret")


def process_event(
    db: Session,
    connection: SourceConnection,
    event: SourceEvent,
    system_user: User,
) -> str:
    """Process one provider event exactly once and return its outcome."""
    existing_event = db.scalar(
        select(SyncEvent).where(
            SyncEvent.connection_id == connection.id,
            SyncEvent.provider_event_id == event.event_id,
        )
    )
    if existing_event:
        return "duplicate"

    document = db.scalar(
        select(Document).where(
            Document.tenant_id == connection.tenant_id,
            Document.source == connection.provider,
            Document.external_id == event.external_id,
        )
    )
    outcome = event.operation
    if event.operation == "delete":
        if document:
            db.delete(document)
    else:
        if not event.title or not event.content or len(event.content.strip()) < 3:
            raise HTTPException(
                status_code=422, detail="Upsert events require title and content"
            )
        if document is None:
            document = Document(
                tenant_id=connection.tenant_id,
                title=event.title,
                source=connection.provider,
                external_id=event.external_id,
                source_url=event.source_url,
                source_metadata=event.metadata,
                allowed_roles=event.allowed_roles,
                classification="internal",
                content=event.content,
                created_by=system_user.id,
            )
            db.add(document)
            db.flush()
        else:
            document.title = event.title
            document.content = event.content
            document.source_url = event.source_url
            document.source_metadata = event.metadata
            document.allowed_roles = event.allowed_roles
            db.execute(delete(Chunk).where(Chunk.document_id == document.id))
        for position, text in enumerate(chunk_text(event.content)):
            db.add(
                Chunk(
                    document_id=document.id,
                    tenant_id=connection.tenant_id,
                    position=position,
                    content=text,
                    embedding=embed(text),
                )
            )

    db.add(
        SyncEvent(
            connection_id=connection.id,
            tenant_id=connection.tenant_id,
            provider_event_id=event.event_id,
            event_type=event.operation,
            external_id=event.external_id,
            detail={"source_url": event.source_url, "provider": connection.provider},
        )
    )
    connection.checkpoint = event.checkpoint or connection.checkpoint
    connection.last_synced_at = datetime.now(UTC)
    connection.last_error = None
    db.commit()
    return outcome
