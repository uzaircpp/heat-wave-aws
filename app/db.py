"""Database models. SQLite by default, Postgres when DATABASE_URL points at one."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Report(Base):
    """A citizen report: waterlogging, blocked drain, heat spot, missing shade or water."""

    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # waterlogging|drain|heat|shade_water|other
    depth: Mapped[str | None] = mapped_column(String(16))  # ankle|knee|waist|above_waist
    description: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    ward_id: Mapped[int | None] = mapped_column(Integer, index=True)
    ward_name: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(255))
    photo_path: Mapped[str | None] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(8), default="warn")  # crit|warn|info
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new|assigned|in_progress|resolved|rejected
    assignee: Mapped[str | None] = mapped_column(String(64))
    reporter_hash: Mapped[str | None] = mapped_column(String(64))  # salted hash of IP, for rate limiting only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    photo_check: Mapped[dict | None] = mapped_column(JSON)  # Amazon Rekognition labels for the photo
    photo_verified: Mapped[bool | None] = mapped_column(Boolean)  # True when the photo shows water
    events: Mapped[list["ReportEvent"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class ReportEvent(Base):
    """Audit trail for every status change, so fix times are measured, not typed in."""

    __tablename__ = "report_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    report: Mapped[Report] = relationship(back_populates="events")


class Broadcast(Base):
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    ward_id: Mapped[int | None] = mapped_column(Integer)
    audiences: Mapped[list] = mapped_column(JSON, default=list)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Subscriber(Base):
    """People who opted in to alerts, by phone (SMS / WhatsApp). Phone numbers stay on your server."""

    __tablename__ = "subscribers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    ward_id: Mapped[int | None] = mapped_column(Integer, index=True)
    audiences: Mapped[list] = mapped_column(JSON, default=list)  # residents|commuters|outdoor_workers
    whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertLog(Base):
    """Every alert we have shown, from official feeds, forecast rules or report clusters."""

    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    source: Mapped[str] = mapped_column(String(24))  # sachet|forecast|reports
    kind: Mapped[str] = mapped_column(String(24))  # heat|rain|flood|other
    severity: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    area: Mapped[str] = mapped_column(String(255), default="")
    ward_id: Mapped[int | None] = mapped_column(Integer)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class PushSub(Base):
    """A browser or phone that turned on push alerts (Web Push). Holds no name or number: just the push
    endpoint from the browser, the ward or rough position to watch, and the type of work."""

    __tablename__ = "push_subs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(64))
    ward_id: Mapped[int | None] = mapped_column(Integer, index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    role: Mapped[str] = mapped_column(String(16), default="rider")
    lang: Mapped[str] = mapped_column(String(4), default="en")
    state: Mapped[dict] = mapped_column(JSON, default=dict)   # last sent per kind, for throttling
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    fails: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """Small server-wide values that must survive restarts, such as the Web Push (VAPID) key pair."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):  # pragma: no cover - trivial
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Tiny migration: add columns introduced after a database was first created."""
    from sqlalchemy import inspect, text

    have = {c["name"] for c in inspect(engine).get_columns("reports")}
    adds = {"photo_check": "JSON", "photo_verified": "BOOLEAN"}
    with engine.begin() as conn:
        for name, typ in adds.items():
            if name not in have:
                conn.execute(text(f"ALTER TABLE reports ADD COLUMN {name} {typ}"))


def get_session():
    with SessionLocal() as session:
        yield session
