"""星宝语料场景查询系统 — DuckDB 查询引擎"""

import os
import re
import time
import threading
from pathlib import Path
from typing import Optional, List

import duckdb
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

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
            pass  # 调用方会处理

        return True, ""


def _check_healthy(conn: Optional[duckdb.DuckDBPyConnection], timeout_sec: int = 3) -> bool:
    """快速检查 DuckDB 连接是否健康"""
    if conn is None:
        return False
    try:
        # 用超时保护，避免被死锁连接卡住
        result = conn.execute("SELECT 1")
        result.fetchone()
        return True
    except Exception:
        return False


class ConnectionPool:
    """DuckDB 连接池（固定大小），轮询分配 + 健康检测 + 自动重建"""

    def __init__(self, size: int = 6):
        self._connections: List[Optional[duckdb.DuckDBPyConnection]] = [None] * size
        self._last_ok: List[float] = [0.0] * size  # 最后活跃时间戳，0=需重建
        self._heartbeat_sec: float = 60.0  # 超过此秒数无活动→自动重建
        self._lock = threading.Lock()
        self._next = 0
        self._db_file = ":memory:"
        self._memory_limit = "1GB"
        self._initialized = False

    def init(self, db_file: str, memory_limit: str,
             mapping_df: Optional[pd.DataFrame] = None,
             master_conn: Optional[duckdb.DuckDBPyConnection] = None):
        """
        初始化连接池。
        - 持久化模式：各连接打开同一 .duckdb 文件，data 表共享
        - 内存模式：各连接各自独立，需在外部确保数据加载
        """
        self._db_file = db_file
        self._memory_limit = memory_limit

        for i in range(len(self._connections)):
            conn = duckdb.connect(db_file)
            conn.execute(f"SET memory_limit='{memory_limit}'")
            conn.execute("SET threads = 1")
            self._connections[i] = conn
            self._last_ok[i] = time.time()  # 初始标记为健康

            # 如果是持久化模式，已存在的 data 表所有连接共享
            # 注册 drug_mapping 视图到每个连接
            if mapping_df is not None:
                conn.register("drug_mapping", mapping_df)

        self._initialized = True
        print(f"[连接池] 初始化完成: {len(self._connections)} 连接"
              f" ({'持久化' if db_file != ':memory:' else '内存'}模式)")

    def get(self) -> Optional[duckdb.DuckDBPyConnection]:
        """非阻塞取连接，不健康自动重建；锁冲突时返回 None"""
        if not self._initialized:
            raise RuntimeError("ConnectionPool 未初始化")

        now = time.time()
        if not self._lock.acquire(blocking=False):
            # 拿不到锁 → 服务繁忙
            return None

        try:
            for _ in range(len(self._connections)):
                idx = self._next
                self._next = (self._next + 1) % len(self._connections)
                conn = self._connections[idx]
                if conn is None:
                    print(f"[连接池] 连接 #{idx} 为空，正在重建...")
                    self._rebuild(idx)
                    self._last_ok[idx] = now
                    return self._connections[idx]
                if now - self._last_ok[idx] < self._heartbeat_sec:
                    return conn
                # 连接超时未活动 → 重建
                print(f"[连接池] 连接 #{idx} 超时未活动 ({(now - self._last_ok[idx]):.0f}s)，正在重建...")
                self._rebuild(idx)
                self._last_ok[idx] = now
                return self._connections[idx]
        finally:
            self._lock.release()

        raise RuntimeError("连接池异常")

    def mark_unhealthy(self, conn: duckdb.DuckDBPyConnection):
        """标记连接为不健康（时间戳置零），下次 get() 时会自动重建"""
        for i, c in enumerate(self._connections):
            if c is conn:
                self._last_ok[i] = 0.0
                print(f"[连接池] 连接 #{i} 标记为不健康")
                break

    def _rebuild(self, idx: int):
        """重建指定位置的连接（不关闭旧连接——旧连接可能在 worker 线程中仍被使用）"""
        conn = duckdb.connect(self._db_file)
        conn.execute(f"SET memory_limit='{self._memory_limit}'")
        conn.execute("SET threads = 1")
        self._connections[idx] = conn
        self._last_ok[idx] = time.time()
        print(f"[连接池] 连接 #{idx} 重建完成")

    def close_all(self):
        """关闭所有连接"""
        for i, conn in enumerate(self._connections):
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
                self._connections[i] = None
        self._initialized = False
        print("[连接池] 已关闭所有连接")

    @property
    def size(self) -> int:
        return len(self._connections)


class DuckDbEngine:
    """DuckDB 查询引擎（单例），内部管理连接池"""

    _instance: Optional["DuckDbEngine"] = None
    _pool: Optional[ConnectionPool] = None
    _query_executor: Optional[ThreadPoolExecutor] = None
    _loaded: bool = False
    _row_count: int = 0
    _mapping_loaded: bool = False
    _mapping_row_count: int = 0
    _lock = threading.Lock()


    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load_data(self, force: bool = False) -> dict:
        """
        加载数据源到 DuckDB，初始化连接池。
        返回: 加载信息
        """
        with self._lock:
            if self._loaded and not force:
                return self._get_info()

            data_path = Path(settings.DATA_PATH)
            if not data_path.exists():
                raise FileNotFoundError(f"数据文件不存在: {data_path}")

            # 关闭旧连接池
            if self._pool:
                self._pool.close_all()

            # 初始化查询线程池（独立于 uvicorn 事件循环）
            if self._query_executor is None:
                self._query_executor = ThreadPoolExecutor(max_workers=4)
                print(f"[查询线程池] 初始化完成: 4 workers")

            # ---- 环境判断：持久化 vs 内存模式 ----
            is_persistent = os.getenv("STARQUERY_DB_MODE", "").lower() == "persistent"

            pool_size = 6 if is_persistent else 1

            if is_persistent:
                db_file = "/tmp/star-query.duckdb"
                self._pool = ConnectionPool(size=pool_size)
                # 主连接：加载数据到文件
                master = duckdb.connect(db_file)
                master.execute("SET memory_limit='2GB'")
                print(f"[DuckDB] 持久化模式: {db_file} (内存上限: 2GB)")

                if self._pool is None:
                    self._pool = ConnectionPool(size=2)

                # 检查表是否已存在
                table_exists = master.execute(
                    "SELECT count(*) FROM duckdb_tables() WHERE table_name='data'"
                ).fetchone()[0]

                if not table_exists or force:
                    master.execute(
                        f"CREATE TABLE data AS SELECT * FROM read_parquet('{data_path}')"
                    )

                self._row_count = master.execute(
                    "SELECT COUNT(*) FROM data"
                ).fetchone()[0]

                # 加载药品映射表（DataFrame 形式）
                mapping_df = self._load_mapping_df()
                mapping_df_for_pool = mapping_df.copy() if mapping_df is not None else None

                # 初始化连接池
                self._pool.init(
                    db_file=db_file,
                    memory_limit="2GB",
                    mapping_df=mapping_df_for_pool,
                )

                # 关闭主连接（池中的连接已打开数据库文件）
                # 注意：master 和池连接都打开了同一个 .duckdb 文件
                # DuckDB 允许多连接读写同一文件，master 先关闭避免文件锁冲突
                # 但池初始化时已重新打开连接，所以可以关闭 master
                try:
                    master.close()
                except Exception:
                    pass

            else:
                # 内存模式（测试环境）
                db_file = ":memory:"
                self._pool = ConnectionPool(size=pool_size)

                master = duckdb.connect(":memory:")
                master.execute("SET memory_limit='1GB'")
                print(f"[DuckDB] 内存模式 (内存上限: 1GB)")

                # 加载 parquet 到 master
                master.execute(
                    f"CREATE TABLE data AS SELECT * FROM read_parquet('{data_path}')"
                )

                self._row_count = master.execute(
                    "SELECT COUNT(*) FROM data"
                ).fetchone()[0]

                # 加载药品映射表
                mapping_df = self._load_mapping_df()

                # 内存模式下池只有 1 个连接，把 master 交给池
                if pool_size == 1:
                    # 把 master 作为池中唯一连接
                    self._pool._connections[0] = master
                    self._pool._last_ok[0] = time.time()  # 标记为健康
                    self._pool._initialized = True
                    if mapping_df is not None:
                        master.register("drug_mapping", mapping_df)
                else:
                    # 理论上走不到这里，但以防万一
                    master.close()

                print(f"[连接池] 初始化完成: {pool_size} 连接 (内存模式)")

            self._loaded = True
            self._mapping_loaded = True

            # 创建药品倒排索引表（P1-2 药品加速）
            self._build_drug_index()



            info = self._get_info()
            print(f"[DuckDB] 加载完成: {info['total_rows']} 行 × {info['total_cols']} 列")
            return info

    def _load_mapping_df(self) -> Optional[pd.DataFrame]:
        """加载药品映射表为 DataFrame"""
        mapping_path = getattr(
            settings, "MAPPING_PATH",
            "/tmp/star-mapping/results/星宝药品ATC映射表_v1.xlsx"
        )

        if not os.path.exists(mapping_path):
            print(f"[映射表] 文件不存在: {mapping_path}，跳过加载")
            return None

        try:
            df = pd.read_excel(mapping_path)
            self._mapping_row_count = len(df)
            self._mapping_loaded = True
            matched_rate = round(
                (df["置信度"] != "待人工审核").sum() / len(df) * 100, 1
            ) if "置信度" in df.columns else 0
            print(f"[映射表] 加载完成: {{loaded: True, rows: {self._mapping_row_count}, matched_rate: {matched_rate}}}")
            return df
        except Exception as e:
            print(f"[映射表] 加载失败: {e}")
            return None

    def _get_info(self) -> dict:
        """获取数据源信息"""
        conn = None
        try:
            if self._pool:
                conn = self._pool.get()
                df = conn.execute("SELECT * FROM data LIMIT 1").fetchdf()
                cols = []
                for col in df.columns:
                    sample = df[col].iloc[0] if len(df) > 0 else None
                    if isinstance(sample, float) and pd.isna(sample):
                        sample = None
                    cols.append({
                        "name": col,
                        "dtype": str(df[col].dtype),
                        "sample": sample,
                    })

                info = {
                    "total_rows": self._row_count,
                    "total_cols": len(df.columns),
                    "columns": cols,
                    "mapping": {
                        "loaded": self._mapping_loaded,
                        "rows": self._mapping_row_count,
                    } if self._mapping_loaded else None,
                }
                return info
        except Exception:
            pass

        return {"total_rows": 0, "total_cols": 0, "columns": [], "mapping": None}

    def incremental_load(self, parquet_path: str) -> dict:
        """运行时增量加载数据（不重启服务）

        将 parquet 文件中的数据 INSERT 到现有 data 表，
        然后重建 drug_index 和预聚合表。

        流程：
        1. 获取增量 parquet 的日期范围（用于去重）
        2. 从 data 表 DELETE 目标日期的旧数据（避免重复）
        3. INSERT 增量数据到 data
        4. 行数更新
        5. 重建 drug_index（~14s）
        6. 重建预聚合表（~2s）

        Args:
            parquet_path: 增量 parquet 文件路径

        Returns:
            dict: {success, incr_rows, data_rows, elapsed_ms, details}
        """
        import time
        import os as _os

        start = time.time()
        result = {
            "success": False,
            "incr_rows": 0,
            "data_rows": 0,
            "elapsed_ms": 0,
            "details": "",
        }

        if not _os.path.exists(parquet_path):
            result["details"] = f"文件不存在: {parquet_path}"
            print(f"[增量加载] 失败: 文件不存在 {parquet_path}")
            return result

        if not self._pool or not self._loaded:
            result["details"] = "引擎未初始化，请先调用 load_data()"
            return result

        try:
            conn = self._pool.get()
            if conn is None:
                result["details"] = "无可用连接"
                return result

            # Step 1: 获取增量 parquet 信息
            date_info = conn.execute(
                "SELECT MIN(ydate), MAX(ydate), COUNT(*) FROM read_parquet(?)",
                [parquet_path]
            ).fetchone()

            min_date, max_date, incr_rows = date_info

            if incr_rows is None or incr_rows == 0:
                result["success"] = True
                result["details"] = "增量文件为空，无操作"
                result["elapsed_ms"] = round((time.time() - start) * 1000, 2)
                return result

            print(f"[增量加载] 文件: {parquet_path} ({incr_rows:,} 行, "
                  f"日期: {min_date} ~ {max_date})")

            # Step 2: 从 data 表删除目标日期的旧数据（避免重复）
            # 查询旧数据中目标日期的行数
            old_count = conn.execute(
                "SELECT COUNT(*) FROM data WHERE ydate >= ? AND ydate <= ?",
                [min_date, max_date]
            ).fetchone()[0]

            if old_count > 0:
                conn.execute(
                    "DELETE FROM data WHERE ydate >= ? AND ydate <= ?",
                    [min_date, max_date]
                )
                print(f"[增量加载] 已删除 {old_count:,} 行旧数据 (日期: {min_date} ~ {max_date})")

            # Step 3: INSERT 增量数据
            conn.execute(
                "INSERT INTO data SELECT * FROM read_parquet(?)",
                [parquet_path]
            )
            print(f"[增量加载] 已插入 {incr_rows:,} 行增量数据")

            # Step 4: 更新行数
            new_count = conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
            with self._lock:
                self._row_count = new_count

            # Step 5: 重建 drug_index
            self._build_drug_index()

            elapsed = (time.time() - start) * 1000
            result["success"] = True
            result["incr_rows"] = int(incr_rows)
            result["data_rows"] = int(new_count)
            result["deleted_rows"] = int(old_count)
            result["elapsed_ms"] = round(elapsed, 2)
            result["details"] = (f"增量 {incr_rows:,} 行 (删除 {old_count:,} 行旧数据), "
                                 f"data表总行数 {new_count:,}")

            print(f"[增量加载] 完成: +{incr_rows:,} 行, "
                  f"data总行数 {new_count:,} ({elapsed:.0f}ms)")

        except Exception as e:
            result["details"] = str(e)
            print(f"[增量加载] 失败: {e}")

        return result

    def _build_drug_index(self):
        """创建药品倒排索引表（P1-2 药品加速）

        将三个药品 JSON 数组字段展开为 {场景ID, 药品名, 来源字段} 格式，
        药品 LIKE 查询改走倒排索引，从全表扫 6s → 毫秒级。

        幂等：每次先删再建，启动时更新。
        一次性建表成本 ~4-5s（611万行），仅在启动时执行一次。
        """
        if not self._loaded or not self._pool:
            return

        try:
            conn = self._pool.get()
            if conn is None:
                print("[药品索引] 跳过：无可用连接")
                return

            # 检查 data 表是否有数据
            row_count = conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
            if row_count == 0:
                print("[药品索引] 跳过：data 表为空")
                return

            start = time.time()

            conn.execute("DROP TABLE IF EXISTS drug_index")
            conn.execute("""
                CREATE TABLE drug_index AS
                SELECT DISTINCT
                    场景ID,
                    TRIM(t.drug, ' "') AS 药品名,
                    '场景提及药品' AS 来源字段
                FROM data,
                LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug)
                WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' AND 场景提及药品 != ''

                UNION ALL

                SELECT DISTINCT
                    场景ID,
                    TRIM(t.drug, ' "') AS 药品名,
                    '顾客点名药品' AS 来源字段
                FROM data,
                LATERAL UNNEST(string_split(TRIM(顾客点名药品, '[]'), ',')) AS t(drug)
                WHERE 顾客点名药品 IS NOT NULL AND 顾客点名药品 != '[]' AND 顾客点名药品 != ''

                UNION ALL

                SELECT DISTINCT
                    场景ID,
                    TRIM(t.drug, ' "') AS 药品名,
                    '订单药品' AS 来源字段
                FROM data,
                LATERAL UNNEST(string_split(TRIM(订单药品, '[]'), ',')) AS t(drug)
                WHERE 订单药品 IS NOT NULL AND 订单药品 != '[]' AND 订单药品 != ''
                AND t.drug NOT LIKE '%未识别%'
            """)

            index_rows = conn.execute("SELECT COUNT(*) FROM drug_index").fetchone()[0]
            elapsed = (time.time() - start) * 1000
            print(f"[药品索引] 完成: {index_rows} 行 ({elapsed:.0f}ms)")

            # 建索引：按药品名分组加速 LIKE 查询
            conn.execute("DROP TABLE IF EXISTS drug_name_index")
            conn.execute("""
                CREATE TABLE drug_name_index AS
                SELECT 药品名, LIST(场景ID) AS 场景ID列表
                FROM drug_index
                GROUP BY 药品名
            """)
            name_rows = conn.execute("SELECT COUNT(*) FROM drug_name_index").fetchone()[0]
            elapsed_total = (time.time() - start) * 1000
            print(f"[药品索引] drug_name_index 完成: {name_rows} 个唯一药品名 ({elapsed_total:.0f}ms)")
        except Exception as e:
            print(f"[药品索引] 失败 (不影响主流程): {e}")

    def shutdown_executor(self):
        """关闭查询线程池"""
        if self._query_executor:
            self._query_executor.shutdown(wait=False)
            self._query_executor = None
            print("[查询线程池] 已关闭")

    def get_drug_mapping_df(self) -> pd.DataFrame:
        """返回 drug_mapping 的 pandas DataFrame"""
        try:
            conn = self._pool.get()
            return conn.execute("SELECT * FROM drug_mapping").fetchdf()
        except Exception:
            return pd.DataFrame()

    def _execute_with_timeout(self, conn: duckdb.DuckDBPyConnection,
                              sql: str, timeout_sec: int = 15):
        """
        在独立线程中执行 DuckDB 查询，带真正超时保护。
        
        利用 ThreadPoolExecutor 将 DuckDB 查询隔离到独立线程中执行，
        利用 future.result(timeout=...) 实现硬超时。
        超时后不调用 conn.interrupt()（可能留下不一致状态），
        而是由调用方标记连接重建。
        """
        if self._query_executor is None:
            self._query_executor = ThreadPoolExecutor(max_workers=4)

        future = self._query_executor.submit(conn.execute, sql)
        try:
            return future.result(timeout=timeout_sec)
        except FutureTimeoutError:
            raise TimeoutError(f"查询执行超时 ({timeout_sec}s)")

    def _rewrite_drug_like_query(self, sql: str) -> str:
        """药品 LIKE 查询自动路由到 drug_name_index 索引

        检测 SQL 中的 场景提及药品/顾客点名药品/订单药品 LIKE 条件，
        改写为走 drug_name_index 子查询（259443行小表 LIKE，毫秒级）。

        改写示例：
          SELECT * FROM data WHERE 场景提及药品 LIKE '%倍他乐克%'
          → SELECT * FROM data WHERE 场景ID IN (SELECT 场景ID FROM drug_name_index WHERE 药品名 LIKE '%倍他乐克%')

          三个字段 OR 也会统一改写为单子查询（drug_name_index 已包含三个来源）
        """
        drug_fields = ['场景提及药品', '顾客点名药品', '订单药品']
        field_or = '|'.join(re.escape(f) for f in drug_fields)

        # 检测药品 LIKE 条件并提取值
        like_pattern = rf'(?:{field_or})\s+LIKE\s+\'([^\']+)\''
        all_vals = re.findall(like_pattern, sql, re.IGNORECASE)
        if not all_vals:
            return sql

        # 只处理单一 LIKE 值的情况（多值不同暂不优化）
        unique_vals = list(set(all_vals))
        if len(unique_vals) != 1:
            return sql

        like_val = unique_vals[0]
        subquery = f"场景ID IN (SELECT 场景ID FROM drug_index WHERE 药品名 LIKE '{like_val}')"

        # 找到 WHERE 子句范围（截断到 GROUP/ORDER/LIMIT 之前）
        where_m = re.search(r'\bWHERE\b', sql, re.IGNORECASE)
        if not where_m:
            return sql

        before_where = sql[:where_m.start()]
        after_where = sql[where_m.end():]

        # 截断：保留 WHERE 后到 GROUP/ORDER/LIMIT/OFFSET 之前的部分
        after_where = re.split(
            r'\b(GROUP\s+BY|ORDER\s+BY|LIMIT|OFFSET)\b',
            after_where, maxsplit=1, flags=re.IGNORECASE
        )
        tail = ''
        if len(after_where) > 1:
            tail = ''.join(after_where[1:])  # 保留截断的后半段（GROUP BY/ORDER BY/LIMIT...）
        after_where = after_where[0]

        # 移除所有药品 LIKE 条件（包括 OR 前缀）
        cleaned = re.sub(
            rf'(?:\s+OR\s+)?(?:{field_or})\s+LIKE\s+\'{re.escape(like_val)}\'',
            '',
            after_where,
            flags=re.IGNORECASE
        )

        # 清理多余的括号/空格/AND/OR
        cleaned = cleaned.strip()
        # 去掉空括号
        cleaned = re.sub(r'\(\s*\)', '', cleaned)
        # 清理括号内开头的 AND/OR（如 (AND 条件) → (条件)）
        cleaned = re.sub(r'\(\s*(AND|OR)\s+', r'(', cleaned)
        # 清理括号内结尾的 AND/OR
        cleaned = re.sub(r'\s+(AND|OR)\s*\)', r')', cleaned)
        # 归一化空格
        cleaned = re.sub(r'\(\s+', '(', cleaned)
        cleaned = re.sub(r'\s+\)', ')', cleaned)
        cleaned = re.sub(r'\s+OR\s+', ' OR ', cleaned)
        cleaned = re.sub(r'\s+AND\s+', ' AND ', cleaned)
        # 去掉开头/结尾的 AND/OR
        cleaned = re.sub(r'^\s*(?:AND|OR)\s+', '', cleaned)
        cleaned = re.sub(r'\s+(?:AND|OR)\s*$', '', cleaned)
        cleaned = cleaned.strip()

        # 构建新 WHERE
        if cleaned and cleaned not in ('()', ''):
            new_where = f"WHERE ({subquery}) AND ({cleaned})"
        else:
            new_where = f"WHERE {subquery}"

        result = f"{before_where} {new_where} {tail}"
        # 清理多余空格
        # 清理多余空格
        result = re.sub(r'\s+', ' ', result).strip()
        if sql.strip().endswith(';'):
            result = result.rstrip() + ';'

        # 打印改写日志（首次命中时）
        if sql != result:
            print(f"[药品索引] 查询改写: {sql[:100]}... → {result[:100]}...")

        return result

    def _rewrite_drug_unnest_query(self, sql: str) -> str:
        """药品 UNNEST 查询改写为 drug_index 查询（P2-1 UNNEST 加速）

        检测 LATERAL UNNEST(string_split(TRIM(药品字段, '[]'), ',')) AS t(drug) 模式，
        改写为直接查 drug_index 表，避免运行时 string_split + UNNEST 全表扫描。

        无条件查询（"各药品分布"）→ 直接查 drug_index（<1ms，加速 40000x+）
        带条件查询（"高血压各药品分布"）→ drug_index JOIN data（~140ms，加速 2.6x）

        在 _rewrite_drug_like_query() 之后链式调用。
        """
        drug_fields = ['场景提及药品', '顾客点名药品', '订单药品']
        field_or = '|'.join(re.escape(f) for f in drug_fields)

        # 检测 UNNEST 模式
        unnest_re = re.compile(
            r'LATERAL\s+UNNEST\s*\(\s*string_split\s*\(\s*TRIM\s*\(\s*'
            rf'({field_or})\s*,\s*\'\[\]\'\s*\)\s*,\s*\',\'\s*\)\s*\)'
            r'\s+AS\s+t\s*\(\s*drug\s*\)',
            re.IGNORECASE
        )
        m = unnest_re.search(sql)
        if not m:
            return sql

        dim_field = m.group(1)

        # 提取 SELECT 维度别名
        alias_m = re.search(r't\.drug\s+AS\s+(\S+)', sql, re.IGNORECASE)
        dim_alias = alias_m.group(1).rstrip(', ') if alias_m else dim_field

        # 提取 LIMIT
        limit_m = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
        limit_val = limit_m.group(1) if limit_m else str(settings.MAX_ROWS)

        # 检测聚合类型
        is_deal_count = bool(re.search(
            r'COUNT\s*\(\s*DISTINCT\s+CASE\s+WHEN\s+交易是否达成\s*=\s*\'是\''
            r'.*?场景ID\s+END\s*\)',
            sql, re.IGNORECASE | re.DOTALL
        ))

        # 提取非 UNNEST 相关的 WHERE 条件
        where_m = re.search(
            r'\bWHERE\b(.+?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)',
            sql, re.IGNORECASE | re.DOTALL
        )

        has_extra_conditions = False
        extra_conditions = ''

        if where_m:
            where_text = where_m.group(1).strip()
            # 移除 UNNEST 字段独有的空值/空数组校验条件
            cleaned = re.sub(
                rf'{re.escape(dim_field)}\s+IS\s+NOT\s+NULL\s*(?:AND\s+)?',
                '', where_text, flags=re.IGNORECASE
            )
            cleaned = re.sub(
                rf'{re.escape(dim_field)}\s+!=\s*\'.*?\'\s*(?:AND\s+)?',
                '', cleaned, flags=re.IGNORECASE
            )
            # 移除完之后的AND清理
            cleaned = re.sub(r'^\s*AND\s+', '', cleaned)
            cleaned = re.sub(r'\s+AND\s*$', '', cleaned)
            cleaned = cleaned.strip()
            if cleaned and cleaned not in ('', '()'):
                has_extra_conditions = True
                extra_conditions = cleaned

        # --- 构建新 SQL ---
        if is_deal_count:
            # 成交场景数：需要 JOIN data 获取 交易是否达成 字段
            new_sql = (
                f"SELECT di.药品名 AS {dim_alias}, "
                f"COUNT(DISTINCT CASE WHEN data.交易是否达成='是' "
                f"THEN di.场景ID END) AS 成交场景数 "
                f"FROM drug_index di "
                f"JOIN data ON di.场景ID = data.场景ID "
                f"WHERE di.来源字段='{dim_field}'"
            )
            if has_extra_conditions:
                new_sql += f" AND ({extra_conditions})"
            new_sql += (
                f" GROUP BY di.药品名 "
                f"ORDER BY 成交场景数 DESC LIMIT {limit_val}"
            )

        elif not has_extra_conditions:
            # 无条件 → 直接查 drug_index（最快路径，<1ms）
            new_sql = (
                f"SELECT 药品名 AS {dim_alias}, "
                f"COUNT(DISTINCT 场景ID) AS 场景数 "
                f"FROM drug_index "
                f"WHERE 来源字段='{dim_field}' "
                f"GROUP BY 药品名 ORDER BY 场景数 DESC LIMIT {limit_val}"
            )
        else:
            # 有条件 → drug_index JOIN data 保留附加过滤
            new_sql = (
                f"SELECT di.药品名 AS {dim_alias}, "
                f"COUNT(DISTINCT di.场景ID) AS 场景数 "
                f"FROM drug_index di "
                f"JOIN data ON di.场景ID = data.场景ID "
                f"WHERE di.来源字段='{dim_field}' AND ({extra_conditions}) "
                f"GROUP BY di.药品名 ORDER BY 场景数 DESC LIMIT {limit_val}"
            )

        print(f"[药品UNNEST] 改写: {sql[:100]}... → {new_sql[:100]}...")
        return new_sql

    def execute(self, sql: str) -> dict:
        """
        执行查询（从连接池取连接，执行，超时保护）
        返回: 查询结果信息
        """
        if not self._loaded or not self._pool:
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
            # 从连接池取健康连接（非阻塞）
            conn = self._pool.get()
            if conn is None:
                return {
                    "success": False,
                    "error": "服务繁忙，所有查询连接均在执行中，请稍后重试",
                    "rows": [],
                    "total_rows": 0,
                    "elapsed_ms": 0,
                }
            start = time.time()
            # P1-2: 药品 LIKE 查询自动路由到 drug_name_index 索引
            sql_checked = self._rewrite_drug_like_query(sql_checked)
            # P2-1: 药品 UNNEST 查询改写为 drug_index 查询（加速 40000x+）
            sql_checked = self._rewrite_drug_unnest_query(sql_checked)
            result = self._execute_with_timeout(conn, sql_checked, timeout_sec=15)
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
        except TimeoutError:
            # 超时：标记连接为不健康，下次 get() 会重建
            # 不调用 conn.interrupt() — 线程池中的 worker 线程仍在执行，
            # 但连接已被丢弃，重建后不影响新查询
            try:
                self._pool.mark_unhealthy(conn)
            except Exception:
                pass
            return {
                "success": False,
                "error": "查询超时，系统已自动恢复",
                "rows": [],
                "total_rows": 0,
                "elapsed_ms": 0,
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
        if not self._loaded or not self._pool:
            self.load_data()
        return self._pool.get()


# 全局引擎实例
engine = DuckDbEngine()
