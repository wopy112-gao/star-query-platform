"""星宝数据平台 — 反馈事件分析器

扫描 feedback_review/ 目录中的 pending 事件，分类根因并生成修复方案。

接入点：heartbeat / session 主动调用
使用方式：from incident_analyzer import scan_and_analyze; proposals = scan_and_analyze()
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# ============================================================
# 配置
# ============================================================

# 支持环境变量覆盖，方便测试/生产环境分离
_FEEDBACK_DIR_ENV = os.environ.get("FEEDBACK_REVIEW_DIR")
_FEEDBACK_DIR = (
    Path(_FEEDBACK_DIR_ENV)
    if _FEEDBACK_DIR_ENV
    else Path(os.path.expanduser("~/.lightclaw/workspace/feedback_review"))
)
_PROPOSAL_DIR_ENV = os.environ.get("FIX_PROPOSALS_DIR")
_PROPOSAL_DIR = (
    Path(_PROPOSAL_DIR_ENV)
    if _PROPOSAL_DIR_ENV
    else Path(os.path.expanduser("~/.lightclaw/workspace/fix_proposals"))
)
_PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 扫描 pending 事件
# ============================================================

def scan_pending() -> list[dict]:
    """扫描所有 status=pending 的 incident 事件"""
    pending = []
    if not _FEEDBACK_DIR.exists():
        return pending

    for f in sorted(_FEEDBACK_DIR.glob("inc_*.json")):
        try:
            with open(f, "r") as fh:
                inc = json.load(fh)
            if inc.get("status") == "pending":
                pending.append(inc)
        except (json.JSONDecodeError, OSError):
            continue

    return pending


def mark_analyzed(incident_id: str) -> bool:
    """标记事件为已分析"""
    for f in _FEEDBACK_DIR.glob("inc_*.json"):
        try:
            with open(f, "r") as fh:
                inc = json.load(fh)
            if inc.get("incident_id") == incident_id:
                inc["status"] = "analyzed"
                with open(f, "w") as fw:
                    json.dump(inc, fw, ensure_ascii=False, indent=2)
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


# ============================================================
# 根因分析
# ============================================================

class ErrorPattern:
    """单个错误模式的分析结果"""
    def __init__(self, pattern_name: str, confidence: str):
        self.pattern_name = pattern_name
        self.confidence = confidence
        self.incidents: list[dict] = []
        self.root_cause: str = ""
        self.fix_proposal: str = ""
        self.proposed_changes: list[dict] = []

    def to_dict(self) -> dict:
        return {
            "pattern_name": self.pattern_name,
            "confidence": self.confidence,
            "incident_count": len(self.incidents),
            "root_cause": self.root_cause,
            "fix_proposal": self.fix_proposal,
            "proposed_changes": self.proposed_changes,
        }


def _classify_error_type(inc: dict) -> str:
    """判断错误类型"""
    inc_type = inc.get("type", "")
    warnings = inc.get("warnings", [])
    intent = inc.get("intent_info", {}) or {}
    sql = inc.get("sql", "")

    if inc_type == "user_dislike":
        return "user_dislike"

    if inc_type == "unknown_condition_type":
        return "unknown_condition_type"

    if inc_type == "validation_fail":
        warn_text = " ".join(warnings).lower()

        if "成交口径" in warn_text or "成交" in warn_text:
            return "deal_caliber_misjudge"

        if "维度" in warn_text:
            return "dimension_mismatch"

        if "药品" in warn_text and ("缺失" in warn_text or "不匹配" in warn_text):
            return "drug_condition_missing"

        if "自相矛盾" in warn_text:
            return "contradictory_condition"

        return "unknown_validation_fail"

    return "unknown"


def _inspect_intent_vs_sql(intent: dict, sql: str, warnings: list[str]) -> str:
    """对比意图和 SQL 的差异，判断根因"""
    if not intent or not sql:
        return ""

    drug_conditions = [
        c for c in intent.get("conditions", [])
        if "drug" in c.get("type", "")
    ]
    for dc in drug_conditions:
        drug_val = dc.get("value", "")
        if drug_val and drug_val not in sql:
            return "意图含药品「" + drug_val + "」条件，但 SQL 中未匹配到—可能是模板未正确渲染条件"

    disease_conditions = [
        c for c in intent.get("conditions", [])
        if c.get("type") == "disease"
    ]
    for dc in disease_conditions:
        disease_val = dc.get("value", "")
        if disease_val and disease_val not in sql:
            return "意图含疾病「" + disease_val + "」条件，但 SQL 中未匹配到—可能是模板未正确渲染条件"

    agg = intent.get("agg", "")
    if agg == "未成交场景数":
        has_deal_no = bool(re.search(r"交易是否达成\s*=\s*'否'", sql))
        if not has_deal_no:
            return "聚合方式为「未成交场景数」但 SQL 中可能缺少「交易是否达成='否'」条件"

    for w in warnings:
        if "缺失" in w:
            return "校验器提示「" + w + "」—可能是校验规则过于严格"
        if "不匹配" in w:
            return "校验器提示「" + w + "」—可能是模板/意图与 SQL 维度不一致"

    return "需要人工核查"


# ============================================================
# user_dislike 分析：从 query_history 查 SQL 做实质性分析
# ============================================================

def _lookup_history_sql(history_id: str) -> str:
    """按 history_id 从 query_history 表查 SQL"""
    try:
        from pathlib import Path
        import sqlite3
        db_path = Path(__file__).resolve().parent.parent / "star-query-history.db"
        if not db_path.exists():
            return ""
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT sql FROM query_history WHERE id = ?", (history_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _lookup_history_sql_by_question(question: str) -> str:
    """按问题关键词从 query_history 查最近 SQL"""
    try:
        from pathlib import Path
        import sqlite3
        db_path = Path(__file__).resolve().parent.parent / "star-query-history.db"
        if not db_path.exists():
            return ""
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            """SELECT sql FROM query_history
               WHERE question = ? ORDER BY created_at DESC LIMIT 1""",
            (question,),
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _analyze_user_dislike_with_sql(question: str, sql: str) -> dict:
    """对比问题与 SQL 的差异，推断用户不满意原因

    返回包含可执行 proposed_changes（兼容 fix_applier.py 格式）。
    """
    result = {"root_cause": "", "fix_proposal": "", "proposed_changes": []}

    # 模式1：用户要求「去重」但 SQL 未使用 COUNT(DISTINCT)
    if "去重" in question and "COUNT(DISTINCT" not in sql.upper():
        result["root_cause"] = (
            "用户问题含有「去重」关键词，期望按某字段去重计数，"
            "但生成的 SQL 未使用 COUNT(DISTINCT xxx)"
        )
        result["fix_proposal"] = "增加 few-shot 示例：去重场景/去重店员/去重门店等"
        # 可执行变更
        result["proposed_changes"] = [
            {
                "action": "add_few_shot",
                "file": "llm_translator.py",
                "content": {
                    "question": "去重店员ID按省份分布呈现",
                    "sql": "SELECT 省份, COUNT(DISTINCT 店员ID) AS 店员数 FROM data GROUP BY 省份 ORDER BY 店员数 DESC",
                },
            },
            {
                "action": "update_prompt_rule",
                "file": "llm_translator.py",
                "rule_id": "去重计数",
                "note": "需人工补充：在计数规则中增加「去重」→对应字段的去重映射",
            },
        ]
        return result

    # 模式2：用户问店员/药师分布，SQL 却统计了场景
    if ("店员" in question or "药师" in question or "店員" in question) and \
       "COUNT(DISTINCT 场景ID)" in sql.upper() and \
       "COUNT(DISTINCT 店员ID)" not in sql.upper() and \
       "COUNT(DISTINCT 药师ID)" not in sql.upper():
        # 提取地域信息（如有）
        province = ""
        for p in ["山东省", "山西省", "广东省", "江苏省", "浙江省"]:
            if p in question:
                province = p
                break
        result["root_cause"] = (
            "用户问题要求按「店员ID」计数，但 SQL 用了 COUNT(DISTINCT 场景ID)"
        )
        result["fix_proposal"] = "增加实体计数 few-shot 示例 + 更新计数规则"
        result["proposed_changes"] = [
            {
                "action": "add_few_shot",
                "file": "llm_translator.py",
                "content": {
                    "question": question,
                    "sql": f"SELECT COUNT(DISTINCT 店员ID) AS 店员数 FROM data"
                           + (f" WHERE 省份='{province}'" if province else ""),
                },
            },
        ]
        return result

    # 模式3：用户要明细，SQL 却是 GROUP BY 聚合
    if any(kw in question for kw in ["明细", "列出", "显示全部", "逐条"]):
        has_group_by = bool(re.search(r"GROUP\s+BY", sql, re.IGNORECASE))
        if has_group_by:
            result["root_cause"] = "用户期望明细数据但 SQL 包含 GROUP BY 聚合"
            result["fix_proposal"] = "意图拆解中「明细」「列出」→detail 模式，不生成 GROUP BY"
            result["proposed_changes"] = [
                {
                    "action": "update_prompt_rule",
                    "file": "query_intent.py",
                    "rule_id": "明细→detail",
                    "note": "需人工补充：在意图拆解 Prompt 中增加明细关键词→detail pattern 的映射",
                },
            ]
            return result

    # 默认：无法识别具体模式
    result["root_cause"] = "用户踩了查询「" + question + "」"
    result["fix_proposal"] = "无法自动识别不满意原因，建议人工审查"
    return result


def analyze_incident(inc: dict) -> Optional[ErrorPattern]:
    """分析单条事件，返回错误模式"""
    error_type = _classify_error_type(inc)
    intent = inc.get("intent_info", {}) or {}
    warnings = inc.get("warnings", [])
    sql = inc.get("sql", "")
    question = inc.get("question", "")

    pattern = ErrorPattern(error_type, "medium")
    pattern.incidents = [inc]

    if error_type == "deal_caliber_misjudge":
        pattern.confidence = "high"
        agg = intent.get("agg", "")
        has_deal_yes = bool(re.search(r"交易是否达成\s*=\s*'是'", sql))
        has_deal_no = bool(re.search(r"交易是否达成\s*=\s*'否'", sql))

        if agg == "未成交场景数" and has_deal_no and not has_deal_yes:
            pattern.root_cause = (
                "校验器 false positive：用户意图为「" + agg + "」，SQL 已正确使用「交易是否达成='否'」，"
                "但校验器误判为缺失成交条件（它查的是 ='是'，但未成交场景数应该查 ='否'）"
            )
            pattern.fix_proposal = (
                "修复 sql_validator.py 中成交口径校验逻辑：\n"
                "1. 获取 intent.agg\n"
                "2. 如果 agg == '未成交场景数'，检查 SQL 中是否有 交易是否达成='否'\n"
                "3. 如果满足，跳过 '交易是否达成=是' 的检查"
            )
            pattern.proposed_changes = [
                {
                    "file": "sql_validator.py",
                    "change": "成交口径校验增加 agg 判断：当意图为「未成交场景数」时，检查 ='否' 而非 ='是'",
                }
            ]
        elif agg == "成交场景数" and has_deal_yes:
            pattern.root_cause = (
                "校验器 false positive：用户意图为「" + agg + "」，SQL 已有 ='是'，但校验器仍报错"
            )
            pattern.fix_proposal = "校验器的 regex 可能匹配不到带空格的 ='是'"
            pattern.proposed_changes = [
                {
                    "file": "sql_validator.py",
                    "change": "成交口径检查改用灵活空格匹配",
                }
            ]
        else:
            pattern.root_cause = "成交口径不匹配：" + (warnings[0] if warnings else "未知")
            pattern.fix_proposal = "需要人工核实具体场景"

    elif error_type == "dimension_mismatch":
        pattern.confidence = "medium"
        pattern.root_cause = (
            "维度校验失败：用户问题中的维度词与 SQL GROUP BY 不匹配\n"
            "  问题：" + question + "\n  warning：" + (warnings[0] if warnings else "")
        )
        pattern.fix_proposal = (
            "检查 intent.dimension 是否正确解析，以及 sql_validator.py 中 _DIMENSION_MAP 是否覆盖了该维度词"
        )
        pattern.proposed_changes = [
            {
                "file": "sql_validator.py",
                "change": "检查 _DIMENSION_MAP 是否缺少对应的维度关键词映射",
            }
        ]

    elif error_type == "drug_condition_missing":
        pattern.confidence = "high"
        # 尝试从 intent 中提取条件来生成可执行修复
        drug_vals = [
            c.get("value", "") for c in intent.get("conditions", [])
            if "drug" in c.get("type", "")
        ]
        drug_name = drug_vals[0] if drug_vals else question
        pattern.root_cause = (
            "药品条件缺失：意图已识别药品「" + drug_name + "」但 SQL 中未正确渲染药品条件"
        )
        pattern.fix_proposal = "检查模板的 {conditions} 占位符是否包含药品字段"
        pattern.proposed_changes = [
            {
                "action": "fix_template",
                "file": "template_matcher.py",
                "note": f"检查涉及药品 '{drug_name}' 的模板是否有 {{conditions}} 占位符",
                "template_id": "tx06",  # 药品-城市分布模板
            },
        ]

    elif error_type == "contradictory_condition":
        pattern.confidence = "high"
        pattern.root_cause = "LLM 生成了自相矛盾的 WHERE 条件"
        pattern.fix_proposal = "已在 _fix_contradictory_where() 中处理，如有新模式需人工补充"
        pattern.proposed_changes = [
            {
                "action": "update_prompt_rule",
                "file": "llm_translator.py",
                "rule_id": "自相矛盾修复",
                "note": "检查 _fix_contradictory_where() 是否需要扩展新模式",
            },
        ]

    elif error_type == "user_dislike":
        pattern.confidence = "low"
        question = inc.get("question", "")
        feedback = inc.get("feedback_comment", "")
        history_id = inc.get("history_id", "")

        # 尝试从 query_history 查找原始 SQL
        history_sql = inc.get("sql", "")
        if not history_sql and history_id:
            history_sql = _lookup_history_sql(history_id)
        if not history_sql:
            history_sql = _lookup_history_sql_by_question(question)

        if history_sql:
            # 有 SQL 可以做实质性分析
            analysis = _analyze_user_dislike_with_sql(question, history_sql)
            pattern.root_cause = analysis.get("root_cause", "未知")
            pattern.fix_proposal = analysis.get("fix_proposal", "需人工审查")
            if analysis.get("proposed_changes"):
                pattern.proposed_changes = analysis["proposed_changes"]
                # 有可执行 action → high；纯文本 → medium
                has_executable = any(
                    "action" in c for c in analysis["proposed_changes"]
                )
                pattern.confidence = "high" if has_executable else "medium"
        else:
            pattern.root_cause = (
                "用户踩了查询「" + question + "」\n"
                "  用户备注：" + (feedback or "无")
            )
            pattern.fix_proposal = "无法从 query_history 找到该查询的 SQL 记录，需人工核查"

    elif error_type == "contradictory_condition":
        pattern.confidence = "high"
        pattern.root_cause = "LLM 生成了自相矛盾的 WHERE 条件（AND ... NOT LIKE ... 同词）"
        pattern.fix_proposal = (
            "已在 llm_translator.py 的 _fix_contradictory_where() 中处理，"
            "检查是否有未被覆盖的新模式"
        )

    elif error_type == "unknown_condition_type":
        pattern.confidence = "high"
        conditions = intent.get("conditions", []) if intent else []
        unknown_types = set()
        unknown_values = set()
        for c in conditions:
            t = c.get("type", "")
            v = c.get("value", "")
            from intent_schemas import ConditionType
            if t not in ConditionType.values():
                unknown_types.add(t)
                unknown_values.add(v)

        pattern.root_cause = (
            f"LLM 自主创建了新的条件类型: {', '.join(sorted(unknown_types))}\n"
            f"  值: {', '.join(sorted(unknown_values))}\n"
            f"  问题: {question[:80]}"
        )
        pattern.fix_proposal = (
            f"检测到新的条件类型「{', '.join(sorted(unknown_types))}」，\n"
            f"值如「{', '.join(sorted(unknown_values))}」。\n\n"
            f"需新增映射步骤：\n"
            f"1. intent_schemas.py ConditionType 枚举新增\n"
            f"2. sql_renderer.py CONDITION_SQL_MAP 新增 SQL 映射\n"
            f"3. 可选的 template_matcher.py 新模板 / llm_translator.py 新示例\n\n"
            f"⚠️ 修复前：此类查询走 LLM fallback（正确但慢）\n"
            f"⚠️ 修复后：走模板渲染（毫秒级）"
        )
        pattern.proposed_changes = [
            {
                "action": "add_condition_type",
                "file": "intent_schemas.py",
                "type_name": list(unknown_types)[0] if unknown_types else "new_type",
                "comment": f"LLM自主创建的类型，值如 {list(unknown_values)[0] if unknown_values else ''}",
            },
            {
                "action": "add_renderer_map",
                "file": "sql_renderer.py",
                "type_name": list(unknown_types)[0] if unknown_types else "new_type",
                "sql_template": "需人工确认正确 SQL 映射",
            },
        ]

    else:
        pattern.confidence = "low"
        pattern.root_cause = (
            "未识别的错误类型: " + error_type + "\n"
            "  warning: " + str(warnings)
        )
        pattern.fix_proposal = "需要人工审查"

    return pattern


def group_similar(incidents: list[dict]) -> list[list[dict]]:
    """将相似事件分组"""
    groups: dict[str, list[dict]] = {}

    for inc in incidents:
        error_type = _classify_error_type(inc)
        intent = inc.get("intent_info", {}) or {}
        agg = intent.get("agg", "") if intent else ""
        warnings = " | ".join(inc.get("warnings", []))

        key = error_type + "|" + agg + "|" + warnings[:60]
        if key not in groups:
            groups[key] = []
        groups[key].append(inc)

    return list(groups.values())


# ============================================================
# 修复方案输出
# ============================================================

def generate_proposal(incidents: list[dict]) -> dict:
    """为一批相似事件生成修复方案"""
    patterns = [analyze_incident(inc) for inc in incidents]
    patterns = [p for p in patterns if p is not None]

    if not patterns:
        return {}

    best_pattern = max(patterns, key=lambda p: {"high": 3, "medium": 2, "low": 1}.get(p.confidence, 0))

    now = datetime.now()
    proposal = {
        "proposal_id": "prop_" + now.strftime("%Y%m%d_%H%M%S"),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "incident_count": len(incidents),
        "incident_ids": [inc.get("incident_id", "") for inc in incidents],
        "samples": [
            {
                "question": inc.get("question", "")[:80],
                "warnings": inc.get("warnings", []),
            }
            for inc in incidents[:3]
        ],
        "error_pattern": best_pattern.pattern_name,
        "confidence": best_pattern.confidence,
        "root_cause": best_pattern.root_cause,
        "fix_proposal": best_pattern.fix_proposal,
        "proposed_changes": best_pattern.proposed_changes,
        "status": "pending_review",
    }

    return proposal


def save_proposal(proposal: dict) -> str:
    """保存修复方案到文件"""
    if not proposal:
        return ""

    pid = proposal.get("proposal_id", "prop_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    filepath = _PROPOSAL_DIR / (pid + ".json")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(proposal, f, ensure_ascii=False, indent=2)

    print("[Proposal] 已保存: " + str(filepath))
    return str(filepath)


def _apply_few_shot(change: dict) -> str:
    """添加 few-shot 示例到 llm_translator.py"""
    try:
        content = change.get("content", {})
        question = content.get("question", "")
        sql = content.get("sql", "")
        if not question or not sql:
            return "缺少 question 或 sql，跳过"

        target = Path(__file__).resolve().parent.parent / "backend" / "llm_translator.py"
        # 兼容测试/正式环境路径
        if not target.exists():
            target = Path(__file__).resolve().parent / "llm_translator.py"
        if not target.exists():
            return f"找不到 llm_translator.py"

        text = target.read_text(encoding="utf-8")

        # 检查是否已存在相同示例（防重复添加）
        if question in text:
            return f"示例已存在，跳过: {question[:40]}"

        # 在最后一个示例后面插入新示例
        # 找通用统计或同分类的末尾
        import re as _re
        # 找 "通用统计" 分类的末尾，在其后插入
        # 通用统计是第一条，在所有示例分类之后添加
        # 简单策略：在最后一个 ], 前面插入
        # 找到 examples 数组的结尾
        inserts = []
        for known_tag in ["实体计数", "通用统计"]:
            tag_pos = text.find(f'"{known_tag}"')
            if tag_pos > 0:
                # 找该 tag 的最后一个 }
                after_tag = text[tag_pos:]
                last_close = after_tag.rfind("},")
                if last_close > 0:
                    abs_pos = tag_pos + last_close + 1
                    inserts.append((abs_pos, known_tag))

        if inserts:
            # 选最靠后的 tag
            inserts.sort(key=lambda x: x[0], reverse=True)
            pos, tag = inserts[0]
            new_entry = f'\n        {{"question": "{question}", "sql": "{sql}"}},'
            text = text[:pos] + new_entry + text[pos:]
            target.write_text(text, encoding="utf-8")
            return f"已添加 few-shot 示例: {question[:40]}"
        else:
            return "未找到合适的插入位置"
    except Exception as e:
        return f"添加 few-shot 失败: {e}"


def _apply_condition_type(change: dict) -> str:
    """添加条件类型到 intent_schemas.py"""
    try:
        type_name = change.get("type_name", "")
        if not type_name:
            return "缺少 type_name"

        target = Path(__file__).resolve().parent.parent / "backend" / "intent_schemas.py"
        if not target.exists():
            target = Path(__file__).resolve().parent / "intent_schemas.py"
        if not target.exists():
            return "找不到 intent_schemas.py"

        text = target.read_text(encoding="utf-8")
        if type_name in text:
            return f"条件类型已存在，跳过: {type_name}"

        # 在 CONFIDENCE 枚举值之后插入
        import re as _re
        m = _re.search(r'(CONFIDENCE\s*=\s*"confidence".*?)\n', text)
        if m:
            insert_pos = m.end()
            comment = change.get("comment", "")
            new_entry = f'    {type_name.upper()} = "{type_name}"  # {comment}\n'
            text = text[:insert_pos] + text[insert_pos:] + new_entry
            target.write_text(text, encoding="utf-8")
            return f"已添加条件类型: {type_name}"
        return "未找到插入位置"
    except Exception as e:
        return f"添加条件类型失败: {e}"


def _apply_renderer_map(change: dict) -> str:
    """添加渲染映射到 sql_renderer.py"""
    try:
        type_name = change.get("type_name", "")
        if not type_name:
            return "缺少 type_name"

        target = Path(__file__).resolve().parent.parent / "backend" / "sql_renderer.py"
        if not target.exists():
            target = Path(__file__).resolve().parent / "sql_renderer.py"
        if not target.exists():
            return "找不到 sql_renderer.py"

        text = target.read_text(encoding="utf-8")
        if f'"{type_name}"' in text:
            return f"渲染映射已存在，跳过: {type_name}"

        # 在最后一个映射项前插入（在已存在项最后）
        import re as _re
        # 找 CONDITION_SQL_MAP 的最后一个条目
        m = _re.search(r'"(time_range|commercial)".*?__DYNAMIC__', text)
        if m:
            insert_pos = m.end()
            sql_template = change.get("sql_template", f"--- {type_name} 需人工确认 ---")
            new_entry = f'\n    "{type_name}": "{sql_template}",'
            text = text[:insert_pos] + new_entry + text[insert_pos:]
            target.write_text(text, encoding="utf-8")
            return f"已添加渲染映射: {type_name}"
        return "未找到插入位置"
    except Exception as e:
        return f"添加渲染映射失败: {e}"


def apply_proposal(proposal: dict) -> list[str]:
    """自动执行修复方案中的 proposed_changes"""
    results = []
    changes = proposal.get("proposed_changes", [])

    for i, change in enumerate(changes):
        action = change.get("action", "")

        if action == "add_few_shot":
            results.append(f"  [{i+1}] {_apply_few_shot(change)}")
        elif action == "add_condition_type":
            results.append(f"  [{i+1}] {_apply_condition_type(change)}")
        elif action == "add_renderer_map":
            results.append(f"  [{i+1}] {_apply_renderer_map(change)}")
        elif action in ("update_prompt_rule", "fix_template"):
            file = change.get("file", "?")
            note = change.get("note", "需人工处理")
            results.append(f"  [{i+1}] ⚠️ {action} → {file}: {note}")
        else:
            results.append(f"  [{i+1}] ⚠️ 未知动作: {action}")

    return results


def scan_and_analyze() -> list[dict]:
    """完整流程：扫描→分组→分析→输出方案"""
    pending = scan_pending()
    if not pending:
        return []

    print("[Analyzer] 发现 " + str(len(pending)) + " 条待处理事件")

    groups = group_similar(pending)
    print("[Analyzer] 分组后共 " + str(len(groups)) + " 个错误模式")

    proposals = []
    for group in groups:
        proposal = generate_proposal(group)
        if proposal:
            save_proposal(proposal)
            proposals.append(proposal)

            for inc in group:
                mark_analyzed(inc.get("incident_id", ""))

            # 将分析结果同步写入 SQLite（管理后台可见 readable 的分析）
            _sync_analysis_to_sqlite(proposal, group)

            # 自动执行修复方案（测试环境）
            if proposal.get("proposed_changes"):
                fix_results = apply_proposal(proposal)
                print("  [AutoFix] 修复执行结果:")
                for r in fix_results:
                    print(f"    {r}")

    return proposals


def list_proposals(status: str = "pending_review") -> list[dict]:
    """列出所有待审核的修复方案"""
    proposals = []
    if not _PROPOSAL_DIR.exists():
        return proposals

    for f in sorted(_PROPOSAL_DIR.glob("prop_*.json")):
        try:
            with open(f, "r") as fh:
                prop = json.load(fh)
            if prop.get("status") == status:
                proposals.append(prop)
        except (json.JSONDecodeError, OSError):
            continue

    return proposals


def approve_proposal(proposal_id: str) -> bool:
    """标记方案为已批准"""
    for f in _PROPOSAL_DIR.glob("prop_*.json"):
        try:
            with open(f, "r") as fh:
                prop = json.load(fh)
            if prop.get("proposal_id") == proposal_id:
                prop["status"] = "approved"
                with open(f, "w") as fw:
                    json.dump(prop, fw, ensure_ascii=False, indent=2)
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False
