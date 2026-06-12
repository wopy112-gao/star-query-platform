"""星宝语料场景查询系统 — 数据筛选导出路由

v2.1: 新增数据导出权限控制
- 允许导出用户：admin, 销售(ella/hubo/liumd/dongjl), amy, wim
- GET  /api/data/filter-options    — 获取筛选字段可选值列表
- POST /api/data/export/preview    — 预估导出行数+文件大小
- POST /api/data/export            — 筛选+导出 CSV/Parquet 文件
- GET  /api/data/export/records    — 下载历史记录
"""

import os
import uuid
import time
import gzip
import shutil
from datetime import datetime
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from auth import get_current_user
from sql_engine import engine
from schema_knowledge import SCHEMA_KNOWLEDGE
from download_store import add_record, get_records

# ---- 数据导出权限 ----
EXPORT_ALLOWED_USERS: set[str] = {
    "admin",
    "ella", "hubo", "liumd", "dongjl",  # 销售
    "amy", "wim",                        # 授权用户
}


def require_export_permission(username: str = Depends(get_current_user)) -> str:
    """依赖注入：校验当前用户是否有数据导出权限"""
    if username not in EXPORT_ALLOWED_USERS:
        raise HTTPException(
            status_code=403,
            detail=f"用户「{username}」无数据导出权限，请联系管理员",
        )
    return username

router = APIRouter(prefix="/api/data", tags=["数据导出"])


# ===== Pydantic 模型 =====

class DataExportParams(BaseModel):
    """数据导出筛选参数"""
    date_from: str = Field("", description="起始月份 YYYY-MM")
    date_to: str = Field("", description="结束月份 YYYY-MM")
    diseases: list[str] = Field(default_factory=list, description="疾病名称列表")
    products: list[str] = Field(default_factory=list, description="产品名称列表")
    provinces: list[str] = Field(default_factory=list, description="省份列表")
    chains: list[str] = Field(default_factory=list, description="连锁列表")
    cities: list[str] = Field(default_factory=list, description="城市列表")
    confidence_min: Optional[float] = Field(None, ge=0.0, le=1.0, description="最低综合置信度评分")
    is_commercial: Optional[int] = Field(None, description="是否商用: None=不限, 0=非商用, 1=商用")
    format: Literal["csv", "parquet", "csv_gz"] = Field("parquet", description="导出格式: csv(通用)/parquet(推荐,压缩)/csv_gz(压缩CSV)")


class ExportPreviewResponse(BaseModel):
    success: bool = True
    total_rows: int = 0
    estimated_size_mb: float = 0.0
    columns: int = 0
    sql_used: str = ""


# ===== 辅助函数 =====

def _build_where_clause(params: DataExportParams) -> str:
    """根据筛选参数构建 WHERE 子句

    将所有条件通过 AND 连接，条件为空时不添加。
    返回空字符串表示无筛选条件。
    """
    where_parts = []

    # 1. 月份范围
    if params.date_from:
        where_parts.append(f"CAST(ydate AS DATE) >= DATE '{params.date_from}-01'")
    if params.date_to:
        # 月份的最后一天
        where_parts.append(f"CAST(ydate AS DATE) <= DATE_TRUNC('month', DATE '{params.date_to}-01') + INTERVAL '1 month' - INTERVAL '1 day'")

    # 2. 疾病名称
    if params.diseases:
        escaped = [f"'{d.replace(chr(39), chr(39)+chr(39))}'" for d in params.diseases]
        where_parts.append(f"疾病名称 IN ({', '.join(escaped)})")

    # 3. 产品 — 匹配 顾客点名药品 / 场景提及药品 / 订单药品 / 店员推荐药品JSON / 店员提及药品JSON
    if params.products:
        product_conditions = []
        for prod in params.products:
            safe_prod = prod.replace("'", "''")
            product_conditions.append(
                f"(CONTAINS(顾客点名药品, '{safe_prod}') "
                f"OR CONTAINS(场景提及药品, '{safe_prod}') "
                f"OR CONTAINS(订单药品, '{safe_prod}') "
                f"OR CONTAINS(店员推荐药品JSON, '{safe_prod}') "
                f"OR CONTAINS(店员提及药品JSON, '{safe_prod}'))"
            )
        where_parts.append("(" + " OR ".join(product_conditions) + ")")

    # 4. 省份
    if params.provinces:
        escaped = [f"'{p.replace(chr(39), chr(39)+chr(39))}'" for p in params.provinces]
        where_parts.append(f"省份 IN ({', '.join(escaped)})")

    # 5. 连锁
    if params.chains:
        escaped = [f"'{c.replace(chr(39), chr(39)+chr(39))}'" for c in params.chains]
        where_parts.append(f"连锁 IN ({', '.join(escaped)})")

    # 6. 城市
    if params.cities:
        escaped = [f"'{c.replace(chr(39), chr(39)+chr(39))}'" for c in params.cities]
        where_parts.append(f"城市 IN ({', '.join(escaped)})")

    # 7. 置信度
    if params.confidence_min is not None and params.confidence_min > 0:
        where_parts.append(f"综合置信度评分 >= {params.confidence_min}")

    # 8. 是否商用
    if params.is_commercial is not None:
        where_parts.append(f"是否商用 = {params.is_commercial}")

    if not where_parts:
        return ""

    return "WHERE " + " AND ".join(where_parts)


def _build_full_sql(where_clause: str) -> str:
    """构建完整的 SELECT * SQL"""
    if where_clause:
        return f"SELECT * FROM data {where_clause}"
    return "SELECT * FROM data"


def _generate_filename(params: DataExportParams) -> str:
    """生成有意义的文件名"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = ["星宝数据"]

    # 添加月份范围
    if params.date_from or params.date_to:
        fr = params.date_from or "最早"
        to = params.date_to or "最新"
        parts.append(f"{fr}~{to}")

    # 添加首个疾病
    if params.diseases:
        short = params.diseases[0].split("-")[-1][:8]
        parts.append(short)

    # 添加首个省份
    if params.provinces:
        short = params.provinces[0][:4]
        parts.append(short)

    # 后缀
    ext_map = {"csv": ".csv", "parquet": ".parquet", "csv_gz": ".csv.gz"}
    ext = ext_map.get(params.format, ".csv")

    return "_".join(parts) + f"_{ts}{ext}"


# ===== 路由 =====

@router.get("/filter-options")
def get_filter_options(username: str = Depends(require_export_permission)):
    """获取数据导出筛选字段的可选值列表"""
    try:
        import duckdb
        conn: duckdb.DuckDBPyConnection = engine.conn

        def _distinct(field: str, limit: int = 5000) -> list:
            try:
                r = conn.execute(
                    f'SELECT DISTINCT "{field}" FROM data '
                    f'WHERE "{field}" IS NOT NULL AND "{field}" != \'\' '
                    f'ORDER BY "{field}" LIMIT {limit}'
                ).fetchall()
                return [row[0] for row in r]
            except Exception as e:
                print(f"[数据导出] DISTINCT {field} 失败: {e}")
                return []

        def _single(sql: str):
            try:
                return conn.execute(sql).fetchone()[0]
            except Exception as e:
                print(f"[数据导出] 单值查询失败: {e}")
                return None

        # 同步执行所有独立查询
        provinces = _distinct("省份")
        chains = _distinct("连锁")
        diseases = _distinct("疾病名称")
        cities = _distinct("城市")

        min_date = _single(
            "SELECT MIN(CAST(ydate AS DATE)) FROM data "
            "WHERE ydate IS NOT NULL "
            "AND CAST(ydate AS DATE) BETWEEN '2020-01-01' AND '2027-12-31'"
        )
        max_date = _single(
            "SELECT MAX(CAST(ydate AS DATE)) FROM data "
            "WHERE ydate IS NOT NULL "
            "AND CAST(ydate AS DATE) BETWEEN '2020-01-01' AND '2027-12-31'"
        )
        min_conf = _single(
            "SELECT MIN(综合置信度评分) FROM data WHERE 综合置信度评分 IS NOT NULL"
        )
        max_conf = _single(
            "SELECT MAX(综合置信度评分) FROM data WHERE 综合置信度评分 IS NOT NULL"
        )
        total_rows = _single("SELECT COUNT(*) FROM data") or 0
        columns = [col["name"] for col in SCHEMA_KNOWLEDGE["columns"]]

        return {
            "success": True,
            "data": {
                "provinces": provinces,
                "chains": chains,
                "diseases": diseases,
                "cities": cities,
                "date_range": {
                    "min_date": str(min_date)[:7] if min_date else "",
                    "max_date": str(max_date)[:7] if max_date else "",
                },
                "confidence_range": {
                    "min": float(min_conf) if min_conf is not None else 0.0,
                    "max": float(max_conf) if max_conf is not None else 1.0,
                },
                "is_commercial_options": [
                    {"value": None, "label": "不限"},
                    {"value": 1, "label": "仅商用数据"},
                    {"value": 0, "label": "非商用数据"},
                ],
                "total_rows": total_rows,
                "total_columns": len(columns),
                "columns": columns,
            },
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"获取筛选选项失败: {str(e)}"},
        )


@router.post("/export/preview")
def export_preview(req: DataExportParams, username: str = Depends(require_export_permission)):
    """预估筛选后的数据量和文件大小

    返回符合条件的行数、预估 CSV 大小和使用的 SQL。
    此操作不会实际生成文件。
    """
    try:
        start = time.time()
        where = _build_where_clause(req)

        # 构建 COUNT 查询
        if where:
            count_sql = f"SELECT COUNT(*) AS _cnt FROM data {where}"
        else:
            count_sql = "SELECT COUNT(*) AS _cnt FROM data"

        # 使用 engine.execute 执行（安全的 SELECT 查询）
        count_res = engine.execute(count_sql)
        if not count_res["success"]:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": f"预估查询失败: {count_res['error']}"},
            )

        total_rows = count_res["rows"][0]["_cnt"] if count_res["rows"] else 0
        elapsed = (time.time() - start) * 1000

        # 估算 CSV 大小（假设每行约 1KB，从样本实测）
        estimated_bytes = total_rows * 1024  # ~1KB/行

        # 格式不同的压缩比估算
        format_ratio = {"csv": 1.0, "csv_gz": 0.17, "parquet": 0.17}
        ratio = format_ratio.get(req.format, 1.0)
        estimated_final_bytes = int(estimated_bytes * ratio)

        estimated_csv_mb = round(estimated_bytes / 1024 / 1024, 1)
        estimated_final_mb = round(estimated_final_bytes / 1024 / 1024, 1)

        # 各格式预估
        formats_estimate = {
            "csv": {
                "size_bytes": estimated_bytes,
                "size_label": _format_size(estimated_bytes),
            },
            "parquet": {
                "size_bytes": estimated_final_bytes,
                "size_label": _format_size(estimated_final_bytes),
            },
            "csv_gz": {
                "size_bytes": estimated_final_bytes,
                "size_label": _format_size(estimated_final_bytes),
            },
        }

        full_sql = _build_full_sql(where)

        return {
            "success": True,
            "total_rows": total_rows,
            "estimated_size_mb": estimated_final_mb,
            "estimated_size_label": _format_size(estimated_final_bytes),
            "estimated_size_csv": _format_size(estimated_bytes),
            "columns": len(SCHEMA_KNOWLEDGE["columns"]),
            "formats": formats_estimate,
            "format_default": req.format,
            "elapsed_ms": round(elapsed, 2),
            "sql_used": full_sql,
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"预估查询异常: {str(e)}"},
        )


@router.post("/export")
def export_data(req: DataExportParams, username: str = Depends(require_export_permission)):
    """根据筛选条件导出 CSV 文件（全字段，不限行数）"""
    start = time.time()
    try:
        import duckdb
        conn: duckdb.DuckDBPyConnection = engine.conn

        where = _build_where_clause(req)

        # 构建完整 SELECT * SQL
        full_sql = _build_full_sql(where)

        # ---- 先跑 COUNT（用于记录和预估反馈） ----
        # where 已包含 "WHERE " 前缀，直接拼接即可
        if where:
            count_sql = "SELECT COUNT(*) AS _cnt FROM data " + where
        else:
            count_sql = "SELECT COUNT(*) AS _cnt FROM data"
        count_res = conn.execute(count_sql).fetchone()
        total_rows = count_res[0] if count_res else 0

        if total_rows == 0:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "筛选条件无匹配数据，请调整条件后重试"},
            )

        # ---- DuckDB COPY → 文件（支持多种格式） ----
        out_path = f"/tmp/star_export_{uuid.uuid4().hex}"

        try:
            if req.format == "parquet":
                # Parquet — DuckDB 原生列式存储+ZSTD压缩
                out_path_parquet = out_path + ".parquet"
                copy_sql = (
                    f"COPY ({full_sql}) TO '{out_path_parquet}' "
                    f"(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
                conn.execute(copy_sql)
                final_path = out_path_parquet
                media_type = "application/octet-stream"

            elif req.format == "csv_gz":
                # CSV → GZIP 压缩
                out_path_csv = out_path + ".csv"
                copy_sql = f"COPY ({full_sql}) TO '{out_path_csv}' (HEADER, DELIMITER ',')"
                conn.execute(copy_sql)
                # 追加 BOM
                with open(out_path_csv, 'rb') as f:
                    raw = f.read()
                with open(out_path_csv, 'wb') as f:
                    f.write(b'\xef\xbb\xbf')
                    f.write(raw)
                # GZIP 压缩
                out_path_gz = out_path + ".csv.gz"
                import gzip, shutil
                with open(out_path_csv, 'rb') as f_in:
                    with gzip.open(out_path_gz, 'wb', compresslevel=6) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.unlink(out_path_csv)
                final_path = out_path_gz
                media_type = "application/gzip"

            else:
                # CSV（默认，通用格式）
                out_path_csv = out_path + ".csv"
                copy_sql = f"COPY ({full_sql}) TO '{out_path_csv}' (HEADER, DELIMITER ',')"
                conn.execute(copy_sql)
                # 追加 BOM
                with open(out_path_csv, 'rb') as f:
                    raw = f.read()
                with open(out_path_csv, 'wb') as f:
                    f.write(b'\xef\xbb\xbf')
                    f.write(raw)
                final_path = out_path_csv
                media_type = "text/csv"

        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"导出失败: {str(e)}"},
            )

        file_size = os.path.getsize(final_path)
        elapsed = (time.time() - start) * 1000

        # ---- 生成文件名 ----
        file_name = _generate_filename(req)

        # ---- 记录下载历史 ----
        try:
            add_record(
                username=username,
                filters=req.model_dump(),
                row_count=total_rows,
                file_size_bytes=file_size,
                file_name=file_name,
                elapsed_ms=round(elapsed, 2),
            )
        except Exception as e:
            print(f"[数据导出] 下载记录写入失败: {e}")

        return FileResponse(
            final_path,
            media_type=media_type,
            filename=file_name,
            headers={
                "X-Total-Rows": str(total_rows),
                "X-File-Size": str(file_size),
                "X-Elapsed-Ms": str(round(elapsed, 2)),
                "X-Format": req.format,
            },
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"数据导出失败: {str(e)}"},
        )


@router.get("/export/records")
def get_download_records(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    username: str = Depends(require_export_permission),
):
    """获取当前用户的下载历史记录"""
    return get_records(username=username, page=page, limit=limit)


def _format_size(bytes_val: int) -> str:
    """友好显示文件大小"""
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / 1024 / 1024 / 1024:.1f} GB"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val / 1024 / 1024:.1f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f} KB"
    return f"{bytes_val} B"
