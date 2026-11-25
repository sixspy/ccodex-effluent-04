"""排放批争用锁。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from effluent_errors import ApiConflict

router = APIRouter(tags=["locks"])


def _write_contention(conn, lock_key: str, holder: str, owner: str | None) -> None:
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (
            lock_key,
            holder,
            "争用失败",
            f"持有人={owner}" if owner else "并发抢锁失败（唯一键仲裁）",
            now_iso(),
        ),
    )


def require_lock(conn, lock_key: str, holder: str) -> bool:
    """抢批锁。

    返回 True 表示本次调用新插入锁（调用方负责 release）；
    返回 False 表示锁本就由同一 holder 持有（可重入，调用方不得替外层删锁）；
    被其他 holder 持有或并发抢锁落败时，落争用痕并抛 409 lock_busy。
    """
    row = conn.execute(
        "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row:
        if row["holder"] != holder:
            _write_contention(conn, lock_key, holder, row["holder"])
            conn.commit()
            raise ApiConflict(
                "lock_busy",
                f"锁被占用：{lock_key} 持有人 {row['holder']}；后到会话仅留争用痕，指标零写入",
            )
        return False

    try:
        conn.execute(
            "INSERT INTO effluent_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
            (lock_key, holder, now_iso()),
        )
        return True
    except sqlite3.IntegrityError:
        # 两路并发同时 INSERT：主键仲裁，输家转 409 并留争用痕（不再冒 500）
        row = conn.execute(
            "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
        ).fetchone()
        owner = row["holder"] if row else None
        if owner == holder:
            return False
        _write_contention(conn, lock_key, holder, owner)
        conn.commit()
        raise ApiConflict(
            "lock_busy",
            f"并发抢批锁落败：{lock_key} 持有人 {owner}；后到仅留争用痕，指标零写入",
        )


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM effluent_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        locks = [dict(r) for r in conn.execute("SELECT * FROM effluent_locks").fetchall()]
        logs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contention_log ORDER BY id DESC LIMIT 50"
            ).fetchall()
        ]
        return {"locks": locks, "contention": logs}


@router.post("/api/locks/acquire")
def acquire(lock_key: str, authorization: AuthHeader = None, session_id: str | None = None):
    user = require_writer(authorization)
    holder = f"{user}#{session_id}" if session_id else user
    with connect() as conn:
        require_lock(conn, lock_key, holder)
        write_audit(conn, user, "lock_acquire", lock_key, {"holder": holder})
        conn.commit()
        return {"ok": True, "lock_key": lock_key, "holder": holder}


@router.post("/api/locks/release")
def release_api(lock_key: str, authorization: AuthHeader = None, session_id: str | None = None):
    user = require_writer(authorization)
    holder = f"{user}#{session_id}" if session_id else user
    with connect() as conn:
        release(conn, lock_key, holder)
        write_audit(conn, user, "lock_release", lock_key, {"holder": holder})
        conn.commit()
        return {"ok": True}
