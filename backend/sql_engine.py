"""星宝语料场景查询系统 — DuckDB 查询引擎"""

import re
import time
import threading
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

from config import settings


# SQL 安全检查正则
_DANGEROUS_PATTERNS = re.compile(
    r"\b(DROP|INSERT|DELETE|UPDATE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|"
    r"CALL|MERGE|REPLACE|GRANT|REVOKE|ATTACH|DETACH|IMPORT|EXPORT)\b",
    re.IGNORECASE,
)

# 允许的表名
_ALLOWED_TABLES = {"data"}


class SqlValidator:
    """SQL 语句安全验证"""

    @staticmethod
    def validate(sql: str) -> tuple[bool, str]:
        """
        验证 SQL 是否安全
        返回: (是否通过, 错误信息)
        """
        sql_stripped = sql.strip()

        # 1. 必须非空
        if not sql_stripped:
            return False, "SQL 语句不能为空"

        # 2. 必须以 SELECT 开头（允许 WITH ... SELECT）
        if not re.match(r"^\s*(WITH\s+.*)?\s*SELECT\b", sql_stripped, re.IGNORECASE):
            return False, "仅支持 SELECT 查询语句"

        # 3. 禁止危险操作
        dangerous = _DANGEROUS_PATTERNS.findall(sql_stripped)
        if dangerous:
            return False, f"检测到不允许的操作: {', '.join(set(dangerous))}"

        # 4. 检查表名
        for table in _ALLOWED_TABLES:
            if table in sql_stripped.lower():
                break
        else:
            # 如果没有明确引用 data 表，检查是否有 FROM 子句引用非允许表
            from_match = re.findall(
                r"\bFROM\s+(\w+)", sql_stripped, re.IGNORECASE
            )
            for t in from_match:
                if t.lower() not in _ALLOWED_TABLES:
                    return False, f"不允许查询表 '{t}'，仅允许: {', '.join(_ALLOWED_TABLES)}"

        # 5. 限制行数
        if not re.search(r"\bLIMIT\s+\d+", sql_stripped, re.IGNORECASE):
            # 自动添加 LIMIT
            pass  # 调用方会处理

        return True, ""


class DuckDbEngine:
    """DuckDB 查询引擎（单例）"""

    _instance: Optional["DuckDbEngine"] = None
    _conn: Optional[duckdb.DuckDBPyConnection] = None
    _loaded: bool = False
    _row_count: int = 0
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load_data(self, force: bool = False) -> dict:
        """
        加载数据源到 DuckDB
        返回: 加载信息
        """
        with self._lock:
            if self._loaded and not force:
                return self._get_info()

            data_path = Path(settings.DATA_PATH)
            if not data_path.exists():
                raise FileNotFoundError(f"数据文件不存在: {data_path}")

            # 关闭旧连接
            if self._conn:
                self._conn.close()

            self._conn = duckdb.connect(":memory:")

            # 根据文件后缀选择加载方式
            ext = data_path.suffix.lower()
            print(f"[DuckDB] 加载数据: {data_path} (格式: {ext})")

            if ext == ".parquet":
                # Parquet 格式 — DuckDB 原生高速读取
                self._conn.execute(
                    f"CREATE TABLE data AS SELECT * FROM read_parquet('{data_path}')"
                )
                self._row_count = self._conn.execute(
                    "SELECT COUNT(*) FROM data"
                ).fetchone()[0]
            else:
                # xlsx 格式 — 用 pandas 读取
                df = pd.read_excel(str(data_path))
                self._conn.register("data", df)
                self._row_count = len(df)

            self._loaded = True

            info = self._get_info()
            print(f"[DuckDB] 加载完成: {info['total_rows']} 行 × {info['total_cols']} 列")
            return info

    def _get_info(self) -> dict:
        """获取数据源信息"""
        if not self._conn:
            return {"total_rows": 0, "total_cols": 0, "columns": []}

        df = self._conn.execute("SELECT * FROM data LIMIT 1").fetchdf()
        cols = []
        for col in df.columns:
            sample = df[col].iloc[0] if len(df) > 0 else None
            # 处理 NaN
            if isinstance(sample, float) and pd.isna(sample):
                sample = None
            cols.append({
                "name": col,
                "dtype": str(df[col].dtype),
                "sample": sample,
            })

        return {
            "total_rows": self._row_count,
            "total_cols": len(df.columns),
            "columns": cols,
        }

    def execute(self, sql: str) -> dict:
        """
        执行查询
        返回: 查询结果信息
        """
        if not self._loaded or not self._conn:
            self.load_data()

        # 安全检查
        valid, err_msg = SqlValidator.validate(sql)
        if not valid:
            return {
                "success": False,
                "error": err_msg,
                "rows": [],
                "total_rows": 0,
                "elapsed_ms": 0,
            }

        # 确保有 LIMIT
        sql_checked = sql.strip().rstrip(";")
        if not re.search(r"\bLIMIT\s+\d+", sql_checked, re.IGNORECASE):
            sql_checked += f" LIMIT {settings.MAX_ROWS}"

        try:
            start = time.time()
            result = self._conn.execute(sql_checked)
            df = result.fetchdf()
            elapsed = (time.time() - start) * 1000

            rows = df.to_dict(orient="records")
            truncated = len(rows) > settings.MAX_ROWS
            if truncated:
                rows = rows[: settings.MAX_ROWS]

            return {
                "success": True,
                "error": None,
                "rows": rows,
                "total_rows": len(rows),
                "elapsed_ms": round(elapsed, 2),
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"查询执行失败: {str(e)}",
                "rows": [],
                "total_rows": 0,
                "elapsed_ms": 0,
            }

    def get_schema(self) -> dict:
        """获取表 Schema"""
        if not self._loaded:
            self.load_data()
        return self._get_info()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def row_count(self) -> int:
        return self._row_count

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """暴露 DuckDB 原生连接（只读操作，如 COPY 导出）"""
        if not self._loaded or not self._conn:
            self.load_data()
        return self._conn


# 全局引擎实例
engine = DuckDbEngine()
