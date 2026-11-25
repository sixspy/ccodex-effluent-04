"""指标血缘边。

effluent-04 口径（甲路径）：血缘主表/主页面只允许正式层
（layer='official' AND promoted=1）。未升正的影子草稿边、事故类型
「封后影子修订」一律过滤，防止影子修订未升正却混进血缘页。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from effluent04_seed import SHADOW_EDGE_TYPE
from effluent_errors import ApiConflict
from models import LineageIn

router = APIRouter(tags=["lineage"])

# 正式血缘读模型：新列默认值 + 事故边类型兜底（老库 ALTER 后影子边会被默认成 official）
_OFFICIAL_WHERE = """
  COALESCE(e.layer, 'official') = 'official'
  AND COALESCE(e.promoted, 1) = 1
  AND COALESCE(e.edge_type, '') != ?
"""


@router.get("/api/lineage")
def list_lineage(authorization: AuthHeader = None, include_shadow: bool = False):
    require_user(authorization)
    with connect() as conn:
        if include_shadow:
            rows = conn.execute(
                """SELECT e.*, f.sample_code from_code, t.sample_code to_code
                   FROM metric_lineage e
                   JOIN sample_batches f ON f.id=e.from_sample_id
                   LEFT JOIN sample_batches t ON t.id=e.to_sample_id
                   ORDER BY e.id DESC"""
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT e.*, f.sample_code from_code, t.sample_code to_code
                   FROM metric_lineage e
                   JOIN sample_batches f ON f.id=e.from_sample_id
                   LEFT JOIN sample_batches t ON t.id=e.to_sample_id
                   WHERE {_OFFICIAL_WHERE}
                   ORDER BY e.id DESC""",
                (SHADOW_EDGE_TYPE,),
            ).fetchall()
        return {"items": [dict(r) for r in rows], "shadow_mixed": False}


@router.get("/api/metric-drafts")
def list_metric_drafts(authorization: AuthHeader = None, sample_code: str | None = None):
    """影子指标草稿隔离台账（effluent-04 事故取证，甲路径下无升正/回写通道）。"""
    require_user(authorization)
    with connect() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='metric_shadow_drafts'"
        ).fetchone()
        if not exists:
            return {"items": []}
        if sample_code:
            rows = conn.execute(
                """SELECT d.*, s.sample_code FROM metric_shadow_drafts d
                   JOIN sample_batches s ON s.id=d.sample_id
                   WHERE s.sample_code=? ORDER BY d.id DESC""",
                (sample_code,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT d.*, s.sample_code FROM metric_shadow_drafts d
                   JOIN sample_batches s ON s.id=d.sample_id
                   ORDER BY d.id DESC"""
            ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["promoted"] = 1 if d.get("status") == "已升正" else 0
            items.append(d)
        return {"items": items, "isolated": True, "promotion_channel": "disabled_jia_path"}


@router.post("/api/lineage")
def create_edge(body: LineageIn, authorization: AuthHeader = None):
    user = require_writer(authorization)

    # 甲路径封死：任何把封后影子修订洗白进正式血缘的写法一律拒绝
    if body.layer == "shadow" or body.edge_type == SHADOW_EDGE_TYPE:
        raise ApiConflict(
            "shadow_lineage_forbidden",
            "封后禁改偏航：未升正影子修订不得写入正式血缘；甲路径不提供影子升正通道",
        )

    with connect() as conn:
        fr = conn.execute(
            "SELECT * FROM sample_batches WHERE id=?", (body.from_sample_id,)
        ).fetchone()
        if not fr:
            raise HTTPException(404, "源采样不存在")
        if body.to_sample_id:
            to = conn.execute(
                "SELECT id FROM sample_batches WHERE id=?", (body.to_sample_id,)
            ).fetchone()
            if not to:
                raise HTTPException(404, "目标采样不存在")
        cur = conn.execute(
            """INSERT INTO metric_lineage(from_sample_id,to_sample_id,edge_type,detail,
                   layer,draft_code,promoted,created_at)
               VALUES (?,?,?,?, 'official', NULL, 1, ?)""",
            (
                body.from_sample_id,
                body.to_sample_id,
                body.edge_type,
                body.detail,
                now_iso(),
            ),
        )
        write_audit(
            conn,
            user,
            "lineage_edge",
            fr["sample_code"],
            body.model_dump(),
        )
        conn.commit()
        return {"id": cur.lastrowid, **body.model_dump(), "layer": "official", "promoted": 1}
