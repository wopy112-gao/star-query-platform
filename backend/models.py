"""星宝语料场景查询系统 — Pydantic 数据模型"""

from typing import Any, Optional, Literal
from pydantic import BaseModel, Field


# ===== 认证 =====

class LoginRequest(BaseModel):
    username: str = Field(..., description="管理员用户名")
    password: str = Field(..., description="管理员密码")


class LoginResponse(BaseModel):
    token: str = Field(..., description="JWT Token")
    expires_in: int = Field(..., description="有效期（秒）")
    username: str = Field(..., description="用户名")


class VerifyResponse(BaseModel):
    valid: bool = Field(..., description="Token 是否有效")
    username: str = Field(..., description="用户名")


# ===== 查询 =====

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500, description="自然语言问题")
    page: Optional[int] = Field(None, ge=1, description="页码（None=不分页）")
    page_size: int = Field(50, ge=10, le=200, description="每页行数")


class SqlQueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=2000, description="SQL 语句")
    original_question: str = Field("", max_length=500, description="编辑 SQL 时的原始问题（用于缓存同步）")


class ExportRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500, description="自然语言问题，导出全部结果")


class QueryInfo(BaseModel):
    natural: str = Field(..., description="自然语言问题")
    sql: str = Field(..., description="实际执行的 SQL")
    source: Literal["template", "moss", "cache", "llm_intent", "template_recovered", "llm_recovered"] = Field(..., description="SQL 来源")
    elapsed_ms: float = Field(..., description="执行耗时（毫秒）")


class ConfidenceInfo(BaseModel):
    level: Literal["high", "medium", "low"] = Field(..., description="置信度等级")
    note: str = Field("", description="置信度说明")


class ExplanationItem(BaseModel):
    label: str = Field(..., description="条目名称")
    content: str = Field(..., description="条目内容")


class ExplanationInfo(BaseModel):
    sql: str = Field(..., description="SQL 语句")
    notes: list[ExplanationItem] = Field(default_factory=list, description="口径说明列表")


class ChartInfo(BaseModel):
    type: Literal["bar", "pie", "line", "grouped_bar", "table_only"] = Field(
        "table_only", description="图表类型"
    )
    option: Optional[dict[str, Any]] = Field(None, description="ECharts option")


class QueryResult(BaseModel):
    summary: str = Field("", description="结果摘要")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="数据行")
    total_rows: int = Field(0, description="结果行数")
    row_limit: int = Field(500, description="行数上限")
    truncated: bool = Field(False, description="是否被截断")
    pagination: Optional[dict[str, Any]] = Field(None, description="分页信息: {page, page_size, total_count, total_pages, has_prev, has_next}")


class IntentCondition(BaseModel):
    type: str = Field(..., description="条件类型")
    value: str = Field(..., description="条件值")
    relation: str = Field("AND", description="条件关系")


class IntentInfo(BaseModel):
    query_pattern: str = Field(..., description="查询模式")
    agg: str = Field(..., description="聚合方式")
    conditions: list[IntentCondition] = Field(default_factory=list, description="筛选条件")
    dimension: Optional[str] = Field(None, description="分组维度")
    route_source: str = Field(..., description="路由来源: template/cache/llm_intent/template_recovered")


class QueryResponse(BaseModel):
    success: bool = Field(True, description="是否成功")
    error: Optional[str] = Field(None, description="错误信息")
    query: Optional[QueryInfo] = None
    explanation: Optional[ExplanationInfo] = None
    result: Optional[QueryResult] = None
    chart: Optional[ChartInfo] = None
    confidence: Optional[ConfidenceInfo] = None
    hint: Optional[str] = Field(None, description="数据范围友好提示")
    warnings: list[str] = Field(default_factory=list, description="SQL 质量门禁警告列表")
    intent_info: Optional[IntentInfo] = Field(None, description="意图拆解结果")


# ===== Schema =====

class ColumnInfo(BaseModel):
    name: str = Field(..., description="列名")
    dtype: str = Field(..., description="数据类型")
    description: str = Field("", description="说明")
    sample: Any = Field(None, description="示例值")


class SchemaResponse(BaseModel):
    table_name: str = Field("data", description="表名")
    total_rows: int = Field(0, description="总行数")
    columns: list[ColumnInfo] = Field(default_factory=list, description="列信息")


# ===== 模板 =====

class TemplateItem(BaseModel):
    id: str = Field(..., description="模板 ID")
    label: str = Field(..., description="显示名称")
    question: str = Field(..., description="示例问题")
    description: str = Field("", description="说明")


# ===== 历史 =====

class HistoryItem(BaseModel):
    id: str = Field(..., description="历史记录 ID")
    question: str = Field(..., description="问题")
    sql: str = Field("", description="SQL")
    elapsed_ms: float = Field(0, description="耗时")
    created_at: str = Field("", description="时间")
    success: bool = Field(True, description="是否成功")


class HistoryResponse(BaseModel):
    items: list[HistoryItem] = Field(default_factory=list, description="历史记录")
    total: int = Field(0, description="总数")
    page: int = Field(1, description="页码")
    limit: int = Field(20, description="每页条数")
    has_more: bool = Field(False, description="是否还有更多")
    keyword: str = Field("", description="搜索关键词")


class DeleteHistoryResponse(BaseModel):
    success: bool = Field(True, description="是否成功")
    deleted: int = Field(0, description="删除条数")


# ===== 登录日志 =====

class LoginLogItem(BaseModel):
    id: str = Field(..., description="日志 ID")
    username: str = Field(..., description="用户名")
    ip_address: str = Field("", description="IP 地址")
    user_agent: str = Field("", description="浏览器/客户端")
    success: bool = Field(True, description="是否成功")
    detail: str = Field("", description="详细信息/失败原因")
    created_at: str = Field("", description="登录时间")


class LoginLogsResponse(BaseModel):
    items: list[LoginLogItem] = Field(default_factory=list, description="登录日志列表")
    total: int = Field(0, description="总数")
    page: int = Field(1, description="页码")
    limit: int = Field(20, description="每页条数")
    has_more: bool = Field(False, description="是否还有更多")


# ===== 反馈 =====

# ===== 密码修改 =====

class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128, description="当前密码")
    new_password: str = Field(..., min_length=4, max_length=128, description="新密码")


class ChangePasswordResponse(BaseModel):
    success: bool = Field(True, description="是否成功")
    message: str = Field("", description="提示信息")


# ===== 反馈 =====

class FeedbackRequest(BaseModel):
    history_id: str = Field(..., description="历史记录 ID")
    question: str = Field(..., description="查询问题")
    sentiment: Literal["like", "dislike"] = Field(..., description="赞/踩")
    comment: str = Field("", max_length=500, description="反馈说明")


class FeedbackResponse(BaseModel):
    success: bool = Field(True, description="是否成功")
    id: str = Field("", description="反馈记录 ID")
    sentiment: str = Field("", description="当前反馈状态")
    message: str = Field("", description="提示信息")


# ===== 洞察 =====

class TrendItem(BaseModel):
    date: str = Field(..., description="日期")
    count: int = Field(0, description="场景数")


class TopItem(BaseModel):
    name: str = Field(..., description="名称")
    count: int = Field(0, description="场景数")


class AlertItem(BaseModel):
    type: str = Field(..., description="告警类型")
    level: str = Field(..., description="告警级别: info/warning")
    message: str = Field(..., description="告警内容")


class TotalStats(BaseModel):
    total_scenes: int = Field(0, description="总场景数")
    today_scenes: int = Field(0, description="今日场景数")
    week_scenes: int = Field(0, description="本周场景数")
    close_rate: float = Field(0.0, description="成交率(%)")
    inquiry_rate: float = Field(0.0, description="问症率(%)")
    combo_rate: float = Field(0.0, description="联合用药率(%)")


class InsightsResponse(BaseModel):
    total: TotalStats = Field(default_factory=TotalStats, description="核心指标")
    trend: list[TrendItem] = Field(default_factory=list, description="近7日趋势")
    top_diseases: list[TopItem] = Field(default_factory=list, description="疾病TOP5")
    top_provinces: list[TopItem] = Field(default_factory=list, description="省份TOP5")
    alerts: list[AlertItem] = Field(default_factory=list, description="异常告警")
    date_range: dict[str, str] = Field(default_factory=dict, description="数据日期范围")
