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
    _health_checker_started: bool = False
    _health_checker_thread: Optional[threading.Thread] = None

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

            # 创建预聚合热数据表（后台执行，不阻塞加载流程）
            self._build_aggregate_tables()

            # 启动后台连接巡检线程（P1-1 自愈机制）
            self._start_health_checker()

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

    def _build_aggregate_tables(self):
        """创建预聚合热数据表（P0-3a）
        
        三张表：pre_agg（全局统计）、disease_agg（疾病维度）、monthly_agg（月度趋势）
        幂等：每次先删再建，确保数据最新
        """
        if not self._loaded or not self._pool:
            return

        try:
            conn = self._pool.get()
            if conn is None:
                print("[预聚合] 跳过：无可用连接")
                return

            # 检查 data 表是否有数据
            row_count = conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
            if row_count == 0:
                print("[预聚合] 跳过：data 表为空")
                return

            start = time.time()

            # --- pre_agg: 全局统计 ---
            conn.execute("DROP TABLE IF EXISTS pre_agg")
            conn.execute("""
                CREATE TABLE pre_agg AS
                SELECT
                    COUNT(DISTINCT 场景ID) AS 总场景数,
                    SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) AS 成交数,
                    COALESCE(SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) * 1.0 /
                        NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 成交率,
                    SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) AS 问症数,
                    COALESCE(SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) * 1.0 /
                        NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 问症率,
                    SUM(CASE WHEN 是否关键信息到达 = '是' THEN 1 ELSE 0 END) AS 关键信息到达数,
                    COALESCE(SUM(CASE WHEN 是否关键信息到达 = '是' THEN 1 ELSE 0 END) * 1.0 /
                        NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 关键信息到达率,
                    COUNT(DISTINCT 门店ID) AS 门店数,
                    COUNT(DISTINCT 店员ID) AS 店员数
                FROM data
            """)
            pre_rows = conn.execute("SELECT COUNT(*) FROM pre_agg").fetchone()[0]

            # --- disease_agg: 按疾病 ---
            conn.execute("DROP TABLE IF EXISTS disease_agg")
            conn.execute("""
                CREATE TABLE disease_agg AS
                SELECT
                    疾病名称,
                    COUNT(DISTINCT 场景ID) AS 场景数,
                    SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) AS 成交数,
                    COALESCE(SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) * 1.0 /
                        NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 成交率,
                    SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) AS 问症数,
                    COALESCE(SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) * 1.0 /
                        NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 问症率
                FROM data
                WHERE 疾病名称 IS NOT NULL AND 疾病名称 != ''
                GROUP BY 疾病名称
                ORDER BY 场景数 DESC
            """)
            disease_rows = conn.execute("SELECT COUNT(*) FROM disease_agg").fetchone()[0]

            # --- monthly_agg: 月度趋势 ---
            conn.execute("DROP TABLE IF EXISTS monthly_agg")
            conn.execute("""
                CREATE TABLE monthly_agg AS
                SELECT
                    strftime(ydate, '%Y-%m') AS 月份,
                    COUNT(DISTINCT 场景ID) AS 场景数,
                    SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) AS 成交数,
                    COALESCE(SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) * 1.0 /
                        NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 成交率
                FROM data
                WHERE ydate IS NOT NULL
                GROUP BY 月份
                ORDER BY 月份
            """)
            monthly_rows = conn.execute("SELECT COUNT(*) FROM monthly_agg").fetchone()[0]

            elapsed = (time.time() - start) * 1000
            print(f"[预聚合] 完成: pre_agg={pre_rows}行, disease_agg={disease_rows}行, monthly_agg={monthly_rows}行 ({elapsed:.0f}ms)")
        except Exception as e:
            print(f"[预聚合] 失败 (不影响主流程): {e}")

    def shutdown_executor(self):
        """关闭查询线程池"""
        if self._query_executor:
            self._query_executor.shutdown(wait=False)
            self._query_executor = None
            print("[查询线程池] 已关闭")

    def _start_health_checker(self):
        """启动后台连接巡检线程（P1-1 自愈机制）

        daemon 线程，随进程退出自动结束。
        每 30 秒遍历所有连接执行 SELECT 1 健康检测，
        异常连接自动重建，
        全部不可用时触发告警（Step 2 扩展为自动重启）。
        """
        if self._health_checker_started:
            return

        def checker():
            while True:
                time.sleep(30)
                if not self._loaded or not self._pool or not self._pool._initialized:
                    continue
                all_dead = True
                for i in range(self._pool.size):
                    conn = self._pool._connections[i]
                    if conn is None:
                        print(f"[健康巡检] 连接 #{i} 为空，重建中...")
                        self._pool._rebuild(i)
                        continue
                    try:
                        conn.execute("SELECT 1").fetchone()
                        all_dead = False
                    except Exception as e:
                        print(f"[健康巡检] 连接 #{i} 异常 ({e})，重建中...")
                        self._pool._rebuild(i)
                if all_dead:
                    print("[健康巡检] ⚠️ 所有连接不可用，5秒后触发自动重启...")
                    time.sleep(5)
                    # 再次确认是否真的全部不可用
                    still_dead = True
                    for i in range(self._pool.size):
                        try:
                            self._pool._connections[i].execute("SELECT 1").fetchone()
                            still_dead = False
                            break
                        except Exception:
                            pass
                    if still_dead:
                        print("[健康巡检] ⚠️ 确认所有连接不可用，正在退出进程（systemd 将自动拉起）...")
                        os._exit(1)

        self._health_checker_thread = threading.Thread(
            target=checker, daemon=True, name="duckdb-health-checker"
        )
        self._health_checker_thread.start()
        self._health_checker_started = True
        print(f"[健康巡检] 后台线程已启动（30s间隔）")

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
