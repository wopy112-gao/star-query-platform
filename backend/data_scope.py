"""星宝语料场景查询系统 — 数据范围检查与友好提示

用途：
1. 查询前：检测用户问题是否超出数据范围，给出友好提示
2. 查询后：当结果为空时，兜底检测并提示

运行逻辑：
1. 从 DuckDB 中提取所有已知疾病名称（动态自适应）
2. 构建已知关键词集合（底层标签 + 大类）
3. 人工标注明确不在数据中的领域关键词（按分类组织）
4. 检查用户问题中的关键词是否落在已知范围内
   - 命中范围内关键词 → 放行
   - 命中范围外关键词 → 提示
   - 不确定 → 放行
   - 同时命中范围内+范围外 → 范围内优先（不提示）
"""

import re
import threading
from typing import Optional

from sql_engine import engine


class DataScopeChecker:
    """数据范围检查器（单例）"""

    _instance: Optional["DataScopeChecker"] = None
    _loaded: bool = False
    _lock = threading.Lock()

    # 已知疾病/场景关键词（从 DuckDB 自动加载）
    known_keywords: set[str] = set()
    # 底层标签（最后一级）
    bottom_tags: set[str] = set()
    # 大类标签（第一级）
    top_tags: set[str] = set()

    # 人工标注、明确不在数据中的领域关键词（按分类分组）
    out_of_scope: dict[str, set[str]] = {}
    # 所有范围外关键词的平铺集合（快速查找用）
    _all_out_of_scope: set[str] = set()

    # 不触发提示的纯通用查询词
    generic_terms: set[str] = set()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self) -> None:
        """从 DuckDB 加载已知疾病关键词"""
        with self._lock:
            if self._loaded:
                return

            try:
                result = engine.execute("""
                    SELECT DISTINCT 疾病名称
                    FROM data
                    WHERE 疾病名称 IS NOT NULL AND 疾病名称 != ''
                """)

                disease_names: set[str] = set()
                bottom_tags: set[str] = set()
                top_tags: set[str] = set()

                for row in result["rows"]:
                    name = str(row.get("疾病名称", "")).strip()
                    if not name:
                        continue
                    disease_names.add(name)

                    parts = name.split("-")
                    top = parts[0].strip()
                    if top:
                        top_tags.add(top)
                    bottom = parts[-1].strip()
                    if bottom:
                        bottom_tags.add(bottom)

                # 构建已知关键词集合
                known = set()
                known.update(top_tags)
                known.update(bottom_tags)

                self.known_keywords = known
                self.bottom_tags = bottom_tags
                self.top_tags = top_tags

                # 人工标注的、明确不在数据中的领域关键词（按分类）
                self.out_of_scope = {
                    "心脑血管": {
                        "高血压", "高血脂", "冠心病", "心绞痛", "脑梗",
                        "脑血栓", "中风", "心脏病", "支架", "降压", "降脂",
                        "他汀", "阿托伐他汀", "瑞舒伐他汀", "硝酸甘油",
                        "速效救心丸", "复方丹参",
                    },
                    "内分泌代谢": {
                        "糖尿病", "血糖", "降糖", "胰岛素", "二甲双胍",
                        "痛风", "尿酸", "减肥", "瘦身", "减重", "燃脂",
                        "甲状腺", "甲亢", "甲减", "优甲乐",
                    },
                    "消化系统": {
                        "胃炎", "胃痛", "胃酸", "胃溃疡", "肠炎", "腹泻",
                        "便秘", "痔疮", "肝病", "乙肝", "脂肪肝",
                        "结石", "胆囊炎", "幽门螺杆菌", "奥美拉唑",
                        "吗丁啉", "达喜", "整肠生", "肠炎宁", "吗丁啉",
                    },
                    "泌尿生殖": {
                        "肾虚", "肾病", "肾炎", "肾衰竭", "尿毒症",
                        "肾功能不全", "前列腺", "前列腺炎", "前列腺增生",
                        "尿路感染", "阴道炎", "妇科炎症", "痛经",
                        "妇科", "月经", "不孕",
                    },
                    "骨关节": {
                        "骨折", "颈椎", "腰椎", "关节炎", "骨质疏松",
                        "风湿", "类风湿", "强直", "痛风石", "骨刺",
                        "骨质增生", "腰椎间盘", "颈椎病",
                    },
                    "皮肤科": {
                        "皮炎", "湿疹", "荨麻疹", "脚气", "灰指甲",
                        "痤疮", "痘痘", "烫伤", "外伤", "脱发",
                        "斑秃", "癣", "手足癣", "体癣",
                    },
                    "神经/精神": {
                        "失眠", "头痛", "头晕", "偏头痛", "神经痛",
                        "癫痫", "抑郁", "抑郁症", "焦虑", "焦虑症",
                        "面瘫", "帕金森", "阿尔茨海默", "渐冻症",
                        "神经衰弱", "安眠药", "褪黑素",
                    },
                    "肿瘤/癌症": {
                        "乳腺癌", "肺癌", "肝癌", "胃癌", "肠癌", "食道癌",
                        "胰腺癌", "肾癌", "膀胱癌", "前列腺癌", "宫颈癌",
                        "卵巢癌", "甲状腺癌", "骨癌", "白血病", "淋巴瘤",
                        "肿瘤", "癌症", "恶性肿瘤", "化疗", "放疗",
                        "靶向药", "免疫治疗",
                    },
                    "五官科（非呼吸）": {
                        "白内障", "青光眼", "结膜炎", "角膜炎",
                        "耳鸣", "口腔溃疡", "牙疼", "牙痛", "牙周炎",
                        "近视", "远视", "散光", "激光",
                    },
                    "免疫/罕见病": {
                        "红斑狼疮", "克罗恩", "强直性脊柱炎",
                    },
                    "其他": {
                        "贫血", "气血不足", "更年期", "补钙",
                        "维生素", "维生素D", "益生菌", "保健品",
                        "增强免疫", "补肾", "肾宝",
                    },
                }

                # 构建平铺集合
                all_out = set()
                for category, keywords in self.out_of_scope.items():
                    all_out.update(keywords)
                self._all_out_of_scope = all_out

                # 不触发提示的纯通用查询词
                self.generic_terms = {
                    "总场景", "总数量", "总数", "总计",
                    "场景数", "全部", "所有场景",
                    "今天场景", "本周场景", "本月场景",
                }

                self._loaded = True
                print(
                    f"[DataScope] 已加载: {len(disease_names)} 个疾病名, "
                    f"{len(top_tags)} 个大类, {len(bottom_tags)} 个底层标签, "
                    f"{len(self._all_out_of_scope)} 个范围外关键词"
                )

            except Exception as e:
                print(f"[DataScope] 加载失败: {e}")
                # 保底方案：使用内置关键词
                self.known_keywords = {
                    "感冒", "咳嗽", "鼻炎", "发烧", "头痛", "哮喘",
                    "咽炎", "支气管炎", "肺炎",
                }
                self._loaded = True

    def _find_matched_keywords(self, text: str, keyword_set: set[str]) -> list[str]:
        """在文本中查找匹配的关键词（最长优先，不重复匹配）"""
        t = text
        matched = []
        sorted_kws = sorted(keyword_set, key=len, reverse=True)
        for kw in sorted_kws:
            if kw and kw in t:
                matched.append(kw)
                t = t.replace(kw, "◇" * len(kw))
        return matched

    def _find_matched_with_category(
        self, text: str
    ) -> dict[str, set[str]]:
        """在文本中查找匹配的范围外关键词，返回 {分类: {关键词}}"""
        result: dict[str, set[str]] = {}
        t = text
        for category, keywords in self.out_of_scope.items():
            matched = set()
            sorted_kws = sorted(keywords, key=len, reverse=True)
            for kw in sorted_kws:
                if kw and kw in t:
                    matched.add(kw)
                    t = t.replace(kw, "◇" * len(kw))
            if matched:
                result[category] = matched
        return result

    def _is_purely_generic(self, question: str) -> bool:
        """判断问题是否纯通用查询，不涉及任何具体疾病关键词"""
        q = question.replace(" ", "")
        generic_found = any(g in q for g in self.generic_terms)
        if not generic_found:
            return False
        known = self._find_matched_keywords(q, self.known_keywords)
        out = self._find_matched_keywords(q, self._all_out_of_scope)
        return not known and not out

    def is_in_scope(self, question: str) -> bool:
        """判断用户问题是否在数据范围内

        逻辑：
        1. 纯通用查询 → 过（不拦截）
        2. 命中范围内关键词 → 过
        3. 命中范围外关键词 → 拦截
        4. 都不命中 → 过（不确定就不拦）
        """
        if not self._loaded:
            self.load()

        q_clean = question.replace(" ", "")

        # 纯通用查询 → 过
        if self._is_purely_generic(question):
            return True

        # 命中范围内关键词 → 过
        known_matched = self._find_matched_keywords(q_clean, self.known_keywords)
        if known_matched:
            return True

        # 命中范围外关键词 → 拦截
        out_matched = self._find_matched_keywords(q_clean, self._all_out_of_scope)
        if out_matched:
            return False

        # 不确定 → 过
        return True

    def get_hint(self, question: str) -> Optional[str]:
        """生成数据范围友好提示

        返回示例:
            "⚠️ 当前数据不包含「糖尿病、高血压」相关交易场景

        返回 None 表示无需提示（在范围内或不确定）
        """
        if not self._loaded:
            self.load()

        q_clean = question.replace(" ", "")

        # 1. 纯通用查询 → 不提示
        if self._is_purely_generic(question):
            return None

        # 2. 查找范围内关键词
        known_matched = self._find_matched_keywords(q_clean, self.known_keywords)

        # 3. 如果命中了范围内关键词 → 不提示（已确定有数据）
        if known_matched:
            return None

        # 4. 查找范围外关键词（按分类分组）
        out_matched = self._find_matched_with_category(q_clean)

        if not out_matched:
            return None  # 不确定 → 不提示

        # 5. 统计匹配数
        total_matched_kws = set()
        for _, kws in out_matched.items():
            total_matched_kws.update(kws)

        # 取前5个关键词展示
        sample_kws = sorted(total_matched_kws)[:5]
        kws_str = "、".join(sample_kws)
        suffix = "等" if len(total_matched_kws) > 5 else ""

        return (
            f"⚠️ 当前数据暂未覆盖「{kws_str}」{suffix}相关交易场景。\n"
            f"💡 如仍需查询，建议换用其他关键词，或确认数据是否已更新覆盖该领域。"
        )


# 全局单例
scope_checker = DataScopeChecker()
