"""审计轨迹只读。"""
from __future__ import annotations

from fastapi import APIRouter

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["audit"])


@router.get("/api/audit")
def list_audit(authorization: AuthHeader = None, limit: int = 100):
    require_user(authorization)
    limit = max(1, min(limit, 500))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_trail ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/deviations")
def list_deviations(authorization: AuthHeader = None, limit: int = 100):
    """effluent-04 偏离说明台账：封后禁改偏航拒写记录。"""
    require_user(authorization)
    limit = max(1, min(limit, 500))
    with connect() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='deviation_log'"
        ).fetchone()
        if not exists:
            return {"items": []}
        rows = conn.execute(
            "SELECT * FROM deviation_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"items": [dict(r) for r in rows]}
