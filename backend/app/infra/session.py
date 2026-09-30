# -*- coding: utf-8 -*-
"""会话、合同与消息仓储；全部读写按 user_id 隔离。"""
import time

from sqlalchemy import delete, func, select

from .database import session_scope
from .models import ChatSession, Message


def _owned(db, user_id: int, session_id: str) -> ChatSession | None:
    return db.scalar(select(ChatSession).where(
        ChatSession.session_id == session_id, ChatSession.user_id == user_id
    ))


def _writable(db, user_id: int, session_id: str) -> ChatSession | None:
    item = db.get(ChatSession, session_id)
    if item is not None and item.user_id not in (None, user_id):
        raise ValueError("无权操作该会话")
    return item


def migrate_anonymous(user_id: int) -> int:
    with session_scope() as db:
        rows = db.scalars(select(ChatSession).where(ChatSession.user_id.is_(None))).all()
        for row in rows:
            row.user_id = user_id
        return len(rows)


def save_contract(user_id: int, session_id: str, contract: str | None,
                  contract_name: str | None = None) -> None:
    now = time.time()
    with session_scope() as db:
        item = _writable(db, user_id, session_id)
        if item is None:
            item = ChatSession(
                session_id=session_id, user_id=user_id, created_at=now, updated_at=now
            )
            db.add(item)
        item.contract = contract
        item.contract_name = contract_name
        item.updated_at = now


def get_contract(user_id: int, session_id: str) -> str:
    with session_scope() as db:
        item = _owned(db, user_id, session_id)
        return (item.contract or "") if item else ""


def get_contract_meta(user_id: int, session_id: str) -> dict | None:
    with session_scope() as db:
        item = _owned(db, user_id, session_id)
        if not item or not item.contract:
            return None
        return {"name": item.contract_name or "已附加合同", "chars": len(item.contract)}


def get_history(user_id: int, session_id: str) -> list[dict]:
    with session_scope() as db:
        if _owned(db, user_id, session_id) is None:
            return []
        rows = db.scalars(
            select(Message).where(Message.session_id == session_id).order_by(Message.id)
        ).all()
        out = []
        for row in rows:
            item = {"id": row.id, "role": row.role, "content": row.content}
            if row.citation:
                item["citation_check"] = row.citation
            if row.trace:
                item["trace"] = row.trace
            out.append(item)
        return out


def append(user_id: int, session_id: str, role: str, content: str,
           citation_check: dict | None = None, trace: list | None = None,
           review_job_id: str | None = None) -> int:
    now = time.time()
    with session_scope() as db:
        item = _writable(db, user_id, session_id)
        if item is None:
            item = ChatSession(
                session_id=session_id, user_id=user_id, created_at=now, updated_at=now
            )
            db.add(item)
            db.flush()
        item.updated_at = now
        message = Message(
            session_id=session_id, role=role, content=content,
            citation=citation_check, trace=trace, review_job_id=review_job_id,
        )
        db.add(message)
        db.flush()
        return message.id


def delete_from(user_id: int, session_id: str, from_id: int) -> None:
    with session_scope() as db:
        if _owned(db, user_id, session_id):
            db.execute(delete(Message).where(
                Message.session_id == session_id, Message.id >= from_id
            ))


def delete_after(user_id: int, session_id: str, after_id: int) -> None:
    with session_scope() as db:
        if _owned(db, user_id, session_id):
            db.execute(delete(Message).where(
                Message.session_id == session_id, Message.id > after_id
            ))


def clear(user_id: int, session_id: str) -> None:
    with session_scope() as db:
        item = _owned(db, user_id, session_id)
        if item:
            db.execute(delete(Message).where(Message.session_id == session_id))
            db.delete(item)


def list_sessions(user_id: int) -> list[dict]:
    with session_scope() as db:
        first_message = select(Message.content).where(
            Message.session_id == ChatSession.session_id
        ).order_by(Message.id).limit(1).correlate(ChatSession).scalar_subquery()
        message_count = select(func.count(Message.id)).where(
            Message.session_id == ChatSession.session_id
        ).correlate(ChatSession).scalar_subquery()
        rows = db.execute(select(
            ChatSession, first_message.label("first_message"),
            message_count.label("message_count"),
        ).where(ChatSession.user_id == user_id).order_by(ChatSession.updated_at.desc())).all()
        return [
            {
                "session_id": item.session_id, "first_message": first or "",
                "message_count": count or 0, "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item, first, count in rows
        ]
