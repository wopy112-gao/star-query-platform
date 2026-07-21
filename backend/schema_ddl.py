"""星宝语料场景查询系统 — Schema DDL 生成器

从 SCHEMA_KNOWLEDGE 自动生成 DuckDB DDL 字符串。
作为 LLM Prompt 的 Schema 描述（代替旧版自然语言字段列表）。

遵循 DAIL-SQL Code Representation (CR) 方式 — LLM 对 DDL 的理解力 > 自然语言描述。
"""

from schema_knowledge import SCHEMA_KNOWLEDGE


# 类型映射（Schema 知识中的类型 → DDL 显示类型）
# 保留原类型，仅 VARCHAR[] 等特殊情况做增强
TYPE_DISPLAY = {
    "BIGINT": "BIGINT",
    "VARCHAR": "VARCHAR",
    "BIGINT | VARCHAR": "BIGINT",  # 优先用 BIGINT
}

# 字段注释（增强描述）
FIELD_COMMENTS = {
    "疾病名称": "疾病全称（格式: 呼吸系统疾病-{子类}）",
    "场景ID": "每个独立购药场景的唯一ID",
    "ydate": "场景解析日期（格式: YYYY-MM-DD）",
    "顾客点名药品": "顾客点名购买的药品（JSON数组字符串，可用 CONTAINS 匹配）",
    "场景提及药品": "场景中提及的所有药品（JSON数组字符串）",
    "订单药品": "实际成交的药品（JSON数组字符串）",
    "省份": "药店所在省份",
    "城市": "药店所在城市",
    "交易是否达成": "成交标记: '是'/'否'",
    "是否问症": "药师是否问症: '是'/'否'",
    "是否联合用药": "药师是否推荐联合用药: '是'/'否'",
    "是否关键信息到达": "药师是否传递关键信息: '是'/'否'",
    "店员提及药品JSON": "药师在对话中说出的药品名（JSON数组）",
    "店员推荐药品JSON": "药师主动推荐给顾客的药品（JSON数组）",
    "联合用药动作": "联合推荐标记: 1=是/0=否",
    "是否商用": "商用标记: 1=是/0=否",
    "综合置信度评分": "数据整体置信度（0~1）",
    "用药人年龄分层": "用药人年龄段: 青壮年/老年人/儿童等",
    "切割完整度分值": "场景切割完整度（0~1）",
}


def _col_name_to_ddl(name: str) -> str:
    """将字段名转为合法的 DuckDB 列名

    特殊处理：包含空格/特殊字符的字段名不需要反引号，
    DuckDB 支持中文字段名直接使用。
    """
    return name


def _get_col_type(raw_type: str) -> str:
    """获取字段 DDL 类型"""
    return TYPE_DISPLAY.get(raw_type, raw_type)


def _get_col_comment(name: str) -> str:
    """获取字段注释（优先用增强描述，否则用 schema 中的 description）"""
    if name in FIELD_COMMENTS:
        return FIELD_COMMENTS[name]
    # fallback 到 schema_knowledge 中的 description
    for col in SCHEMA_KNOWLEDGE["columns"]:
        if col["name"] == name:
            return col["description"]
    return ""


def generate_ddl(include_comments: bool = True) -> str:
    """生成 DuckDB CREATE TABLE DDL 字符串

    Args:
        include_comments: 是否包含列注释（LLM Prompt 用，友好但较长）

    Returns:
        DDL 字符串，如:
        CREATE TABLE data (
            场景ID BIGINT,      -- 每个独立购药场景的唯一ID
            疾病名称 VARCHAR,    -- 疾病全称
            ...
        );
    """
    lines = [f"CREATE TABLE {SCHEMA_KNOWLEDGE['table_name']} ("]

    col_lines = []
    for col in SCHEMA_KNOWLEDGE["columns"]:
        name = _col_name_to_ddl(col["name"])
        dtype = _get_col_type(col["type"])
        comment = _get_col_comment(col["name"]) if include_comments else ""

        if comment:
            col_lines.append(f"  {name:18s} {dtype:10s} -- {comment}")
        else:
            col_lines.append(f"  {name:18s} {dtype}")

    lines.append(",\n".join(col_lines))
    lines.append(");")

    return "\n".join(lines)


def generate_ddl_compact() -> str:
    """生成精简版 DDL（不含注释，节省 tokens）

    用于 LLM Prompt 中当 tokens 紧张时使用。
    """
    return generate_ddl(include_comments=False)


def get_column_descriptions() -> str:
    """生成字段描述概览（比 DDL 更简短，用于非 SQL 场景）

    返回格式:
    ydate: 日期 yyyy-MM-dd
    省份: 药店所在省份
    """
    parts = []
    for col in SCHEMA_KNOWLEDGE["columns"]:
        comment = _get_col_comment(col["name"])
        if comment:
            parts.append(f"- {col['name']}: {comment}")
        else:
            parts.append(f"- {col['name']}: {col['description']}")
    return "\n".join(parts)


# ============================================================
# 预置 DDL 字符串（避免重复计算）
# ============================================================

DDL = generate_ddl()
DDL_COMPACT = generate_ddl_compact()


if __name__ == "__main__":
    print("=== 完整 DDL（含注释）===")
    print(DDL)
    print()
    print("=== 精简 DDL ===")
    print(DDL_COMPACT)
    print()
    print(f"完整版: {len(DDL)} chars")
    print(f"精简版: {len(DDL_COMPACT)} chars")
