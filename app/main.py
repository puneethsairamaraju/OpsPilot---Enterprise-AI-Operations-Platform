from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, inspect, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.config import settings
from app.connectors import (
    hash_secret,
    new_webhook_secret,
    process_event,
    verify_webhook_secret,
)
from app.db import Base, SessionLocal, engine, get_db
from app.ingestion import extract_upload
from app.metrics import APPROVAL_COUNT, INGEST_COUNT, QUERY_COUNT, QUERY_LATENCY
from app.models import (
    Approval,
    AuditEvent,
    Chunk,
    Document,
    Feedback,
    QueryRun,
    Role,
    SourceConnection,
    SyncEvent,
    User,
)
from app.retrieval import chunk_text, embed, hybrid_search
from app.schemas import (
    ApprovalDecision,
    ConnectionCreate,
    DocumentCreate,
    DocumentOut,
    FeedbackCreate,
    LoginRequest,
    QueryRequest,
    QueryResponse,
    SourceEvent,
    TokenResponse,
    UserOut,
)
from app.security import create_token, current_user, hash_password, require_roles, verify_password
from app.workflow import grounded_answer

STATIC = Path(__file__).parent / "static"


def audit(
    db: Session,
    user: User,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail or {},
        )
    )


def index_document(
    db: Session,
    user: User,
    *,
    title: str,
    content: str,
    source: str,
    classification: str = "internal",
    external_id: str | None = None,
) -> Document:
    doc = Document(
        tenant_id=user.tenant_id,
        title=title,
        source=source,
        external_id=external_id,
        classification=classification,
        content=content,
        created_by=user.id,
    )
    db.add(doc)
    db.flush()
    for position, chunk_content in enumerate(chunk_text(content)):
        db.add(
            Chunk(
                document_id=doc.id,
                tenant_id=user.tenant_id,
                position=position,
                content=chunk_content,
                embedding=embed(chunk_content),
            )
        )
    return doc


def seed() -> None:
    Base.metadata.create_all(engine)
    migrate_development_schema()
    with SessionLocal() as db:
        if db.scalar(select(func.count(User.id))):
            return
        users = [
            User(email="admin@opspilot.dev", name="Maya Chen", role=Role.admin, password_hash=hash_password("admin123!")),
            User(email="analyst@opspilot.dev", name="Noah Williams", role=Role.analyst, password_hash=hash_password("analyst123!")),
            User(email="viewer@opspilot.dev", name="Avery Patel", role=Role.viewer, password_hash=hash_password("viewer123!")),
        ]
        db.add_all(users)
        db.flush()
        samples = [
            (
                "Incident Response SOP",
                "Critical incidents are Severity 1 when customer data or production availability is at risk. "
                "The incident commander must open a bridge within 10 minutes, assign a communications lead, "
                "and post customer updates every 30 minutes. After recovery, the owner must complete a blameless "
                "post-incident review within five business days.",
            ),
            (
                "Access Control Policy",
                "Privileged production access requires manager approval and security approval. Access is time-bound "
                "to four hours and all actions are audited. Contractors may not receive standing administrator access. "
                "Quarterly access reviews are owned by the system owner.",
            ),
            (
                "Customer Refund Playbook",
                "Support agents may approve refunds up to 250 dollars for service outages. Refunds above 250 dollars "
                "require approval from a support manager. Requests involving suspected fraud must be escalated to Risk "
                "and must not be processed until the investigation is complete.",
            ),
        ]
        for title, content in samples:
            doc = Document(
                tenant_id=users[0].tenant_id,
                title=title,
                source="seed",
                classification="internal",
                content=content,
                created_by=users[0].id,
            )
            db.add(doc)
            db.flush()
            for position, text in enumerate(chunk_text(content)):
                db.add(
                    Chunk(
                        document_id=doc.id,
                        tenant_id=doc.tenant_id,
                        position=position,
                        content=text,
                        embedding=embed(text),
                    )
                )
        db.commit()


def migrate_development_schema() -> None:
    """Add connector columns to databases created by the original local MVP.

    Production deployments should use Alembic migrations. This small compatibility
    migration preserves existing demo data and makes upgrades painless for local users.
    """
    existing = {column["name"] for column in inspect(engine).get_columns("documents")}
    additions = {
        "external_id": "VARCHAR(255)",
        "source_url": "TEXT",
        "source_metadata": "JSON",
        "allowed_roles": "JSON",
    }
    with engine.begin() as connection:
        for column, sql_type in additions.items():
            if column not in existing:
                connection.execute(
                    sql_text(f"ALTER TABLE documents ADD COLUMN {column} {sql_type}")
                )
        connection.execute(
            sql_text(
                "UPDATE documents SET source_metadata = '{}' "
                "WHERE source_metadata IS NULL"
            )
        )
        connection.execute(
            sql_text(
                "UPDATE documents SET allowed_roles = '[]' WHERE allowed_roles IS NULL"
            )
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "healthy", "environment": settings.app_env}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    audit(db, user, "auth.login", "user", user.id)
    db.commit()
    return TokenResponse(access_token=create_token(user))


@app.get("/api/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@app.get("/api/documents", response_model=list[DocumentOut])
def documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Document)
        .where(Document.tenant_id == user.tenant_id)
        .order_by(Document.created_at.desc())
    ).all()


@app.post("/api/documents", response_model=DocumentOut, status_code=201)
def create_document(
    payload: DocumentCreate,
    user: User = Depends(require_roles(Role.admin, Role.analyst)),
    db: Session = Depends(get_db),
):
    doc = index_document(
        db,
        user,
        title=payload.title,
        content=payload.content,
        source=payload.source,
        classification=payload.classification,
    )
    audit(db, user, "document.ingested", "document", doc.id, {"title": doc.title})
    db.commit()
    db.refresh(doc)
    INGEST_COUNT.inc()
    return doc


@app.post("/api/documents/upload", response_model=list[DocumentOut], status_code=201)
async def upload_documents(
    files: list[UploadFile] = File(...),
    classification: str = Form(default="internal"),
    user: User = Depends(require_roles(Role.admin, Role.analyst)),
    db: Session = Depends(get_db),
):
    if not 1 <= len(files) <= 20:
        raise HTTPException(status_code=422, detail="Upload between 1 and 20 files")
    if classification not in {"public", "internal", "confidential"}:
        raise HTTPException(status_code=422, detail="Invalid classification")
    documents = []
    for file in files:
        content = await extract_upload(file)
        title = Path(file.filename or "Untitled").stem.replace("_", " ").strip()
        doc = index_document(
            db,
            user,
            title=title,
            content=content,
            source="upload",
            classification=classification,
        )
        documents.append(doc)
        audit(
            db,
            user,
            "document.uploaded",
            "document",
            doc.id,
            {"filename": file.filename},
        )
    db.commit()
    for doc in documents:
        db.refresh(doc)
        INGEST_COUNT.inc()
    return documents


@app.post("/api/demo/load-sample-data", response_model=list[DocumentOut], status_code=201)
def load_sample_data(
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    sample_dir = Path(__file__).parent / "sample_data"
    loaded = []
    for path in sorted(sample_dir.glob("*.md")):
        external_id = f"sample:{path.name}"
        existing = db.scalar(
            select(Document).where(
                Document.tenant_id == user.tenant_id,
                Document.external_id == external_id,
            )
        )
        if existing:
            loaded.append(existing)
            continue
        doc = index_document(
            db,
            user,
            title=path.stem.replace("_", " ").title(),
            content=path.read_text(encoding="utf-8"),
            source="sample_database",
            external_id=external_id,
        )
        loaded.append(doc)
    audit(
        db,
        user,
        "sample_data.loaded",
        "knowledge_base",
        None,
        {"documents": len(loaded)},
    )
    db.commit()
    for doc in loaded:
        db.refresh(doc)
    return loaded


@app.post("/api/query", response_model=QueryResponse)
def query(
    payload: QueryRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    started = perf_counter()
    hits = hybrid_search(
        db, user.tenant_id, payload.question, payload.top_k, role=user.role
    )
    result = grounded_answer(payload.question, hits)
    latency = round((perf_counter() - started) * 1000, 2)
    status = (
        "pending_approval"
        if result.confidence < settings.approval_confidence_threshold
        else "completed"
    )
    run = QueryRun(
        tenant_id=user.tenant_id,
        user_id=user.id,
        question=payload.question,
        answer=result.answer,
        citations=result.citations,
        confidence=result.confidence,
        status=status,
        latency_ms=latency,
        estimated_cost=result.estimated_cost,
    )
    db.add(run)
    db.flush()
    if status == "pending_approval":
        db.add(Approval(tenant_id=user.tenant_id, query_run_id=run.id))
    audit(
        db,
        user,
        "query.completed",
        "query_run",
        run.id,
        {"confidence": result.confidence, "status": status},
    )
    db.commit()
    QUERY_COUNT.labels(status=status).inc()
    QUERY_LATENCY.observe(latency / 1000)
    return QueryResponse(
        id=run.id,
        answer=run.answer,
        confidence=run.confidence,
        status=run.status,
        citations=run.citations,
        latency_ms=latency,
    )


@app.get("/api/approvals")
def approvals(
    user: User = Depends(require_roles(Role.admin, Role.analyst)),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Approval, QueryRun)
        .join(QueryRun, Approval.query_run_id == QueryRun.id)
        .where(Approval.tenant_id == user.tenant_id, Approval.status == "pending")
        .order_by(Approval.created_at.desc())
    ).all()
    return [
        {
            "id": approval.id,
            "question": run.question,
            "answer": run.answer,
            "confidence": run.confidence,
            "created_at": approval.created_at,
        }
        for approval, run in rows
    ]


@app.post("/api/approvals/{approval_id}/decision")
def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    approval = db.scalar(
        select(Approval).where(
            Approval.id == approval_id, Approval.tenant_id == user.tenant_id
        )
    )
    if not approval or approval.status != "pending":
        raise HTTPException(status_code=404, detail="Pending approval not found")
    approval.status = payload.decision
    approval.note = payload.note
    approval.reviewer_id = user.id
    approval.decided_at = datetime.now(UTC)
    run = db.get(QueryRun, approval.query_run_id)
    run.status = payload.decision
    audit(db, user, f"approval.{payload.decision}", "approval", approval.id)
    db.commit()
    APPROVAL_COUNT.labels(decision=payload.decision).inc()
    return {"status": payload.decision}


@app.post("/api/feedback", status_code=201)
def feedback(
    payload: FeedbackCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    run = db.scalar(
        select(QueryRun).where(
            QueryRun.id == payload.query_run_id, QueryRun.tenant_id == user.tenant_id
        )
    )
    if not run:
        raise HTTPException(status_code=404, detail="Query run not found")
    item = Feedback(
        query_run_id=run.id,
        user_id=user.id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(item)
    audit(db, user, "feedback.created", "query_run", run.id, {"rating": payload.rating})
    db.commit()
    return {"id": item.id}


@app.get("/api/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    runs = db.scalars(
        select(QueryRun)
        .where(QueryRun.tenant_id == user.tenant_id)
        .order_by(QueryRun.created_at.desc())
        .limit(100)
    ).all()
    doc_count = db.scalar(
        select(func.count(Document.id)).where(Document.tenant_id == user.tenant_id)
    )
    pending = db.scalar(
        select(func.count(Approval.id)).where(
            Approval.tenant_id == user.tenant_id, Approval.status == "pending"
        )
    )
    ratings = db.scalars(
        select(Feedback.rating)
        .join(QueryRun, Feedback.query_run_id == QueryRun.id)
        .where(QueryRun.tenant_id == user.tenant_id)
    ).all()
    return {
        "summary": {
            "documents": doc_count or 0,
            "queries": len(runs),
            "pending_approvals": pending or 0,
            "avg_confidence": round(mean([run.confidence for run in runs]), 2) if runs else 0,
            "avg_latency_ms": round(mean([run.latency_ms for run in runs]), 1) if runs else 0,
            "estimated_cost": round(sum(run.estimated_cost for run in runs), 4),
            "feedback_score": round(mean(ratings), 1) if ratings else None,
        },
        "recent_runs": [
            {
                "id": run.id,
                "question": run.question,
                "confidence": run.confidence,
                "status": run.status,
                "latency_ms": run.latency_ms,
                "created_at": run.created_at,
            }
            for run in runs[:8]
        ],
    }


@app.get("/api/audit")
def audit_log(
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == user.tenant_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(100)
    ).all()


@app.get("/api/connectors")
def list_connectors(
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    connections = db.scalars(
        select(SourceConnection)
        .where(SourceConnection.tenant_id == user.tenant_id)
        .order_by(SourceConnection.created_at.desc())
    ).all()
    return [
        {
            "id": item.id,
            "provider": item.provider,
            "name": item.name,
            "status": item.status,
            "last_synced_at": item.last_synced_at,
            "last_error": item.last_error,
            "webhook_url": f"/api/connectors/{item.id}/events",
        }
        for item in connections
    ]


@app.post("/api/connectors", status_code=201)
def create_connector(
    payload: ConnectionCreate,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    secret = new_webhook_secret()
    connection = SourceConnection(
        tenant_id=user.tenant_id,
        provider=payload.provider,
        name=payload.name,
        config=payload.config,
        webhook_secret_hash=hash_secret(secret),
        created_by=user.id,
    )
    db.add(connection)
    db.flush()
    audit(
        db,
        user,
        "connector.created",
        "source_connection",
        connection.id,
        {"provider": connection.provider},
    )
    db.commit()
    return {
        "id": connection.id,
        "provider": connection.provider,
        "webhook_url": f"/api/connectors/{connection.id}/events",
        "webhook_secret": secret,
        "warning": "Save this secret now. It is never returned again.",
    }


@app.post("/api/connectors/{connection_id}/events")
def receive_source_event(
    connection_id: str,
    payload: SourceEvent,
    x_connector_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    connection = db.get(SourceConnection, connection_id)
    if not connection or connection.status != "active":
        raise HTTPException(status_code=404, detail="Active connector not found")
    verify_webhook_secret(connection, x_connector_secret)
    system_user = db.scalar(
        select(User).where(
            User.tenant_id == connection.tenant_id, User.role == Role.admin
        )
    )
    outcome = process_event(db, connection, payload, system_user)
    return {"status": outcome}


@app.get("/api/connectors/{connection_id}/events")
def connector_events(
    connection_id: str,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    connection = db.scalar(
        select(SourceConnection).where(
            SourceConnection.id == connection_id,
            SourceConnection.tenant_id == user.tenant_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connector not found")
    return db.scalars(
        select(SyncEvent)
        .where(SyncEvent.connection_id == connection.id)
        .order_by(SyncEvent.created_at.desc())
        .limit(100)
    ).all()
