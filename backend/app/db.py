"""Database layer.

All reference data (contacts, tickets, knowledge) now lives in the database
instead of being hardcoded Python lists, so the agent operates on *dynamic*
data that can be read and extended at runtime.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = 'tasks'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    assignee: Mapped[str] = mapped_column(String(200), default='Unassigned')
    assignee_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_date: Mapped[str] = mapped_column(String(200), default='Not specified')
    priority: Mapped[str] = mapped_column(String(20), default='normal')
    status: Mapped[str] = mapped_column(String(50), default='open', index=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_status: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Contact(Base):
    __tablename__ = 'contacts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    team: Mapped[str] = mapped_column(String(120), default='General')
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    # Phone in E.164 (e.g. +919876543210) — used by the WhatsApp/SMS channels.
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Ticket(Base):
    __tablename__ = 'tickets'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(20), default='low', index=True)
    status: Mapped[str] = mapped_column(String(20), default='open', index=True)
    owner: Mapped[str] = mapped_column(String(120), default='Unassigned')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class KnowledgeDoc(Base):
    __tablename__ = 'knowledge_docs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GeneratedDocument(Base):
    """A document the agent authored (leave letter, memo, ...) and tried to deliver."""
    __tablename__ = 'generated_documents'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    doc_type: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    recipient: Mapped[str] = mapped_column(String(200), default='')
    body: Mapped[str] = mapped_column(Text)
    channels: Mapped[str] = mapped_column(String(200), default='')
    delivery: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Execution(Base):
    __tablename__ = 'executions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    command: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), index=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    event: Mapped[str] = mapped_column(String(100), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# SQLite needs a special flag; Postgres/other use pooling options.
_url = settings().database_url
_engine_kwargs: dict = {'pool_pre_ping': True}
if _url.startswith('sqlite'):
    _engine_kwargs = {'connect_args': {'check_same_thread': False}}

engine = create_engine(_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables if they do not exist (idempotent), then run light migrations."""
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Best-effort add of columns introduced after a DB was first created.

    `create_all` adds missing *tables* but not missing *columns*, so an existing
    `contacts` table would lack `phone`. This adds it without a migration tool.
    """
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(engine)
        if 'contacts' in inspector.get_table_names():
            cols = {c['name'] for c in inspector.get_columns('contacts')}
            if 'phone' not in cols:
                with engine.begin() as conn:
                    conn.execute(text('ALTER TABLE contacts ADD COLUMN phone VARCHAR(40)'))
    except Exception:  # noqa: BLE001 - never block startup on a migration probe
        pass


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def audit(event: str, details: dict, execution_id: str | None = None) -> None:
    with session_scope() as db:
        db.add(AuditLog(execution_id=execution_id, event=event, details=details))


def seed_reference_data() -> None:
    """Seed contacts, tickets and knowledge if the tables are empty.

    Data is *seeded* into the DB (not hardcoded at the call site), so it can be
    read, searched and extended dynamically through the API and MCP tools.
    """
    from .seed_data import SEED_CONTACTS, SEED_KNOWLEDGE, SEED_TICKETS

    with session_scope() as db:
        if not db.scalar(select(Contact.id).limit(1)):
            db.add_all(Contact(**c) for c in SEED_CONTACTS)
        if not db.scalar(select(Ticket.id).limit(1)):
            db.add_all(Ticket(**t) for t in SEED_TICKETS)
        if not db.scalar(select(KnowledgeDoc.id).limit(1)):
            db.add_all(KnowledgeDoc(**k) for k in SEED_KNOWLEDGE)
