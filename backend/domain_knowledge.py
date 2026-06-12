"""星宝语料场景查询系统 — 领域知识引擎

用途：判断用户查询中的关键是药品名还是疾病名，辅助 L1 模板准确分流。
数据源：从 drug-analysis-skill 的药品映射表和品牌匹配规则提取。

用法：
    from domain_knowledge import kb
    result = kb.classify("查询一下有感冒疾病")
    # → {"type": "disease", "keyword": "感冒", "matched_terms": ["感冒"]}
"""

import json
import re
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "domain_data.json"


class DomainKnowledge:
    """领域知识引擎"""

    def __init__(self):
        self._loaded = False
        self.drug_names: set[str] = set()
        self.brand_names: set[str] = set()
        self.disease_keywords: set[str] = set()
        self.stopwords: set[str] = set()
        self.city_names: set[str] = set()
        self.province_names: set[str] = set()

    def load(self, data_file: str | None = None) -> None:
        """加载领域知识数据"""
        file_path = Path(data_file) if data_file else DATA_FILE
        if not file_path.exists():
            print(f"[DomainKB] 数据文件不存在: {file_path}")
            self._init_fallback()
            return

        with open(file_path, "r") as f:
            data = json.load(f)

        self.drug_names = {n.lower() for n in data.get("drug_names", [])}
        self.brand_names = {n.lower() for n in data.get("brand_names", [])}
        self.disease_keywords = {k.lower() for k in data.get("disease_keywords", [])}
        self.stopwords = {s for s in data.get("stopwords", [])}
        self.city_names = {c.lower() for c in data.get("city_names", [])}
        self.province_names = {p.lower() for p in data.get("province_names", [])}
        self._loaded = True

        print(
            f"[DomainKB] 已加载: "
            f"{len(self.drug_names)} 药品名, "
            f"{len(self.disease_keywords)} 疾病关键词, "
            f"{len(self.city_names)} 城市名, "
            f"{len(self.province_names)} 省份名, "
            f"{len(self.stopwords)} 停用词"
        )

    def _init_fallback(self) -> None:
        """无数据文件时的最低保底知识"""
        self.drug_names = {
            "诺欣妥", "倍他乐克", "希舒美", "雷诺考特", "拜阿司匹灵",
            "美林", "辅舒良", "内舒拿", "信必可", "舒利迭",
        }
        self.disease_keywords = {
            "感冒", "咳嗽", "鼻炎", "发烧", "头痛",
            "咽炎", "支气管炎", "哮喘", "肺炎",
        }
        self.stopwords = {"查询", "统计", "有", "的", "一下", "疾病", "场景数"}
        self.city_names = {"北京", "上海", "广州", "深圳", "杭州"}
        self._loaded = True
        print(f"[DomainKB] 使用保底知识（{len(self.drug_names)} 药品, {len(self.disease_keywords)} 疾病）")

    def is_city(self, name: str) -> bool:
        """判断是否为已知城市名"""
        return name.lower() in self.city_names

    def is_province(self, name: str) -> bool:
        """判断是否为已知省份名（含简称）"""
        n = name.lower()
        # 精确匹配
        if n in self.province_names:
            return True
        # 处理 "山东省" → "山东" 的匹配
        if n.endswith("省") and n[:-1] in self.province_names:
            return True
        return False

    def get_province_candidates(self, name: str) -> list[str]:
        """根据用户输入的省份关键词，返回可能的省份全名"""
        n = name.lower()
        candidates = []
        # 精确匹配（如"山东省"）
        if n in self.province_names:
            candidates.append(n)
        # 简称匹配（如"山东"）
        for p in self.province_names:
            if p.endswith("省") and p[:-1] == n:
                candidates.append(p)
            if n.endswith("省") and p == n[:-1]:
                candidates.append(p)
        return candidates

    def is_geo_name(self, name: str) -> bool:
        """判断是否为已知地理名称（省份或城市）"""
        return self.is_city(name) or self.is_province(name)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def _clean_term(self, term: str) -> str:
        """清理提取的候选词：去停用词、去标点"""
        clean = term.lower().strip()
        for w in sorted(self.stopwords, key=len, reverse=True):
            clean = clean.replace(w, "")
        clean = re.sub(r"[^\u4e00-\u9fff\w]", "", clean).strip()
        return clean

    def classify(self, term: str) -> dict:
        """
        对查询词进行分类

        返回:
            {"type": "drug" | "disease" | "unknown",
             "keyword": str,           # 实际匹配到的关键术语
             "matched_terms": list,    # 所有命中的匹配项
             "cleaned": str}           # 去噪后的词
        """
        cleaned = self._clean_term(term)
        if not cleaned:
            return {"type": "unknown", "keyword": "", "matched_terms": [], "cleaned": term}

        matched_diseases = []
        matched_drugs = []

        # 1. 匹配疾病关键词（子串匹配即可，疾病词都是短词）
        for kw in self.disease_keywords:
            if kw in cleaned:
                matched_diseases.append(kw)

        # 2. 匹配药品名（词边界匹配，避免"感冒灵"误伤"感冒"查询）
        for drug in sorted(self.drug_names, key=len, reverse=True):
            # 短药品名（≤2字）必须完全等于 cleaned 才匹配
            if len(drug) <= 2:
                if drug == cleaned:
                    matched_drugs.append(drug)
            else:
                # 长药品名（≥3字）做子串匹配
                if drug in cleaned:
                    matched_drugs.append(drug)

        # 3. 决策逻辑
        #    有疾病匹配 + 无药品匹配 → disease
        #    有药品匹配 + 无疾病匹配 → drug
        #    两者都有 → 如果药品匹配是精确的（非子串误伤），药品优先；否则疾病优先
        if matched_diseases and not matched_drugs:
            return {
                "type": "disease",
                "keyword": matched_diseases[0],
                "matched_terms": matched_diseases,
                "cleaned": cleaned,
            }

        if matched_drugs and not matched_diseases:
            return {
                "type": "drug",
                "keyword": matched_drugs[0],
                "matched_terms": matched_drugs,
                "cleaned": cleaned,
            }

        if matched_drugs and matched_diseases:
            # 两者都命中 → 判断优先级
            # 如果是疾病关键词在 cleaned 中作为完整词出现 → 疾病优先
            # 特例：常见疾病词（感冒/咳嗽/鼻炎等）即使出现在药品名中，也优先按疾病处理
            disease_in_cleaned = any(
                kw == cleaned or kw in cleaned
                for kw in matched_diseases
            )
            if disease_in_cleaned:
                # 疾病词在 cleaned 中精确匹配 → 疾病优先
                return {
                    "type": "disease",
                    "keyword": matched_diseases[0],
                    "matched_terms": matched_drugs + matched_diseases,
                    "cleaned": cleaned,
                }
            # 疾病词不在 cleaned 中精确命中 → 药品优先
            return {
                "type": "drug",
                "keyword": matched_drugs[0],
                "matched_terms": matched_drugs + matched_diseases,
                "cleaned": cleaned,
            }

        return {
            "type": "unknown",
            "keyword": "",
            "matched_terms": [],
            "cleaned": cleaned,
        }


# 全局单例
kb = DomainKnowledge()
