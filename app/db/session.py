from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.paths import ensure_dir


def _make_engine():
    settings = get_settings()
    ensure_dir(settings.db_path.parent)
    engine = create_engine(f"sqlite:///{settings.db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


_engine: Engine | None = None
_engine_path: str | None = None
SessionLocal = sessionmaker(expire_on_commit=False, future=True)


def get_engine() -> Engine:
    global _engine, _engine_path
    settings = get_settings()
    engine_path = str(settings.db_path)
    if _engine is None or _engine_path != engine_path:
        _engine = _make_engine()
        _engine_path = engine_path
        SessionLocal.configure(bind=_engine)
    return _engine


def init_db() -> None:
    from app.db.migrations import upgrade_to_head

    upgrade_to_head()


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
