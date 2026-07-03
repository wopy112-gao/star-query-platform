/** 管理后台 — 反馈事件管理 */

import { useState, useEffect, useCallback } from 'react';
import { authHeaders } from '../api/auth';

interface Incident {
  id: string;
  type: string;
  env: string;
  status: string;
  question: string;
  sql: string;
  error: string;
  warnings: string[];
  history_id: string;
  feedback_comment: string;
  root_cause: string;
  fix_proposal: string;
  resolved_at: string | null;
  resolver: string;
  created_at: string;
  fix_status: string;
  fix_attempted_at: string;
  verification_note: string;
}

interface IncidentListResponse {
  items: Incident[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

interface IncidentStats {
  total: number;
  pending: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
}

const TYPE_LABELS: Record<string, string> = {
  validation_fail: '校验失败',
  user_dislike: '用户踩',
};

const TYPE_ICONS: Record<string, string> = {
  validation_fail: '🔴',
  user_dislike: '👎',
};

const STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  analyzing: '分析中',
  resolved: '已处理',
  wontfix: '已忽略',
};

const ENV_LABELS: Record<string, string> = {
  prod: '正式',
  test: '测试',
};

const FIX_STATUS_LABELS: Record<string, string> = {
  '': '未执行',
  queue_fix: '⏳ 排队中',
  fix_applying: '修复中…',
  fix_verified: '✅ 已验证',
  fix_failed: '❌ 失败',
  fix_done: '✅ 已修复',
};

export default function AdminIncidents() {
  // 列表状态
  const [items, setItems] = useState<Incident[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<IncidentStats | null>(null);

  // 筛选状态
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [envFilter, setEnvFilter] = useState('');
  const [keyword, setKeyword] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  // 选中状态
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [selectAll, setSelectAll] = useState(false);

  // 展开详情
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<Incident | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 操作状态
  const [operating, setOperating] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const limit = 20;

  // ===== 加载数据 =====
  const fetchIncidents = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), limit: String(limit) });
      if (typeFilter) params.set('type', typeFilter);
      if (statusFilter) params.set('status', statusFilter);
      if (envFilter) params.set('env', envFilter);
      if (keyword) params.set('keyword', keyword);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo);

      const res = await fetch(`/api/admin/incidents?${params}`, { headers: authHeaders() });
      const data: IncidentListResponse = await res.json();
      setItems(data.items);
      setTotal(data.total);
      setPage(data.page);
      setHasMore(data.has_more);
      setSelectedIds(new Set());
      setSelectAll(false);
    } catch {
      // 静默
    } finally {
      setLoading(false);
    }
  }, [typeFilter, statusFilter, envFilter, keyword, dateFrom, dateTo]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/api/admin/incidents/stats', { headers: authHeaders() });
      const data: IncidentStats = await res.json();
      setStats(data);
    } catch {
      // 静默
    }
  }, []);

  useEffect(() => {
    fetchIncidents(1);
    fetchStats();
  }, [fetchIncidents, fetchStats]);

  // ===== 筛选搜索 =====
  const handleSearch = () => {
    setPage(1);
    fetchIncidents(1);
  };

  // ===== 展开详情 =====
  const toggleExpand = async (inc: Incident) => {
    if (expandedId === inc.id) {
      setExpandedId(null);
      setExpandedDetail(null);
      return;
    }
    setExpandedId(inc.id);
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/admin/incidents/${inc.id}`, { headers: authHeaders() });
      const data: Incident = await res.json();
      setExpandedDetail(data);
    } catch {
      setExpandedDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  // ===== 选择逻辑 =====
  const toggleSelect = (id: string) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelectedIds(next);
  };

  const toggleSelectAll = () => {
    if (selectAll) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(items.map(i => i.id)));
    }
    setSelectAll(!selectAll);
  };

  // ===== 批量操作 =====
  const batchStatus = async (status: string, label: string) => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    if (!window.confirm(`确定将选中的 ${ids.length} 条事件标记为「${label}」吗？`)) return;

    setOperating(true);
    try {
      const res = await fetch('/api/admin/incidents/batch-status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ ids, status }),
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(`已标记 ${data.updated} 条事件为「${label}」`);
        setTimeout(() => setSuccessMsg(''), 3000);
        fetchIncidents(page);
        fetchStats();
      }
    } catch {
      // 静默
    } finally {
      setOperating(false);
    }
  };

  const batchDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    if (!window.confirm(`确定要删除选中的 ${ids.length} 条事件吗？此操作不可撤销！`)) return;

    setOperating(true);
    try {
      const res = await fetch('/api/admin/incidents/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ ids }),
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(`已删除 ${data.deleted} 条事件`);
        setTimeout(() => setSuccessMsg(''), 3000);
        fetchIncidents(1);
        fetchStats();
      }
    } catch {
      // 静默
    } finally {
      setOperating(false);
    }
  };

  // ===== 单条操作 =====
  const updateStatus = async (id: string, status: string, label: string) => {
    if (!window.confirm(`确定将此事件标记为「${label}」吗？`)) return;
    setOperating(true);
    try {
      const res = await fetch(`/api/admin/incidents/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ status }),
      });
      if (res.ok) {
        setSuccessMsg(`事件已标记为「${label}」`);
        setTimeout(() => setSuccessMsg(''), 3000);
        fetchIncidents(page);
        fetchStats();
        // 如果详情展开，重新加载
        if (expandedId === id) {
          setExpandedDetail(null);
          setExpandedId(null);
        }
      }
    } catch {
      // 静默
    } finally {
      setOperating(false);
    }
  };

  const reanalyze = async (id: string) => {
    setOperating(true);
    try {
      await fetch(`/api/admin/incidents/${id}/analyze`, {
        method: 'POST',
        headers: authHeaders(),
      });
      setSuccessMsg(`AI 分析完成`);
      setTimeout(() => setSuccessMsg(''), 3000);
      // 刷新详情
      if (expandedId === id) {
        const detailRes = await fetch(`/api/admin/incidents/${id}`, { headers: authHeaders() });
        const detailData = await detailRes.json();
        setExpandedDetail(detailData);
      }
      fetchIncidents(page);
    } catch {
      // 静默
    } finally {
      setOperating(false);
    }
  };

  const handleApplyFix = async (id: string) => {
    if (!window.confirm('确定让 Moss 执行修复方案吗？\n\n流程：\n1. Moss 读取修复方案\n2. 在测试环境执行代码变更\n3. 重启测试服务 + 回归验证\n4. 在本会话中汇报结果\n\nMoss 执行完成后你会看到详细结果。')) return;
    setOperating(true);
    try {
      const res = await fetch(`/api/admin/incidents/${id}/apply-fix`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(`✅ 修复申请已提交，Moss 即将执行。请查看对话区。`);
      } else {
        setSuccessMsg(`❌ 提交失败: ${data.detail || '未知错误'}`);
      }
      setTimeout(() => setSuccessMsg(''), 6000);
      // 刷新详情
      if (expandedId === id) {
        const detailRes = await fetch(`/api/admin/incidents/${id}`, { headers: authHeaders() });
        const detailData = await detailRes.json();
        setExpandedDetail(detailData);
      }
      fetchIncidents(page);
      fetchStats();
    } catch {
      setSuccessMsg('❌ 网络异常，请重试');
      setTimeout(() => setSuccessMsg(''), 3000);
    } finally {
      setOperating(false);
    }
  };

  const deleteSingle = async (id: string) => {
    if (!window.confirm('确定要删除此事件吗？')) return;
    setOperating(true);
    try {
      const res = await fetch(`/api/admin/incidents/${id}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (res.ok) {
        setSuccessMsg('事件已删除');
        setTimeout(() => setSuccessMsg(''), 3000);
        if (expandedId === id) { setExpandedId(null); setExpandedDetail(null); }
        fetchIncidents(page);
        fetchStats();
      }
    } catch {
      // 静默
    } finally {
      setOperating(false);
    }
  };

  // ===== 导出 CSV =====
  const exportCSV = () => {
    const params = new URLSearchParams();
    if (typeFilter) params.set('type', typeFilter);
    if (statusFilter) params.set('status', statusFilter);
    if (envFilter) params.set('env', envFilter);
    const url = `/api/admin/incidents/export?${params}`;
    window.open(url, '_blank');
  };

  // ===== 渲染状态颜色 =====
  const statusClass = (s: string) => {
    switch (s) {
      case 'pending': return 'status-pending';
      case 'analyzing': return 'status-analyzing';
      case 'resolved': return 'status-resolved';
      case 'wontfix': return 'status-wontfix';
      default: return '';
    }
  };

  // ===== 渲染 =====
  return (
    <div className="admin-content">
      {/* 顶栏 */}
      <div className="admin-toolbar">
        <h2 className="admin-page-title">⚠️ 反馈事件</h2>
        <div className="admin-toolbar-right">
          <button className="admin-btn-secondary" onClick={exportCSV}>
            📥 导出 CSV
          </button>
        </div>
      </div>

      {/* 成功提示 */}
      {successMsg && <div className="admin-toast success">{successMsg}</div>}

      {/* 统计卡片 */}
      {stats && (
        <div className="incident-stats-bar">
          <div className="incident-stat">
            <span className="incident-stat-value">{stats.total}</span>
            <span className="incident-stat-label">总计</span>
          </div>
          <div className="incident-stat highlight">
            <span className="incident-stat-value">{stats.pending}</span>
            <span className="incident-stat-label">待处理</span>
          </div>
          {Object.entries(stats.by_status).filter(([k]) => k !== 'pending').map(([k, v]) => (
            <div key={k} className="incident-stat">
              <span className="incident-stat-value">{v}</span>
              <span className="incident-stat-label">{STATUS_LABELS[k] || k}</span>
            </div>
          ))}
        </div>
      )}

      {/* 筛选栏 */}
      <div className="incident-filters">
        <select
          className="incident-filter-select"
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value)}
        >
          <option value="">全部类型</option>
          <option value="validation_fail">🔴 校验失败</option>
          <option value="user_dislike">👎 用户踩</option>
        </select>

        <select
          className="incident-filter-select"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
        >
          <option value="">全部状态</option>
          <option value="pending">⏳ 待处理</option>
          <option value="analyzing">🔄 分析中</option>
          <option value="resolved">✅ 已处理</option>
          <option value="wontfix">⏭️ 已忽略</option>
        </select>

        <select
          className="incident-filter-select"
          value={envFilter}
          onChange={e => setEnvFilter(e.target.value)}
        >
          <option value="">全部环境</option>
          <option value="prod">正式</option>
          <option value="test">测试</option>
        </select>

        <input
          type="date"
          className="incident-filter-date"
          value={dateFrom}
          onChange={e => setDateFrom(e.target.value)}
          placeholder="开始日期"
        />
        <span className="incident-filter-sep">~</span>
        <input
          type="date"
          className="incident-filter-date"
          value={dateTo}
          onChange={e => setDateTo(e.target.value)}
          placeholder="结束日期"
        />

        <input
          type="text"
          className="incident-filter-input"
          placeholder="搜索问题关键词..."
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
        />
        <button className="admin-btn-primary incident-filter-btn" onClick={handleSearch}>
          筛选
        </button>

        {(typeFilter || statusFilter || envFilter || keyword || dateFrom || dateTo) && (
          <button
            className="incident-filter-clear"
            onClick={() => {
              setTypeFilter(''); setStatusFilter(''); setEnvFilter('');
              setKeyword(''); setDateFrom(''); setDateTo('');
              setPage(1);
              setTimeout(() => fetchIncidents(1), 0);
            }}
          >
            清除筛选
          </button>
        )}
      </div>

      {/* 批量操作栏 */}
      {selectedIds.size > 0 && (
        <div className="incident-batch-bar">
          <span className="incident-batch-info">已选 {selectedIds.size} 条</span>
          <button
            className="incident-batch-btn approve"
            onClick={() => batchStatus('resolved', '已处理')}
            disabled={operating}
          >
            ✅ 标记已处理
          </button>
          <button
            className="incident-batch-btn ignore"
            onClick={() => batchStatus('wontfix', '已忽略')}
            disabled={operating}
          >
            ⏭️ 标记忽略
          </button>
          <button
            className="incident-batch-btn danger"
            onClick={batchDelete}
            disabled={operating}
          >
            🗑️ 批量删除
          </button>
        </div>
      )}

      {/* 列表 */}
      <div className="incident-table-meta">
        共 <strong>{total}</strong> 条事件
      </div>

      <div className="incident-list">
        {loading ? (
          <div className="incident-loading">加载中...</div>
        ) : items.length === 0 ? (
          <div className="incident-empty">暂无匹配事件</div>
        ) : (
          items.map(inc => (
            <div key={inc.id} className={`incident-card${expandedId === inc.id ? ' expanded' : ''}`}>
              {/* 卡片头部 */}
              <div className="incident-card-header">
                {/* 复选框 */}
                <label className="incident-checkbox">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(inc.id)}
                    onChange={() => toggleSelect(inc.id)}
                  />
                  <span className="incident-checkbox-custom" />
                </label>

                {/* 类型标签 */}
                <span className={`incident-type-tag type-${inc.type}`}>
                  {TYPE_ICONS[inc.type] || '📋'} {TYPE_LABELS[inc.type] || inc.type}
                </span>

                {/* 状态标签 */}
                <span className={`incident-status-tag ${statusClass(inc.status)}`}>
                  {STATUS_LABELS[inc.status] || inc.status}
                </span>

                {/* 环境 */}
                <span className="incident-env-tag">
                  {ENV_LABELS[inc.env] || inc.env}
                </span>

                {/* 修复状态 */}
                {inc.fix_status && (
                  <span className={`incident-fix-status-tag fix-${inc.fix_status}`}>
                    {FIX_STATUS_LABELS[inc.fix_status] || inc.fix_status}
                  </span>
                )}

                {/* 问题摘要 */}
                <span className="incident-question" title={inc.question}>
                  {inc.question.length > 50 ? inc.question.slice(0, 50) + '...' : inc.question}
                </span>

                {/* 展开按钮 */}
                <button
                  className="incident-expand-btn"
                  onClick={() => toggleExpand(inc)}
                >
                  {expandedId === inc.id ? '收起 ▲' : '详情 ▼'}
                </button>
              </div>

              {/* 卡片元信息 */}
              <div className="incident-card-meta">
                <span className="incident-meta-id">{inc.id}</span>
                <span className="incident-meta-time">{inc.created_at}</span>
                {inc.resolved_at && (
                  <span className="incident-meta-resolved">
                    已处理: {inc.resolved_at} by {inc.resolver}
                  </span>
                )}
              </div>

              {/* 展开详情 */}
              {expandedId === inc.id && (
                <div className="incident-detail">
                  {detailLoading ? (
                    <div className="incident-detail-loading">加载详情...</div>
                  ) : expandedDetail ? (
                    <>
                      {/* 完整问题 */}
                      <div className="incident-detail-section">
                        <div className="incident-detail-label">📝 问题原文</div>
                        <div className="incident-detail-text">{expandedDetail.question}</div>
                      </div>

                      {/* 反馈备注 */}
                      {expandedDetail.feedback_comment && (
                        <div className="incident-detail-section">
                          <div className="incident-detail-label">💬 用户备注</div>
                          <div className="incident-detail-text">{expandedDetail.feedback_comment}</div>
                        </div>
                      )}

                      {/* SQL */}
                      {expandedDetail.sql && (
                        <div className="incident-detail-section">
                          <div className="incident-detail-label">🔧 SQL 语句</div>
                          <pre className="incident-detail-sql">{expandedDetail.sql}</pre>
                        </div>
                      )}

                      {/* 错误信息 */}
                      {expandedDetail.error && (
                        <div className="incident-detail-section">
                          <div className="incident-detail-label">❌ 错误信息</div>
                          <div className="incident-detail-error">{expandedDetail.error}</div>
                        </div>
                      )}

                      {/* Warnings */}
                      {expandedDetail.warnings && expandedDetail.warnings.length > 0 && (
                        <div className="incident-detail-section">
                          <div className="incident-detail-label">⚠️ 质量门禁警告</div>
                          <ul className="incident-detail-warnings">
                            {expandedDetail.warnings.map((w, i) => (
                              <li key={i}>{w}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* 根因分析 */}
                      {expandedDetail.root_cause && (
                        <div className="incident-detail-section">
                          <div className="incident-detail-label">🧠 根因分析</div>
                          <div className="incident-detail-analysis">{expandedDetail.root_cause}</div>
                        </div>
                      )}

                      {/* 修复建议 */}
                      {expandedDetail.fix_proposal && (
                        <div className="incident-detail-section">
                          <div className="incident-detail-label">🔧 修复建议</div>
                          <div className="incident-detail-fix">{expandedDetail.fix_proposal}</div>
                        </div>
                      )}

                      {/* 修复执行状态 */}
                      {(expandedDetail.fix_status || expandedDetail.verification_note) && (
                        <div className="incident-detail-section">
                          <div className="incident-detail-label">
                            🛠️ 修复执行
                            <span className={`incident-fix-status-tag fix-${expandedDetail.fix_status}`}>
                              {FIX_STATUS_LABELS[expandedDetail.fix_status] || expandedDetail.fix_status}
                            </span>
                          </div>
                          {expandedDetail.fix_attempted_at && (
                            <div className="incident-detail-meta">
                              执行时间: {expandedDetail.fix_attempted_at}
                            </div>
                          )}
                          {expandedDetail.verification_note && (
                            <pre className="incident-detail-fix">{expandedDetail.verification_note}</pre>
                          )}
                        </div>
                      )}

                      {/* 关联信息 */}
                      <div className="incident-detail-section">
                        <div className="incident-detail-label">🔗 关联信息</div>
                        <div className="incident-detail-links">
                          {expandedDetail.history_id && (
                            <span>历史记录: {expandedDetail.history_id}</span>
                          )}
                        </div>
                      </div>

                      {/* 操作按钮 */}
                      <div className="incident-detail-actions">
                        {inc.status === 'analyzed' && !inc.fix_status && (
                          <button
                            className="incident-action-btn fix"
                            onClick={() => handleApplyFix(inc.id)}
                            disabled={operating}
                          >
                            🛠️ 执行修复
                          </button>
                        )}
                        {inc.fix_status === 'fix_failed' && (
                          <button
                            className="incident-action-btn fix"
                            onClick={() => handleApplyFix(inc.id)}
                            disabled={operating}
                          >
                            🔄 重试修复
                          </button>
                        )}
                        {inc.status !== 'resolved' && (
                          <button
                            className="incident-action-btn approve"
                            onClick={() => updateStatus(inc.id, 'resolved', '已处理')}
                            disabled={operating}
                          >
                            ✅ 标记已处理
                          </button>
                        )}
                        {inc.status !== 'wontfix' && (
                          <button
                            className="incident-action-btn ignore"
                            onClick={() => updateStatus(inc.id, 'wontfix', '已忽略')}
                            disabled={operating}
                          >
                            ⏭️ 标记忽略
                          </button>
                        )}
                        {inc.status !== 'pending' && (
                          <button
                            className="incident-action-btn reopen"
                            onClick={() => updateStatus(inc.id, 'pending', '待处理')}
                            disabled={operating}
                          >
                            🔄 重新打开
                          </button>
                        )}
                        <button
                          className="incident-action-btn analyze"
                          onClick={() => reanalyze(inc.id)}
                          disabled={operating}
                        >
                          🤖 AI 重新分析
                        </button>
                        <button
                          className="incident-action-btn danger"
                          onClick={() => deleteSingle(inc.id)}
                          disabled={operating}
                        >
                          🗑️ 删除
                        </button>
                      </div>
                    </>
                  ) : (
                    <div className="incident-detail-loading">加载失败</div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* 分页 + 全选 */}
      <div className="incident-footer">
        <label className="incident-checkbox select-all">
          <input
            type="checkbox"
            checked={selectAll && items.length > 0}
            onChange={toggleSelectAll}
            disabled={items.length === 0}
          />
          <span className="incident-checkbox-custom" />
          <span className="incident-select-all-label">全选本页</span>
        </label>

        <div className="admin-pagination">
          <button
            disabled={page <= 1}
            onClick={() => fetchIncidents(page - 1)}
          >
            ‹ 上一页
          </button>
          <span>第 {page}/{Math.max(1, Math.ceil(total / limit))} 页</span>
          <button
            disabled={!hasMore}
            onClick={() => fetchIncidents(page + 1)}
          >
            下一页 ›
          </button>
        </div>
      </div>
    </div>
  );
}
