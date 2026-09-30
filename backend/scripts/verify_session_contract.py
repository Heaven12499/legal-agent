# -*- coding: utf-8 -*-
"""会话附件回归验收：普通追问不应清空合同，显式移除才清空。"""
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

with tempfile.TemporaryDirectory() as tmp:
    os.environ["SESSION_DB"] = str(Path(tmp) / "sessions.db")
    from backend.app.infra import session

    uid, sid = 1, "contract-regression"
    session.save_contract(uid, sid, "第一条 合同文本", "采购合同.docx")
    assert session.get_contract(uid, sid) == "第一条 合同文本"
    assert session.get_contract_meta(uid, sid) == {"name": "采购合同.docx", "chars": 8}

    # 模拟普通追问：主接口省略 contract 字段，因此不会调用 save_contract。
    session.append(uid, sid, "user", "请继续核查违约金条款")
    assert session.get_contract(uid, sid) == "第一条 合同文本"

    # 模拟用户点击移除附件：显式 null 才清除合同和展示元数据。
    session.save_contract(uid, sid, None)
    assert session.get_contract(uid, sid) == ""
    assert session.get_contract_meta(uid, sid) is None

    from backend.app.infra.database import get_engine
    get_engine().dispose()

print("[OK] 合同附件：普通追问保留，显式移除才清空")
