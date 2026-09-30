# -*- coding: utf-8 -*-
"""Argon2 + JWT 认证，用户数据统一由 SQLAlchemy 管理。"""
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import argon2
import jwt
from sqlalchemy import select

from .database import session_scope
from .models import User

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_hasher = argon2.PasswordHasher()
_SECRET_FILE = PROJECT_ROOT / "data" / ".jwt_secret"


def _get_secret() -> bytes:
    env = os.environ.get("JWT_SECRET")
    if env:
        return env.encode()
    if os.environ.get("APP_ENV") == "production":
        raise RuntimeError("生产环境必须配置 JWT_SECRET")
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    key = secrets.token_bytes(32)
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SECRET_FILE.write_bytes(key)
    return key


def _expire_seconds() -> int:
    return int(os.environ.get("JWT_EXPIRE_SECONDS", str(7 * 24 * 3600)))


def _validate_credentials(username: str, password: str) -> None:
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    if len(username) < 2 or len(username) > 32:
        raise ValueError("用户名长度需在 2~32 字符之间")
    if len(password) < 8:
        raise ValueError("密码长度至少 8 位")


def register_user(username: str, password: str) -> dict:
    username = username.strip()
    _validate_credentials(username, password)
    with session_scope() as db:
        if db.scalar(select(User.id).where(User.username == username)):
            raise ValueError("用户名已被占用")
        user = User(username=username, password_hash=_hasher.hash(password), created_at=time.time())
        db.add(user)
        db.flush()
        return {"id": user.id, "username": user.username}


def authenticate(username: str, password: str) -> dict | None:
    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == username.strip()))
        if user is None:
            return None
        try:
            valid = _hasher.verify(user.password_hash, password)
        except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.InvalidHashError):
            valid = False
        if not valid:
            return None
        if _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = _hasher.hash(password)
        return {"id": user.id, "username": user.username}


def seed_user(username: str, password: str) -> dict:
    username = username.strip()
    _validate_credentials(username, password)
    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, password_hash=_hasher.hash(password), created_at=time.time())
            db.add(user)
            db.flush()
        return {"id": user.id, "username": user.username}


def create_token(user: dict) -> dict:
    now = datetime.now(timezone.utc)
    exp_sec = _expire_seconds()
    payload = {
        "sub": str(user["id"]), "username": user["username"], "iat": now,
        "exp": now + timedelta(seconds=exp_sec),
    }
    return {"token": jwt.encode(payload, _get_secret(), algorithm="HS256"), "expires_in": exp_sec}


def verify_token(token: str) -> dict:
    payload = jwt.decode(token, _get_secret(), algorithms=["HS256"])
    user_id = int(payload["sub"])
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise jwt.InvalidTokenError("用户不存在")
        return {"id": user.id, "username": user.username}
