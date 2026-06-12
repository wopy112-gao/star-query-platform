"""星宝语料场景查询系统 — 数据洞察模块

自动汇总核心指标，提供全景概览。
所有查询复用 DuckDB 引擎，保证数据口径一致。
"""

import time
import threading
from datetime import datetime, timedelta

from sql_engine import engine


class InsightCache:
    """洞察结果缓存（数据文件不频繁变动，适当缓存避免重复计算）"""
    _data: dict | None = None
    _timestamp: float = 0
    _ttl: float = 60.0  # 缓存有效期 60 秒
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> dict:
        """获取洞察结果（缓存命中直接返回）"""
        now = time.time()
        if cls._data is not None and (now - cls._timestamp) < cls._ttl:
            return cls._data

        with cls._lock:
            # 双重检查
            if cls._data is not None and (now - cls._timestamp) < cls._ttl:
                return cls._data
            cls._data = _compute_insights()
            cls._timestamp = time.time()
            return cls._data

    @classmethod
    def invalidate(cls):
        """主动刷新缓存"""
        with cls._lock:
            cls._data = None
            cls._timestamp = 0


def _today_str() -> str:
    """获取今日日期字符串 YYYY-MM-DD"""
    return datetime.now().strftime("%Y-%m-%d")


def _this_week_range() -> tuple[str, str]:
    """获取本周一的日期和今天"""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _last_7_days() -> list[str]:
    """获取最近 7 天日期列表（含今天）"""
    today = datetime.now()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]


def _execute_safe(sql: str) -> list[dict]:
    """执行 SQL 并返回行列表，失败返回空列表"""
    result = engine.execute(sql)
    if result["success"]:
        return result["rows"]
    return []


def _compute_insights() -> dict:
    """计算核心洞察指标"""
    today = _today_str()
    monday, _ = _this_week_range()
    last7 = _last_7_days()
    today_start = f"{today} 00:00:00"

    # ===== 1. 基础场景数统计 =====
    total_scenes = _execute_safe(
        "SELECT COUNT(DISTINCT 场景ID) AS cnt FROM data"
    )
    total_scenes_cnt = total_scenes[0]["cnt"] if total_scenes else 0

    today_scenes = _execute_safe(
        f"SELECT COUNT(DISTINCT 场景ID) AS cnt FROM data WHERE ydate = '{today}'"
    )
    today_scenes_cnt = today_scenes[0]["cnt"] if today_scenes else 0

    week_scenes = _execute_safe(
        f"SELECT COUNT(DISTINCT 场景ID) AS cnt FROM data WHERE ydate >= '{monday}'"
    )
    week_scenes_cnt = week_scenes[0]["cnt"] if week_scenes else 0

    # ===== 2. 成交率 =====
    close_rate_res = _execute_safe(
        """SELECT
            COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 1.0
            / COUNT(DISTINCT 场景ID) * 100.0 AS rate
        FROM data"""
    )
    close_rate = round(close_rate_res[0]["rate"], 1) if close_rate_res else 0.0

    # ===== 3. 问症率 =====
    inquiry_rate_res = _execute_safe(
        """SELECT
            COUNT(DISTINCT CASE WHEN 是否问症='是' THEN 场景ID END) * 1.0
            / COUNT(DISTINCT 场景ID) * 100.0 AS rate
        FROM data"""
    )
    inquiry_rate = round(inquiry_rate_res[0]["rate"], 1) if inquiry_rate_res else 0.0

    # ===== 4. 联合用药率 =====
    combo_rate_res = _execute_safe(
        """SELECT
            COUNT(DISTINCT CASE WHEN 是否联合用药='是' THEN 场景ID END) * 1.0
            / COUNT(DISTINCT 场景ID) * 100.0 AS rate
        FROM data"""
    )
    combo_rate = round(combo_rate_res[0]["rate"], 1) if combo_rate_res else 0.0

    # ===== 5. 近7日趋势 =====
    trend = _execute_safe(
        """SELECT ydate, COUNT(DISTINCT 场景ID) AS cnt
        FROM data
        WHERE ydate >= (SELECT MIN(ydate) FROM data)
        GROUP BY ydate
        ORDER BY ydate DESC
        LIMIT 7"""
    )
    # 补全缺失日期
    trend_map = {}
    for r in trend:
        ydate_val = r["ydate"]
        # DuckDB 返回的 ydate 可能是 Timestamp 或 date 对象，统一转成 YYYY-MM-DD 字符串
        if hasattr(ydate_val, 'strftime'):
            ydate_str = ydate_val.strftime('%Y-%m-%d')
        else:
            ydate_str = str(ydate_val)
        trend_map[ydate_str] = r["cnt"]
    trend_data = []
    for d in last7:
        trend_data.append({
            "date": d,
            "count": trend_map.get(d, 0),
        })

    # ===== 6. 疾病TOP5 =====
    disease_top = _execute_safe(
        """SELECT 疾病名称, COUNT(DISTINCT 场景ID) AS cnt
        FROM data
        WHERE 疾病名称 IS NOT NULL AND 疾病名称 != ''
        GROUP BY 疾病名称
        ORDER BY cnt DESC
        LIMIT 5"""
    )
    disease_list = [
        {"name": r["疾病名称"], "count": r["cnt"]}
        for r in disease_top
    ] if disease_top else []

    # ===== 7. 省份TOP5 =====
    province_top = _execute_safe(
        """SELECT 省份, COUNT(DISTINCT 场景ID) AS cnt
        FROM data
        WHERE 省份 IS NOT NULL AND 省份 != ''
        GROUP BY 省份
        ORDER BY cnt DESC
        LIMIT 5"""
    )
    province_list = [
        {"name": r["省份"], "count": r["cnt"]}
        for r in province_top
    ] if province_top else []

    # ===== 8. 异常检测（今日 vs 昨日环比） =====
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_scenes = _execute_safe(
        f"SELECT COUNT(DISTINCT 场景ID) AS cnt FROM data WHERE ydate = '{yesterday}'"
    )
    yesterday_cnt = yesterday_scenes[0]["cnt"] if yesterday_scenes else 0

    alerts = []
    if yesterday_cnt > 0 and today_scenes_cnt > 0:
        change_pct = round((today_scenes_cnt - yesterday_cnt) / yesterday_cnt * 100, 1)
        if abs(change_pct) >= 20:
            direction = "📈 上升" if change_pct > 0 else "📉 下降"
            alerts.append({
                "type": "scene_change",
                "level": "warning" if abs(change_pct) >= 50 else "info",
                "message": f"今日场景数较昨日{direction} {abs(change_pct)}%（{yesterday_cnt} → {today_scenes_cnt}）",
            })

    # ===== 数据日期范围 =====
    date_range = _execute_safe(
        "SELECT MIN(ydate) AS min_date, MAX(ydate) AS max_date FROM data"
    )
    def _fmt_date(val) -> str:
        if hasattr(val, 'strftime'):
            return val.strftime('%Y-%m-%d')
        return str(val)

    min_date = _fmt_date(date_range[0]["min_date"]) if date_range else "-"
    max_date = _fmt_date(date_range[0]["max_date"]) if date_range else "-"

    return {
        "total": {
            "total_scenes": total_scenes_cnt,
            "today_scenes": today_scenes_cnt,
            "week_scenes": week_scenes_cnt,
            "close_rate": close_rate,
            "inquiry_rate": inquiry_rate,
            "combo_rate": combo_rate,
        },
        "trend": trend_data,
        "top_diseases": disease_list,
        "top_provinces": province_list,
        "alerts": alerts,
        "date_range": {
            "min_date": min_date,
            "max_date": max_date,
        },
    }
