"""星宝语料场景查询系统 — Instructions 规则引擎

按意图标签动态选取相关业务规则，用于：
1. 注入 LLM Prompt，提升 SQL 生成质量
2. 标识可用的 SQL 改写规则
3. 提供质量门禁校验参考
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
import importlib.util
from dataclasses import dataclass, field

from intent_schemas import QueryIntent

# 直接通过文件路径加载 YamlLoader
_YAML_LOADER_PATH = Path(__file__).resolve().parent / "yaml_loader.py"
if _YAML_LOADER_PATH.exists():
    _spec = importlib.util.spec_from_file_location("_yaml_loader_internal", str(_YAML_LOADER_PATH))
    _yaml_loader_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_yaml_loader_mod)
    YamlLoader = _yaml_loader_mod.YamlLoader
else:
    raise ImportError(f"yaml_loader.py 不存在: {_YAML_LOADER_PATH}")


@dataclass
class Instruction:
    """单条指令"""
    id: str
    label: str
    description: str
    applies_to: list[str]
    priority: str        # high / medium / low
    rule_type: str       # prompt_injection / sql_rewrite / validation
    text: str
    rewrite_fn: str = ""


_INSTRUCTIONS_YAML = Path(__file__).resolve().parent / "instructions.yaml"


class InstructionEngine:
    """Instructions 规则引擎"""

    def __init__(self):
        self._instructions: list[Instruction] = []
        self._loaded = False

    def load(self) -> None:
        """从 YAML 加载指令"""
        raw = YamlLoader.load_with_fallback(_INSTRUCTIONS_YAML, {})
        items = raw.get("instructions", [])
        self._instructions = []
        for item in items:
            self._instructions.append(Instruction(
                id=item.get("id", ""),
                label=item.get("label", ""),
                description=item.get("description", ""),
                applies_to=item.get("applies_to", []),
                priority=item.get("priority", "medium"),
                rule_type=item.get("rule_type", "prompt_injection"),
                text=item.get("text", ""),
                rewrite_fn=item.get("rewrite_fn", ""),
            ))
        self._loaded = True
        print(f"[InstructionEngine] 已加载 {len(self._instructions)} 条指令")

    def _get_intent_tags(self, intent: Optional[QueryIntent]) -> set[str]:
        """从意图推理匹配标签

        通用 intent 标签映射：
        - 疾病条件 → disease_query
        - 药品条件 → drug_query
        - 成交条件 → deal_analysis
        - 时间范围 → time_range
        - 时间趋势 → time_trend
        - 地域维度 → distribution_query
        - 联合用药 → combo_analysis
        - 彩蛋 → egg_task_analysis
        - 比率计算 → rate_calculation
        - 数据质量 → quality_analysis
        - 药品分布 → drug_distribution
        """
        tags = set()
        if intent is None:
            return tags

        # 从条件类型推理
        for c in intent.conditions:
            if c.type in ("disease",):
                tags.add("disease_query")
            elif c.type in ("drug_any", "drug_named", "drug_mentioned", "drug_ordered"):
                tags.add("drug_query")
                tags.add("drug_distribution")
            elif c.type in ("deal_yes", "deal_no"):
                tags.add("deal_analysis")
            elif c.type == "time_range":
                tags.add("time_range")
            elif c.type == "geo":
                tags.add("distribution_query")

        # 从模式推理
        if intent.query_pattern in ("distribution", "top_n", "ranking"):
            tags.add("distribution_query")

        # 从聚合方式推理
        if intent.agg in ("成交率", "成交场景数", "未成交率"):
            tags.add("deal_analysis")
            tags.add("rate_calculation")
        elif intent.agg in ("联合用药率", "组合推荐率"):
            tags.add("combo_analysis")
        elif intent.agg in ("问症率", "关键信息到达率"):
            tags.add("rate_calculation")

        # 从维度推理
        if intent.dimension in ("省份", "城市"):
            tags.add("distribution_query")
        elif intent.dimension == "月度":
            tags.add("time_trend")

        # 从问题文本推理
        if intent.raw_question:
            q = intent.raw_question.lower()
            if "彩蛋" in q:
                tags.add("egg_task_analysis")
            if "质量" in q or "商用" in q or "置信度" in q:
                tags.add("quality_analysis")

        return tags

    def select_for_prompt(self, intent: Optional[QueryIntent], max_rules: int = 5) -> str:
        """选取适用于 LLM Prompt 注入的指令，返回格式化文本

        Args:
            intent: 用户意图（可为 None）
            max_rules: 最大注入条数

        Returns:
            格式化后的指令文本（空字符串 = 无匹配指令）
        """
        if not self._loaded:
            self.load()

        tags = self._get_intent_tags(intent)
        # 候选：匹配标签 + "all" 通配 + prompt_injection 类型
        candidates = []
        for inst in self._instructions:
            if inst.rule_type != "prompt_injection":
                continue
            # 检查是否匹配：标签交集或 all 通配
            if not set(inst.applies_to) & (tags | {"all"}):
                continue
            weight = {"high": 3, "medium": 2, "low": 1}.get(inst.priority, 1)
            candidates.append((weight, inst))

        # 按优先级排序
        candidates.sort(key=lambda x: -x[0])

        if not candidates:
            return ""

        # 返回格式化文本
        selected = candidates[:max_rules]
        lines = ["\n## 业务规则"]
        for _, inst in selected:
            lines.append(f"- {inst.text}")
        return "\n".join(lines)

    def get_rewrite_rules(self) -> list[Instruction]:
        """获取所有 SQL 改写规则"""
        if not self._loaded:
            self.load()
        return [inst for inst in self._instructions if inst.rule_type == "sql_rewrite"]

    def get_validation_rules(self, tags: set[str] | None = None) -> list[Instruction]:
        """获取校验规则，可选按标签过滤"""
        if not self._loaded:
            self.load()
        rules = [inst for inst in self._instructions if inst.rule_type == "validation"]
        if tags:
            rules = [
                r for r in rules
                if set(r.applies_to) & (tags | {"all"})
            ]
        return rules

    def get_all(self) -> list[Instruction]:
        """获取所有指令"""
        if not self._loaded:
            self.load()
        return self._instructions


# 全局单例
engine = InstructionEngine()
