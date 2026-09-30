# -*- coding: utf-8 -*-
"""统一数据库入口：生产使用 PostgreSQL，本地验收可回退 SQLite。"""
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if os.environ.get("DB_HOST"):
        return URL.create(
            "postgresql+psycopg",
            username=os.environ.get("DB_USER", "legal_rag"),
            password=os.environ.get("DB_PASSWORD"),
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "legal_rag"),
        ).render_as_string(hide_password=False)
    # 兼容现有回归脚本；生产 Compose 总是显式设置 DATABASE_URL。
    path = Path(os.environ.get("SESSION_DB", PROJECT_ROOT / "data" / "sessions.db"))
    return f"sqlite:///{path.resolve().as_posix()}"


_engine = None
_engine_url = None
_factory = None


def get_engine():
    global _engine, _engine_url, _factory
    url = database_url()
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            kwargs.update({
                "pool_size": int(os.environ.get("DB_POOL_SIZE", "10")),
                "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "20")),
                "pool_recycle": 1800,
            })
        _engine = create_engine(url, **kwargs)
        _engine_url = url
        _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def ensure_schema() -> None:
    """SQLite 测试环境自动建表；生产 PostgreSQL 必须通过 Alembic 迁移。"""
    url = database_url()
    if url.startswith("sqlite") or os.environ.get("AUTO_CREATE_SCHEMA") == "1":
        from . import models  # noqa: F401 - 注册 metadata
        Base.metadata.create_all(get_engine())


@contextmanager
def session_scope():
    ensure_schema()
    get_engine()
    db = _factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
