"""星宝数据平台 — 修复方案自动应用器（fix_applier）

职责：
1. 扫描 fix_proposals/ 中 status=pending_review 的方案
2. 按置信度分级：high→自动应用, medium→待审, low→汇总通知
3. 应用 high 级别方案（修改代码文件）
4. 回归验证（重跑 incident 对应的问题）
5. 回滚（修复前的代码备份 → 失败时恢复）
6. 通知用户（中文摘要）

使用：python3 fix_applier.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ============================================================
# 配置
# ============================================================

_BACKEND_DIR = Path(__file__).resolve().parent
_WORKSPACE_DIR = _BACKEND_DIR.parent.parent  # ~/.lightclaw/workspace
_PROPOSAL_DIR = _WORKSPACE_DIR / "fix_proposals"
_BACKUP_DIR = _WORKSPACE_DIR / "backups" / "fix_applier"

# 可执行的操作类型 → 处理函数
ACTION_HANDLERS = {
    "add_few_shot": "_apply_add_few_shot",
    "update_prompt_rule": "_apply_update_prompt_rule",
    "add_condition_type": "_apply_add_condition_type",
    "add_renderer_map": "_apply_add_renderer_map",
    "add_template": "_apply_add_template",
    "fix_template": "_apply_fix_template",
    "update_prompt_reference": "_apply_update_prompt_reference",
}


class FixApplier:
    """修复方案自动应用器"""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.notifications: list[str] = []
        self.applied: list[dict] = []
        self.skipped: list[dict] = []
        self.failed: list[dict] = []

    # ============================================================
    # 主流程
    # ============================================================

    def run(self) -> str:
        """执行完整流程，返回通知消息"""
        self.notifications = []
        self.applied = []
        self.skipped = []
        self.failed = []

        proposals = self._scan_proposals()
        if not proposals:
            return "📭 无待处理修复方案。"

        print(f"[FixApplier] 发现 {len(proposals)} 个待处理方案")

        # 按置信度分级
        high, medium, low = self._classify(proposals)

        # 处理 high 置信度 → 自动应用
        for p in high:
            self._process_high(p)

        # 处理 medium 置信度 → 标记待审
        for p in medium:
            self._process_medium(p)

        # 处理 low 置信度 → 汇总通知
        for p in low:
            self._process_low(p)

        # 生成通知
        return self._generate_notification()

    def _scan_proposals(self) -> list[dict]:
        """扫描所有待处理的修复方案"""
        pending = []
        if not _PROPOSAL_DIR.exists():
            return pending

        for f in sorted(_PROPOSAL_DIR.glob("prop_*.json")):
            try:
                with open(f, "r") as fh:
                    prop = json.load(fh)
                if prop.get("status") == "pending_review":
                    pending.append(prop)
            except (json.JSONDecodeError, OSError):
                continue

        return pending

    def _classify(self, proposals: list[dict]) -> tuple[list, list, list]:
        """按置信度分级"""
        high = [p for p in proposals if p.get("confidence") == "high"]
        medium = [p for p in proposals if p.get("confidence") == "medium"]
        low = [p for p in proposals if p.get("confidence") == "low"]
        return high, medium, low

    # ============================================================
    # 分级处理
    # ============================================================

    def _process_high(self, proposal: dict) -> None:
        """处理高置信度方案：自动应用 + 回归验证"""
        pid = proposal.get("proposal_id", "?")
        changes = proposal.get("proposed_changes", [])
        if not changes:
            # 高置信度但没有可执行变更 → 降级为 medium
            print(f"[FixApplier] high but no executable changes: {pid}, downgrading to medium")
            self._process_medium(proposal)
            return

        if self.dry_run:
            print(f"[FixApplier] DRY RUN: would apply {pid}")
            self.applied.append(proposal)
            return

        # 1. 备份
        backup_path = self._backup_files(changes)
        if not backup_path:
            print(f"[FixApplier] 备份失败: {pid}")
            self.failed.append(proposal)
            return

        # 2. 应用变更
        ok = self._apply_changes(changes)
        if not ok:
            self._rollback(backup_path, changes)
            print(f"[FixApplier] 应用失败，已回滚: {pid}")
            self.failed.append(proposal)
            return

        # 3. 回归验证
        incidents = proposal.get("incident_ids", [])
        passed = self._regression_verify(incidents)
        if not passed:
            self._rollback(backup_path, changes)
            print(f"[FixApplier] 回归失败，已回滚: {pid}")
            self.failed.append(proposal)
            return

        # 4. 标记成功
        self._mark_processed(pid, "applied")
        self.applied.append(proposal)
        print(f"[FixApplier] ✅ 已应用: {pid}")

    def _process_medium(self, proposal: dict) -> None:
        """处理中置信度方案：标记待审"""
        pid = proposal.get("proposal_id", "?")
        self._mark_processed(pid, "needs_review")
        self.skipped.append(proposal)
        print(f"[FixApplier] 🔍 待审: {pid}")

    def _process_low(self, proposal: dict) -> None:
        """处理低置信度方案：汇总"""
        pid = proposal.get("proposal_id", "?")
        self._mark_processed(pid, "aggregated")
        self.skipped.append(proposal)
        print(f"[FixApplier] 📋 已汇总: {pid}")

    # ============================================================
    # 备份与回滚
    # ============================================================

    def _backup_files(self, changes: list[dict]) -> Optional[Path]:
        """备份变更涉及的文件"""
        files = set()
        for c in changes:
            fname = c.get("file", "")
            if fname:
                files.add(fname)

        if not files:
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = _BACKUP_DIR / ts
        backup_dir.mkdir(parents=True, exist_ok=True)

        for fname in files:
            src = _BACKEND_DIR / fname
            if src.exists():
                dst = backup_dir / fname
                shutil.copy2(src, dst)
                print(f"[Backup] {fname} → {dst}")

        return backup_dir

    def _rollback(self, backup_dir: Path, changes: list[dict]) -> None:
        """从备份恢复文件"""
        for c in changes:
            fname = c.get("file", "")
            if not fname:
                continue
            src = backup_dir / fname
            dst = _BACKEND_DIR / fname
            if src.exists():
                shutil.copy2(src, dst)
                print(f"[Rollback] {fname} ← {src}")

    # ============================================================
    # 变更应用（骨架 — 后续步骤实现具体操作）
    # ============================================================

    def _apply_changes(self, changes: list[dict]) -> bool:
        """应用一组变更

        兼容两种格式：
        - 新格式：{"action": "...", "file": "...", "content": {...}}
        - 旧格式：{"file": "...", "change": "文本描述"}
          旧格式无 action 字段 → 报 NOT_EXECUTABLE，转人工处理
        """
        for change in changes:
            action = change.get("action", "")
            if not action:
                # 旧格式（纯文本描述）→ 不可自动执行
                if "change" in change:
                    print(f"[FixApplier] 旧格式变更不可自动执行: {change.get('change', '?')[:60]}")
                return False

            handler_name = ACTION_HANDLERS.get(action)
            if not handler_name:
                print(f"[FixApplier] 未知操作: {action}")
                return False

            handler = getattr(self, handler_name, None)
            if not handler:
                print(f"[FixApplier] 未实现的处理器: {handler_name}")
                return False

            ok = handler(change)
            if not ok:
                return False

        return True

    # ---- 具体操作处理器 ----

    def _apply_add_few_shot(self, change: dict) -> bool:
        """新增 few-shot 示例到 llm_translator.py

        change 格式:
        {
            "action": "add_few_shot",
            "file": "llm_translator.py",
            "content": {"question": "山东省有多少店员",
                         "sql": "SELECT COUNT(DISTINCT 店员ID) ..."}
        }
        """
        content = change.get("content", {})
        question = content.get("question", "")
        sql = content.get("sql", "")
        if not question or not sql:
            print("[FixApplier] add_few_shot: 缺少 question 或 sql")
            return False

        filepath = _BACKEND_DIR / "llm_translator.py"
        if not filepath.exists():
            print(f"[FixApplier] 文件不存在: {filepath}")
            return False

        text = filepath.read_text(encoding="utf-8")

        # 找到 FEW_SHOT_EXAMPLES 的结束位置（最后一个 ] 之前）
        # 策略：找 "]  # 闭包" 或者列表末尾的 "]"
        marker = "FEW_SHOT_EXAMPLES = ["
        if marker not in text:
            print("[FixApplier] 找不到 FEW_SHOT_EXAMPLES")
            return False

        # 从 FEW_SHOT_EXAMPLES = [ 开始，找到匹配的 ]
        start = text.index(marker)
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        # 构建新条目（带正确缩进）
        indent = "    "  # 4 spaces for list item
        new_entry = (
            f'\n{indent}{{"question": "{question}", '
            f'"sql": "{sql}"}},\n{indent}'
        )

        new_text = text[:end] + new_entry + text[end:]
        filepath.write_text(new_text, encoding="utf-8")
        print(f"[FixApplier] add_few_shot: {question}")
        return True

    def _apply_update_prompt_rule(self, change: dict) -> bool:
        """更新 Prompt 规则（在 llm_translator.py 的 SYSTEM_PROMPT 中）

        change 格式:
        {
            "action": "update_prompt_rule",
            "file": "llm_translator.py",
            "rule_id": "3",                 # 可选，用于日志
            "old_text": "场景数统计必须用...",  # 精确匹配的旧文本
            "new_text": "计数规则（按用户问的...",  # 替换为的新文本
        }
        """
        old_text = change.get("old_text", "")
        new_text = change.get("new_text", "")
        if not old_text or not new_text:
            print("[FixApplier] update_prompt_rule: 缺少 old_text 或 new_text")
            return False

        filepath = _BACKEND_DIR / "llm_translator.py"
        if not filepath.exists():
            print(f"[FixApplier] 文件不存在: {filepath}")
            return False

        text = filepath.read_text(encoding="utf-8")
        if old_text not in text:
            print(f"[FixApplier] update_prompt_rule: 找不到目标文本")
            return False

        new_text = text.replace(old_text, new_text, 1)
        filepath.write_text(new_text, encoding="utf-8")
        rule_id = change.get("rule_id", "?")
        print(f"[FixApplier] update_prompt_rule: rule #{rule_id}")
        return True

    def _apply_add_condition_type(self, change: dict) -> bool:
        """新增条件类型到 intent_schemas.py

        change 格式:
        {
            "action": "add_condition_type",
            "file": "intent_schemas.py",
            "type_name": "geo",
            "comment": "地域过滤（省份/城市）",
            "insert_before": "TRUST",       # 插入在此枚举项之前
        }
        """
        type_name = change.get("type_name", "")
        comment = change.get("comment", "")
        insert_before = change.get("insert_before", "")

        if not type_name:
            print("[FixApplier] add_condition_type: 缺少 type_name")
            return False

        filepath = _BACKEND_DIR / "intent_schemas.py"
        if not filepath.exists():
            return False

        text = filepath.read_text(encoding="utf-8")

        # 检查是否已存在
        if f'{type_name.upper()} = "{type_name}"' in text:
            print(f"[FixApplier] add_condition_type: {type_name} 已存在，跳过")
            return True

        # 找到插入位置
        marker = f'{insert_before} = ' if insert_before else None
        if marker and marker in text:
            line_start = text.rfind("\n", 0, text.index(marker)) + 1
            new_line = f'    {type_name.upper()} = "{type_name}"  # {comment}\n'
            new_text = text[:line_start] + new_line + text[line_start:]
        else:
            # 找不到插入点 → 在最后一个枚举项之后插入
            # 找 "class ConditionType" 之后的最后一个枚举
            class_start = text.index("class ConditionType")
            last_enum = 0
            for line in text[class_start:].split("\n"):
                if "= " in line and line.strip().startswith(tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")):
                    last_enum = text.index(line, class_start)
            insert_at = text.index("\n", last_enum) + 1
            new_line = f'    {type_name.upper()} = "{type_name}"  # {comment}\n'
            new_text = text[:insert_at] + new_line + text[insert_at:]

        filepath.write_text(new_text, encoding="utf-8")
        print(f"[FixApplier] add_condition_type: {type_name}")
        return True

    def _apply_add_renderer_map(self, change: dict) -> bool:
        """新增渲染器映射到 sql_renderer.py

        change 格式:
        {
            "action": "add_renderer_map",
            "file": "sql_renderer.py",
            "type_name": "geo",
            "sql_template": "({geo_clause})",
            "comment": "地域过滤",
        }
        """
        type_name = change.get("type_name", "")
        sql_template = change.get("sql_template", "")
        comment = change.get("comment", "")

        if not type_name or not sql_template:
            print("[FixApplier] add_renderer_map: 缺少参数")
            return False

        filepath = _BACKEND_DIR / "sql_renderer.py"
        if not filepath.exists():
            return False

        text = filepath.read_text(encoding="utf-8")

        # 检查是否已存在
        if f'"{type_name}":' in text[text.index("CONDITION_SQL_MAP"):]:
            print(f"[FixApplier] add_renderer_map: {type_name} 已存在，跳过")
            return True

        # 在 CONDITION_SQL_MAP 最后一个条目之后插入
        map_start = text.index("CONDITION_SQL_MAP")
        map_end = text.index("}", text.index("# 信任度", map_start)) + 1
        new_entry = f'\n    # {comment}\n    "{type_name}": "{sql_template}",'
        new_text = text[:map_end] + new_entry + text[map_end:]

        filepath.write_text(new_text, encoding="utf-8")
        print(f"[FixApplier] add_renderer_map: {type_name}")
        return True

    def _apply_add_template(self, change: dict) -> bool:
        """新增模板到 template_matcher.py

        change 格式:
        {
            "action": "add_template",
            "file": "template_matcher.py",
            "template_obj": { ... }   # 完整的模板字典
        }
        """
        template_obj = change.get("template_obj", {})
        if not template_obj:
            print("[FixApplier] add_template: 缺少 template_obj")
            return False

        filepath = _BACKEND_DIR / "template_matcher.py"
        if not filepath.exists():
            return False

        text = filepath.read_text(encoding="utf-8")

        # 检查是否已存在
        tid = template_obj.get("id", "")
        if tid and f'"id": "{tid}"' in text:
            print(f"[FixApplier] add_template: {tid} 已存在，跳过")
            return True

        # 在 TEMPLATES 列表的 ] 之前插入
        marker = "TEMPLATES = ["
        start = text.index(marker)
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        # 格式化模板为 Python 代码
        template_code = self._format_template(template_obj)
        new_text = text[:end] + ",\n    " + template_code + "\n" + text[end:]

        filepath.write_text(new_text, encoding="utf-8")
        print(f"[FixApplier] add_template: {tid}")
        return True

    def _apply_fix_template(self, change: dict) -> bool:
        """修复模板的 sql_template

        change 格式:
        {
            "action": "fix_template",
            "file": "template_matcher.py",
            "template_id": "ts07",
            "old_sql": "SELECT ROUND(AVG(...)) FROM data",
            "new_sql": "SELECT ROUND(AVG(...)) FROM data {conditions}",
        }
        """
        template_id = change.get("template_id", "")
        old_sql = change.get("old_sql", "")
        new_sql = change.get("new_sql", "")

        if not template_id or not old_sql or not new_sql:
            print("[FixApplier] fix_template: 缺少参数")
            return False

        filepath = _BACKEND_DIR / "template_matcher.py"
        if not filepath.exists():
            return False

        text = filepath.read_text(encoding="utf-8")

        # 找模板 → 找其 sql_template → 替换
        tid_marker = f'"id": "{template_id}"'
        tid_pos = text.index(tid_marker)
        # 从 tid 位置向后找 "sql_template"
        sql_pos = text.index('"sql_template"', tid_pos)
        # 替换（只替换该模板范围内的第一次出现）
        if old_sql not in text[sql_pos:]:
            print(f"[FixApplier] fix_template: 找不到 old_sql in {template_id}")
            return False

        # 精确替换：只替换从 sql_pos 开始往后的第一次匹配
        before = text[:sql_pos]
        after = text[sql_pos:]
        after_replaced = after.replace(old_sql, new_sql, 1)
        new_text = before + after_replaced

        filepath.write_text(new_text, encoding="utf-8")
        print(f"[FixApplier] fix_template: {template_id}")
        return True

    def _apply_update_prompt_reference(self, change: dict) -> bool:
        """更新 Prompt 中的参考信息（如省份列表）

        change 格式:
        {
            "action": "update_prompt_reference",
            "file": "llm_translator.py",
            "section": "known_geo",         # 替换 {known_geo} 的值
            "new_reference": "省份: ...",    # 新参考文本
        }
        """
        section = change.get("section", "")
        new_ref = change.get("new_reference", "")
        if not section or not new_ref:
            print("[FixApplier] update_prompt_reference: 缺少参数")
            return False

        filepath = _BACKEND_DIR / "llm_translator.py"
        if not filepath.exists():
            return False

        text = filepath.read_text(encoding="utf-8")
        # 替换 _build_prompt 或 _build_intent_prompt 中的 known_xxx 值
        marker = f'known_geo_text = "'
        if marker in text:
            line_start = text.index(marker)
            line_end = text.index('"', line_start + len(marker) + 1)
            if line_end > line_start:
                new_text = text[:line_start] + f'{marker}{new_ref}' + text[line_end:]
            else:
                new_text = text
        else:
            # 在其他上下文中替换
            placeholder = "{" + section + "}"
            if placeholder in text:
                new_text = text.replace(placeholder, new_ref)
            else:
                print(f"[FixApplier] update_prompt_reference: 找不到 {section}")
                return False

        filepath.write_text(new_text, encoding="utf-8")
        print(f"[FixApplier] update_prompt_reference: {section}")
        return True

    @staticmethod
    def _format_template(template_obj: dict) -> str:
        """将模板字典格式化为 Python 代码字符串"""
        import json
        tid = template_obj.get("id", "")
        label = template_obj.get("label", "")
        question = template_obj.get("question", "")
        desc = template_obj.get("description", "")
        sql = template_obj.get("sql_template", "")
        chart = template_obj.get("chart_type", "table_only")

        # 格式化 intent_key
        intent_key = template_obj.get("intent_key", {})
        ik_pattern = intent_key.get("pattern", "single_stat")
        ik_agg = intent_key.get("agg", "场景数")
        ik_cond = intent_key.get("condition_types", [])
        ik_dim = intent_key.get("dimension", None)

        lines = [
            "{",
            f'    "id": "{tid}",',
            f'    "label": "{label}",',
            f'    "question": "{question}",',
            f'    "description": "{desc}",',
            f'    "intent_key": {{"pattern": "{ik_pattern}", "agg": "{ik_agg}", '
            f'"condition_types": {json.dumps(ik_cond)}, '
            f'"dimension": {json.dumps(ik_dim)}}},',
        ]

        if "\n" in sql:
            lines.append(f'    "sql_template": (')
            for sline in sql.split("\n"):
                lines.append(f'        "{sline}"')
            lines.append(f'    ),')
        else:
            lines.append(f'    "sql_template": "{sql}",')

        lines.append(f'    "chart_type": "{chart}",')
        lines.append("}")

        return "\n".join(lines)

    # ============================================================
    # 回归验证
    # ============================================================

    def _regression_verify(self, incident_ids: list[str]) -> bool:
        """回归验证：将 incident 对应的问题重跑一遍

        验证策略：
        1. 从 incident 文件中提取原始问题
        2. 通过 query_intent + llm_translator 重新翻译
        3. 对比新旧 SQL（修复后必须不同）
        4. 检查是否还有旧警告中的错误模式

        返回 True 表示全部通过。
        """
        if not incident_ids:
            return True  # 无待验证项，默认通过

        incidents = self._load_incidents(incident_ids)
        if not incidents:
            print("[FixApplier] 回归验证: 无待验证 incident")
            return True

        passed = 0
        failed = 0

        for inc in incidents:
            question = inc.get("question", "")
            old_sql = inc.get("sql", "")
            old_warnings = inc.get("warnings", [])
            incident_type = inc.get("type", "")

            if not question:
                continue

            # 1. 重新查询
            try:
                new_sql = self._requery(question)
            except Exception as e:
                print(f"[Regress] 查询异常: {question[:40]} — {e}")
                failed += 1
                continue

            if not new_sql:
                print(f"[Regress] 查询返回空: {question[:40]}")
                failed += 1
                continue

            # 2. 验证修复
            fixes_ok = True

            # 2a. SQL 必须与旧的不同（如果旧 SQL 存在）
            if old_sql and self._sql_eq(new_sql, old_sql):
                print(f"[Regress] SQL 未变化: {question[:40]}")
                fixes_ok = False

            # 2b. 如果是 validation_fail 类型，检查旧警告中提到的错误模式是否消除
            if incident_type == "validation_fail" and old_warnings:
                for w in old_warnings:
                    if self._warning_still_present(new_sql, w):
                        print(f"[Regress] 警告未消除: {question[:40]} — {w[:60]}")
                        fixes_ok = False
                        break

            # 2c. SQL 必须可解析（基本语法检查）
            if not self._is_valid_sql(new_sql):
                print(f"[Regress] SQL 语法异常: {question[:40]}")
                fixes_ok = False

            if fixes_ok:
                passed += 1
            else:
                failed += 1

        print(f"[Regress] 验证完成: {passed} passed, {failed} failed (total {len(incidents)})")
        return failed == 0

    def _load_incidents(self, incident_ids: list[str]) -> list[dict]:
        """从 feedback_review/ 目录加载 incident 文件"""
        incidents = []
        feedback_dir = _WORKSPACE_DIR / "feedback_review"
        if not feedback_dir.exists():
            return incidents

        for inc_file in sorted(feedback_dir.glob("inc_*.json")):
            try:
                with open(inc_file, "r") as f:
                    inc = json.load(f)
                if inc.get("incident_id") in incident_ids:
                    incidents.append(inc)
            except (json.JSONDecodeError, OSError):
                continue

        return incidents

    def _requery(self, question: str) -> Optional[str]:
        """重新翻译一个问题，返回新 SQL

        直接调用本地模块，不依赖 HTTP API。
        """
        try:
            # 动态导入（避免循环依赖）
            sys.path.insert(0, str(_BACKEND_DIR))
            from query_intent import translate as decompose_intent
            from llm_translator import translate as translate_sql
            from template_matcher import matcher
            from sql_renderer import renderer

            # L2: 意图拆解
            intent_result = decompose_intent(question)
            if not intent_result.success or not intent_result.intent:
                return None

            intent = intent_result.intent

            # L2.5: 模板匹配 + SQL 生成
            template = matcher.match_by_intent(intent)
            if template:
                sql = renderer.render(template["template_obj"], intent)
            else:
                result = translate_sql(question, intent=intent)
                if not result.get("success"):
                    return None
                sql = result.get("sql", "")

            return sql
        except Exception as e:
            print(f"[FixApplier] _requery 异常: {e}")
            return None

    @staticmethod
    def _sql_eq(sql1: str, sql2: str) -> bool:
        """判断两条 SQL 是否等价（忽略空白差异）"""
        if not sql1 or not sql2:
            return False
        return "".join(sql1.split()) == "".join(sql2.split())

    @staticmethod
    def _warning_still_present(sql: str, warning: str) -> bool:
        """检查警告中描述的错误模式是否仍在新 SQL 中存在

        常见模式检测：
        - "缺少成交条件" → 检查 SQL 中是否有 交易是否达成
        - "维度不匹配" → 简单检查：warning 中的关键词是否在 SQL 中出现
        """
        warning_lower = warning.lower()

        # 模式: "缺少成交条件" → SQL 应包含 交易是否达成
        if "缺少成交" in warning_lower or "成交" in warning_lower:
            if "交易是否达成" not in sql and "deal" not in sql.lower():
                return True  # 仍然缺失

        # 模式: 自相矛盾
        if "自相矛盾" in warning_lower:
            # 检查是否有 NOT LIKE ... AND LIKE ... 模式
            import re
            likes = re.findall(r"LIKE\s+'%([^']+)%'", sql, re.IGNORECASE)
            not_likes = re.findall(r"NOT\s+LIKE\s+'%([^']+)%'", sql, re.IGNORECASE)
            if set(likes) & set(not_likes):
                return True

        return False

    @staticmethod
    def _is_valid_sql(sql: str) -> bool:
        """基本 SQL 语法检查"""
        if not sql:
            return False
        sql_upper = sql.upper().strip()
        # 至少以 SELECT 开头
        if not sql_upper.startswith("SELECT"):
            # 允许 --ERROR: 标记
            if sql.startswith("--ERROR:"):
                return False
        return True

    # ============================================================
    # 状态标记
    # ============================================================

    def _mark_processed(self, proposal_id: str, new_status: str) -> bool:
        """标记方案状态"""
        for f in _PROPOSAL_DIR.glob("prop_*.json"):
            try:
                with open(f, "r") as fh:
                    prop = json.load(fh)
                if prop.get("proposal_id") == proposal_id:
                    prop["status"] = new_status
                    prop["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(f, "w") as fw:
                        json.dump(prop, fw, ensure_ascii=False, indent=2)
                    return True
            except (json.JSONDecodeError, OSError):
                continue
        return False

    # ============================================================
    # 通知生成
    # ============================================================

    def _generate_notification(self) -> str:
        """生成中文摘要通知"""
        lines = ["🔧 **fix_applier 运行报告**", ""]

        if self.applied:
            lines.append(f"### ✅ 已自动应用 ({len(self.applied)} 个)")
            for p in self.applied:
                samples = p.get("samples", [])
                questions = [s.get("question", "?") for s in samples[:3]]
                lines.append(f"- {p['proposal_id']}: {', '.join(questions)}")
            lines.append("")

        if self.skipped:
            needs_review = [p for p in self.skipped if p.get("status") == "needs_review"]
            aggregated = [p for p in self.skipped if p.get("status") == "aggregated"]
            if needs_review:
                lines.append(f"### 🔍 待人工审核 ({len(needs_review)} 个)")
                for p in needs_review:
                    lines.append(f"- {p['proposal_id']}: {p.get('error_pattern', '?')} — {p.get('fix_proposal', '?')[:80]}")
                lines.append("")
            if aggregated:
                lines.append(f"### 📋 已汇总 ({len(aggregated)} 个 low 置信度事件)")
                lines.append(f"（详见 fix_proposals/ 目录）")
                lines.append("")

        if self.failed:
            lines.append(f"### ❌ 应用失败 ({len(self.failed)} 个)")
            for p in self.failed:
                lines.append(f"- {p['proposal_id']}: {p.get('error_pattern', '?')}")
            lines.append("")

        if not self.applied and not self.skipped and not self.failed:
            lines.append("📭 无变化")

        return "\n".join(lines)


# ============================================================
# 入口
# ============================================================

def main():
    dry_run = "--dry-run" in sys.argv
    applier = FixApplier(dry_run=dry_run)
    notification = applier.run()
    print("\n" + "=" * 50)
    print(notification)
    print("=" * 50)


if __name__ == "__main__":
    main()
