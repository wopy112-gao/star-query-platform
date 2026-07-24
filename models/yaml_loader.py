"""YAML 文件加载器 — 支持热加载 + 缓存 + 兜底"""

from __future__ import annotations

import yaml
import os
import time
from pathlib import Path
from typing import Any


class YamlLoader:
    """YAML 文件加载器

    特性：
    - 懒加载 + 缓存
    - 文件不存在 / 解析失败 → fallback 兜底
    - 可选热加载（检测文件修改时间）
    """

    _cache: dict[str, dict] = {}
    _mtime: dict[str, float] = {}

    @classmethod
    def load(
        cls,
        path: str | Path,
        hot_reload: bool = False,
    ) -> dict:
        """从 YAML 文件加载配置

        Args:
            path: YAML 文件路径
            hot_reload: 是否热加载（每次调用检测文件修改时间）

        Returns:
            解析后的字典
        """
        path = Path(path)
        abs_path = str(path.resolve())

        if hot_reload:
            cls._check_reload(abs_path, path)
        elif abs_path not in cls._cache:
            cls._load_file(abs_path, path)

        return cls._cache.get(abs_path, {})

    @classmethod
    def load_with_fallback(
        cls,
        path: str | Path,
        fallback: dict | None = None,
        hot_reload: bool = False,
    ) -> dict:
        """从 YAML 文件加载配置，失败时返回 fallback

        Args:
            path: YAML 文件路径
            fallback: 兜底字典（YAML 加载失败时使用）
            hot_reload: 是否热加载

        Returns:
            解析后的字典（失败时返回 fallback 或空字典）
        """
        path = Path(path)
        if not path.exists():
            print(f"[YamlLoader] 文件不存在: {path}，使用 fallback")
            return fallback or {}

        try:
            return cls.load(path, hot_reload=hot_reload)
        except Exception as e:
            print(f"[YamlLoader] 加载失败: {path} — {e}，使用 fallback")
            return fallback or {}

    @classmethod
    def invalidate(cls, path: str | Path) -> None:
        """清除指定文件的缓存"""
        abs_path = str(Path(path).resolve())
        cls._cache.pop(abs_path, None)
        cls._mtime.pop(abs_path, None)

    @classmethod
    def invalidate_all(cls) -> None:
        """清除所有缓存"""
        cls._cache.clear()
        cls._mtime.clear()

    # ---- 内部方法 ----

    @classmethod
    def _load_file(cls, abs_path: str, path: Path) -> None:
        """加载 YAML 文件到缓存"""
        mtime = path.stat().st_mtime
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        cls._cache[abs_path] = data or {}
        cls._mtime[abs_path] = mtime

    @classmethod
    def _check_reload(cls, abs_path: str, path: Path) -> None:
        """检查文件是否修改，需重新加载"""
        if abs_path not in cls._cache:
            cls._load_file(abs_path, path)
            return

        current_mtime = path.stat().st_mtime
        if current_mtime > cls._mtime.get(abs_path, 0):
            cls._load_file(abs_path, path)
