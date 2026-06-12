"""星宝语料场景查询系统 — 配置模块"""

import os
import json
import hashlib
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

# 加载 .env 文件（如果存在）
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


class Settings:
    """应用配置"""
    # ---- 管理员账号 ----
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin888")

    # ---- JWT 配置 ----
    JWT_SECRET: str = os.getenv("JWT_SECRET", "change-this-to-a-random-secret-key-please")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_HOURS: int = int(os.getenv("JWT_EXPIRES_HOURS", "24"))

    # ---- 服务器 ----
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # ---- 数据源 ----
    DATA_PATH: str = os.getenv(
        "DATA_PATH",
        "/root/呼吸类主数据V20260508_clean.xlsx"
    )

    # ---- 查询限制 ----
    MAX_ROWS: int = 500          # 结果行数上限
    QUERY_TIMEOUT_SEC: int = 10  # 查询超时秒数
    MAX_HISTORY: int = 500       # 历史记录上限（/用户）

    # ---- 历史记录 ----
    HISTORY_DB_PATH: str = os.getenv("HISTORY_DB_PATH", "star-query-history.db")

    # ---- LLM 翻译配置 ----
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    LLM_TIMEOUT_SEC: int = int(os.getenv("LLM_TIMEOUT_SEC", "30"))

    # ---- 缓存版本 ----
    @property
    def CACHE_VERSION(self) -> str:
        """缓存版本号：基于 LLM 翻译器代码哈希

        llm_translator.py 变更后哈希自动变化，下次重启时旧缓存自动失效。
        也包含 template_matcher.py 的哈希，模板变更同样触发缓存失效。
        """
        backend_dir = Path(__file__).resolve().parent
        sources = []
        for fname in ("llm_translator.py", "template_matcher.py"):
            fp = backend_dir / fname
            if fp.exists():
                sources.append(fp.read_bytes())
        if sources:
            return hashlib.md5(b"".join(sources)).hexdigest()[:8]
        return "0"

    # ---- 多用户 ----
    @property
    def USERS(self) -> dict[str, str]:
        """获取多用户列表（每次重新加载.env，改后无需重启）"""
        load_dotenv(ENV_FILE, override=True)
        raw = os.getenv("USERS", "{}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}


settings = Settings()
