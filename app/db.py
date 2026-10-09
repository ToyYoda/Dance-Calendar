from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(conn, _record):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return eng


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def configure(url: str) -> None:
    """Rebind the session factory to another database (used by tests)."""
    global engine
    engine = make_engine(url)
    SessionLocal.configure(bind=engine)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    if settings.database_url.startswith("sqlite:///"):
        from pathlib import Path

        Path(str(engine.url.database)).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
