"""星宝语料场景查询系统 — Schema 知识文件

用于 Moss 翻译自然语言为 SQL 时查询的表结构知识。
"""

SCHEMA_KNOWLEDGE = {
    "table_name": "data",
    "source": "/root/All_data_ch_full.parquet",
    "total_rows": 2381281,
    "columns": [
        {"name": "疾病名称", "type": "VARCHAR", "description": "进店顾客主诉疾病或购药对应疾病（呼吸系统分类）",
         "sample": "呼吸系统疾病-感冒", "keywords": ["感冒", "咳嗽", "鼻炎", "发烧", "头痛"]},
        {"name": "场景ID", "type": "BIGINT", "description": "每个独立购药场景的唯一ID，做去重计数时必须用 COUNT(DISTINCT 场景ID)"},
        {"name": "店员ID", "type": "BIGINT", "description": "药信系统上的店员ID"},
        {"name": "门店ID", "type": "BIGINT", "description": "药信系统上的门店ID"},
        {"name": "交易是否达成", "type": "VARCHAR", "description": "是否达成交易：是/否"},
        {"name": "交易失败原因", "type": "VARCHAR", "description": "未达成交易的原因"},
        {"name": "顾客点名药品", "type": "VARCHAR", "description": "顾客点名购买的药品(JSON数组字符串)，可用 CONTAINS 或 LIKE 模糊匹配"},
        {"name": "场景提及药品", "type": "VARCHAR", "description": "场景中提及的药品(JSON数组字符串)"},
        {"name": "订单药品", "type": "VARCHAR", "description": "实际成交的药品(JSON数组字符串)"},
        {"name": "是否问症", "type": "VARCHAR", "description": "药师是否问症：是/否"},
        {"name": "是否关键信息到达", "type": "VARCHAR", "description": "药师是否传递关键信息：是/否"},
        {"name": "问症表现", "type": "VARCHAR", "description": "药师问症的具体表现文本"},
        {"name": "关键信息表现", "type": "VARCHAR", "description": "药师传递关键信息的具体表现文本"},
        {"name": "订单达成表现", "type": "VARCHAR", "description": "交易达成的表现文本"},
        {"name": "患者关键信息", "type": "VARCHAR", "description": "顾客主诉核心信息(JSON数组)"},
        {"name": "场景时长", "type": "BIGINT", "description": "场景对话时长(秒)"},
        {"name": "会话ID", "type": "BIGINT", "description": "场景所属的原始会话编号，同一会话可能包含多个场景"},
        {"name": "会话开始时间", "type": "VARCHAR", "description": "场景所在会话的开始时间"},
        {"name": "场景从会话的开始时间", "type": "VARCHAR", "description": "场景在会话内的偏移开始时间(HH:mm:ss)"},
        {"name": "场景自然开始时间", "type": "VARCHAR", "description": "场景开始时间 yyyy-MM-dd HH:mm:ss"},
        {"name": "场景日期", "type": "VARCHAR", "description": "场景的日期"},
        {"name": "ydate", "type": "VARCHAR", "description": "日期 yyyy-MM-dd（同场景日期，推荐用此字段做时间维度过滤）"},
        {"name": "是否联合用药", "type": "VARCHAR", "description": "药师是否推荐联合用药：是/否"},
        {"name": "联合用药合理性", "type": "VARCHAR", "description": "联合用药是否合理：是/否"},
        {"name": "顾客性别", "type": "VARCHAR", "description": "顾客性别：男/女/不可识别"},
        {"name": "顾客年龄", "type": "VARCHAR", "description": "顾客年龄(混用：定性/区间/具体数字)"},
        {"name": "顾客信任度", "type": "VARCHAR", "description": "顾客对药师的信任评估"},
        {"name": "省份", "type": "VARCHAR", "description": "药店所在省份"},
        {"name": "城市", "type": "VARCHAR", "description": "药店所在城市"},
        {"name": "连锁", "type": "VARCHAR", "description": "药店所属连锁品牌"},
        {"name": "门店", "type": "VARCHAR", "description": "药店名称"},
        {"name": "是否场景下活动推荐", "type": "VARCHAR", "description": "药师是否推荐活动：是/否"},
        {"name": "活动是否参与", "type": "VARCHAR", "description": "顾客是否参与活动：是/否"},
        {"name": "活动时间占比", "type": "VARCHAR", "description": "活动耗时占场景比例(字符串)"},
        {"name": "活动满意度", "type": "VARCHAR", "description": "顾客对活动的满意度"},
        {"name": "活动介绍", "type": "VARCHAR", "description": "门店活动的主要信息文本"},
        {"name": "场景解析来源", "type": "VARCHAR", "description": "场景数据的解析来源：AI自动化/人工"},
        # ↓↓↓ 2026-06-11 新增字段 ↓↓↓
        {"name": "店员提及药品JSON", "type": "VARCHAR", "description": "对话中药师说出的药品名(JSON数组)"},
        {"name": "店员推荐药品JSON", "type": "VARCHAR", "description": "对话中药师主动推荐给顾客的药品(JSON数组)"},
        {"name": "用药人年龄分层", "type": "VARCHAR", "description": "用药人的大致年龄段，如：青壮年、老年人、儿童"},
        {"name": "联合用药动作", "type": "BIGINT", "description": "店员是否做了联合推荐动作：1=是,0=否（注意区别于'是否联合用药'，后者是AI判断结论）"},
        {"name": "推荐的联合用药JSON", "type": "VARCHAR", "description": "店员推荐联合用药的具体药品组合(JSON)"},
        {"name": "综合置信度评分", "type": "DOUBLE", "description": "音转文后的数据整体置信度评分，0~1之间，越高越好"},
        {"name": "场景完整度", "type": "VARCHAR", "description": "交易场景数据完整度分值"},
        {"name": "业务置信度", "type": "VARCHAR", "description": "业务层面的置信度评分"},
        {"name": "是否商用", "type": "BIGINT", "description": "数据是否达到商用标准：1=是,0=否（过滤高质量数据）"},
        {"name": "切割置信度分值", "type": "DOUBLE", "description": "对话切割算法的置信度分值，0~1之间"},
        {"name": "切割完整度分值", "type": "DOUBLE", "description": "对话切割算法的完整度分值，0~1之间"},
        # ↑↑↑ 新增字段结束 ↑↑↑
    ],
    "business_rules": [
        "场景ID去重：统计场景数必须用 COUNT(DISTINCT 场景ID)",
        "药品匹配：查询药品相关时，需同时检查 顾客点名药品、场景提及药品、订单药品 三个字段（用 LIKE 或 CONTAINS）",
        "疾病名称过滤：疾病名称格式为'呼吸系统疾病-{子类}'，筛选时用 LIKE '呼吸系统疾病-{关键词}%'",
        "交易达成：交易是否达成='是' 表示成交",
        "问症率 = COUNT(DISTINCT 场景ID WHERE 是否问症='是') / COUNT(DISTINCT 场景ID)",
        "联合用药率：建议用新字段'联合用药动作'='1' 计算实际联合推荐率，旧字段'是否联合用药'='是' 为AI判断结论",
        "数据质量：可用'是否商用'='1' 过滤高质量数据，用'综合置信度评分' 评估数据可靠性",
        "店员推荐分析：'店员推荐药品JSON' 记录主动推荐，'顾客点名药品' 记录顾客指名，两者对比可分析店员影响力",
        "年龄段分析：'用药人年龄分层' 可按青壮年/老年人/儿童等分层分析购药行为差异",
        "转化率：分子必须是分母的子集，确保 ≤100%",
    ],
    "query_tips": [
        "查询药品相关时，用 CONTAINS(字段名, '药品名') 或 LIKE '%药品名%'",
        "时间维度用 ydate 字段过滤，格式 'YYYY-MM-DD'",
        "地域维度用 省份、城市 字段",
        "做 TOP N 排序时用 ORDER BY ... DESC LIMIT N",
        "统计占比时用 COUNT(DISTINCT 场景ID) * 1.0 / (SELECT COUNT(DISTINCT 场景ID) FROM data)",
        "联合用药分析：优先用'联合用药动作'='1' 代表实际联合推荐行为",
        "店员推荐分析：`CONTAINS(店员推荐药品JSON, '药品名')` 可查哪些场景店员推荐了指定药品",
        "置信度过滤：`综合置信度评分` 和 `是否商用`='1' 可用于过滤高质量数据",
    ],
}


def get_schema_text() -> str:
    """获取 Schema 文本描述（供 Moss 翻译用）"""
    lines = [f"## 表: {SCHEMA_KNOWLEDGE['table_name']}"]
    lines.append(f"总行数: {SCHEMA_KNOWLEDGE['total_rows']}")
    lines.append("")
    lines.append("### 字段列表")
    for col in SCHEMA_KNOWLEDGE["columns"]:
        lines.append(f"- **{col['name']}** ({col['type']}): {col['description']}")
    lines.append("")
    lines.append("### 业务规则")
    for rule in SCHEMA_KNOWLEDGE["business_rules"]:
        lines.append(f"- {rule}")
    lines.append("")
    lines.append("### 查询技巧")
    for tip in SCHEMA_KNOWLEDGE["query_tips"]:
        lines.append(f"- {tip}")
    return "\n".join(lines)
