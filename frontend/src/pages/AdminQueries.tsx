/** 管理后台 — 查询记录 */

import { useState, useEffect, useCallback } from 'react';
import { authHeaders } from '../api/auth';

interface QueryRecord {
  id: string;
  username: string;
  question: string;
  sql: string;
  elapsed_ms: number;
  created_at: string;
  success: boolean;
}

interface QueryListResponse {
  items: QueryRecord[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

interface Props {
  /** 从用户管理页钻取过来的默认用户名 */
  initialUsername?: string;
}

export default function AdminQueries({ initialUsername }: Props) {
  // 列表状态
  const [items, setItems] = useState<QueryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);

  // 筛选状态
  const [usernameFilter, setUsernameFilter] = useState(initialUsername || '');
  const [keyword, setKeyword] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  // 用户列表（筛选下拉用）
  const [userList, setUserList] = useState<string[]>([]);

  // 展开详情
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const limit = 20;

  // 加载用户列表（用于下拉筛选）
  useEffect(() => {
    fetch('/api/admin/users?limit=100', { headers: authHeaders() })
      .then(r => r.json())
      .then(data => {
        if (data.items) {
          setUserList(data.items.map((u: any) => u.username));
        }
      })
      .catch(() => {});
  }, []);

  // 加载查询记录
  const fetchQueries = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), limit: String(limit) });
      if (usernameFilter) params.set('username', usernameFilter);
      if (keyword) params.set('keyword', keyword);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo);

      const res = await fetch(`/api/admin/query-history?${params}`, { headers: authHeaders() });
      const data: QueryListResponse = await res.json();
      setItems(data.items);
      setTotal(data.total);
      setPage(data.page);
      setHasMore(data.has_more);
      setExpandedId(null);
    } catch {
      // 静默
    } finally {
      setLoading(false);
    }
  }, [usernameFilter, keyword, dateFrom, dateTo]);

  // 首次加载或有筛选变化时重置到第一页
  useEffect(() => {
    fetchQueries(1);
  }, [fetchQueries]);

  // 筛选搜索（重置页码）
  const handleSearch = () => {
    setKeyword(searchInput.trim());
    setPage(1);
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch();
  };

  // 翻页
  const prevPage = () => { if (page > 1) fetchQueries(page - 1); };
  const nextPage = () => { if (hasMore) fetchQueries(page + 1); };

  // 清除所有筛选
  const clearFilters = () => {
    setUsernameFilter('');
    setKeyword('');
    setSearchInput('');
    setDateFrom('');
    setDateTo('');
  };

  // 展开/收起详情
  const toggleExpand = (id: string) => {
    setExpandedId(expandedId === id ? null : id);
  };

  // 格式化毫秒
  const fmtMs = (ms: number) => {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  // 是否有活跃筛选条件
  const hasFilters = usernameFilter || keyword || dateFrom || dateTo;

  return (
    <div className="admin-content">
      <h2 className="admin-page-title">🔍 查询记录</h2>

      {/* 总统计 */}
      <div className="admin-table-meta">
        共 <strong>{total}</strong> 条查询记录
        {hasFilters && (
          <span className="admin-table-meta-filtered">
            （已筛选）
            <button className="admin-search-clear-inline" onClick={clearFilters}>✕ 清除筛选</button>
          </span>
        )}
      </div>

      {/* 筛选栏 */}
      <div className="incident-filters">
        {/* 用户筛选 */}
        <select
          className="incident-filter-select"
          value={usernameFilter}
          onChange={e => { setUsernameFilter(e.target.value); setPage(1); }}
        >
          <option value="">全部用户</option>
          {userList.map(u => (
            <option key={u} value={u}>{u}</option>
          ))}
        </select>

        {/* 关键词搜索 */}
        <div className="incident-filter-search">
          <input
            type="text"
            className="incident-filter-input"
            placeholder="搜索问题或 SQL..."
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={handleSearchKeyDown}
          />
          <button className="incident-filter-btn admin-btn-primary" onClick={handleSearch}>🔍</button>
          {keyword && (
            <button className="incident-filter-clear" onClick={() => { setKeyword(''); setSearchInput(''); }}>
              ✕
            </button>
          )}
        </div>

        {/* 日期范围 */}
        <div className="incident-filter-date">
          <input
            type="date"
            className="incident-filter-input"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
            placeholder="开始日期"
          />
          <span className="incident-filter-sep">~</span>
          <input
            type="date"
            className="incident-filter-input"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
            placeholder="结束日期"
          />
        </div>
      </div>

      {/* 查询记录列表 */}
      <div className="incident-list">
        {loading ? (
          <div className="incident-loading">加载中...</div>
        ) : items.length === 0 ? (
          <div className="incident-empty">暂无查询记录</div>
        ) : (
          items.map(q => (
            <div key={q.id} className={`incident-card${expandedId === q.id ? ' expanded' : ''}`}>
              {/* 卡片头部 */}
              <div className="incident-card-header" onClick={() => toggleExpand(q.id)}>
                {/* 成功/失败标识 */}
                <span className={`query-status-dot ${q.success ? 'success' : 'fail'}`}>
                  {q.success ? '✅' : '❌'}
                </span>

                {/* 用户名 */}
                <span className="query-username-tag">{q.username}</span>

                {/* 问题摘要 */}
                <span className="incident-question" title={q.question}>
                  {q.question.length > 60 ? q.question.slice(0, 60) + '...' : q.question}
                </span>

                {/* 耗时 */}
                <span className="query-elapsed">{fmtMs(q.elapsed_ms)}</span>

                {/* 展开 */}
                <button
                  className="incident-expand-btn"
                  onClick={e => { e.stopPropagation(); toggleExpand(q.id); }}
                >
                  {expandedId === q.id ? '收起 ▲' : '详情 ▼'}
                </button>
              </div>

              {/* 元信息 */}
              <div className="incident-card-meta">
                <span className="incident-meta-id">{q.id}</span>
                <span className="incident-meta-time">{q.created_at}</span>
              </div>

              {/* 展开详情 */}
              {expandedId === q.id && (
                <div className="incident-detail">
                  {/* 完整问题 */}
                  <div className="incident-detail-section">
                    <div className="incident-detail-label">📝 问题原文</div>
                    <div className="incident-detail-text">{q.question}</div>
                  </div>

                  {/* SQL */}
                  {q.sql && (
                    <div className="incident-detail-section">
                      <div className="incident-detail-label">🔧 SQL 语句</div>
                      <pre className="incident-detail-sql">{q.sql}</pre>
                    </div>
                  )}

                  {/* 详细信息 */}
                  <div className="incident-detail-section">
                    <div className="incident-detail-label">ℹ️ 详细信息</div>
                    <div className="incident-detail-text">
                      <div>👤 用户: {q.username}</div>
                      <div>⏱ 耗时: {fmtMs(q.elapsed_ms)}</div>
                      <div>🕐 时间: {q.created_at}</div>
                      <div>✅ 状态: {q.success ? '成功' : '失败'}</div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* 分页 */}
      {total > limit && (
        <div className="incident-footer" style={{ justifyContent: 'flex-end' }}>
          <div className="admin-pagination">
            <button disabled={page <= 1} onClick={prevPage}>‹ 上一页</button>
            <span>第 {page}/{Math.max(1, Math.ceil(total / limit))} 页</span>
            <button disabled={!hasMore} onClick={nextPage}>下一页 ›</button>
          </div>
        </div>
      )}
    </div>
  );
}
