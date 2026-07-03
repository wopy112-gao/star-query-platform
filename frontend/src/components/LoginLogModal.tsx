/** 星宝语料场景查询 — 登录日志 Modal */

import { useState, useRef, useEffect, useCallback } from 'react';
import { authHeaders } from '../api/auth';

interface LoginLogItem {
  id: string;
  username: string;
  ip_address: string;
  user_agent: string;
  success: boolean;
  detail: string;
  created_at: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  fontLarge: boolean;
}

export default function LoginLogModal({ open, onClose, fontLarge }: Props) {
  const [items, setItems] = useState<LoginLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [usernameFilter, setUsernameFilter] = useState('');
  const bodyRef = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(async (p: number, append: boolean = false) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), limit: '20' });
      if (usernameFilter.trim()) params.set('username', usernameFilter.trim());

      const res = await fetch(`/api/auth/login-logs?${params}`, {
        headers: authHeaders(),
      });
      const data = await res.json();
      if (data) {
        if (append) {
          setItems(prev => [...prev, ...data.items]);
        } else {
          setItems(data.items);
        }
        setTotal(data.total);
        setPage(data.page);
        setHasMore(data.has_more);
      }
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, [usernameFilter]);

  // 打开时加载
  useEffect(() => {
    if (open) {
      setPage(1);
      setItems([]);
      setUsernameFilter('');
      fetchLogs(1);
    }
  }, [open, fetchLogs]);

  // 按用户名过滤
  const handleFilter = () => {
    setPage(1);
    setItems([]);
    fetchLogs(1);
  };

  // 加载更多
  const handleLoadMore = () => {
    if (!hasMore || loading) return;
    fetchLogs(page + 1, true);
  };

  // ESC 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  // 滚动加载更多
  const handleScroll = useCallback(() => {
    if (!bodyRef.current || !hasMore || loading) return;
    const { scrollTop, scrollHeight, clientHeight } = bodyRef.current;
    if (scrollHeight - scrollTop - clientHeight < 60) {
      handleLoadMore();
    }
  }, [hasMore, loading, handleLoadMore]);

  if (!open) return null;

  return (
    <div className="dict-overlay" onClick={onClose}>
      <div
        className={`dict-modal login-log-modal${fontLarge ? ' font-large' : ''}`}
        onClick={e => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="dict-header">
          <span className="dict-title">🔐 登录日志</span>
          <span className="dict-title-count">共 {total} 条</span>
          <button className="dict-close" onClick={onClose}>✕</button>
        </div>

        {/* 过滤 */}
        <div className="dict-search">
          <input
            type="text"
            className="dict-search-input"
            placeholder="按用户名过滤..."
            value={usernameFilter}
            onChange={e => setUsernameFilter(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleFilter()}
          />
          {usernameFilter && (
            <button className="dict-search-clear" onClick={() => { setUsernameFilter(''); setPage(1); setItems([]); fetchLogs(1); }}>✕</button>
          )}
        </div>

        {/* 列表 */}
        <div
          className="dict-body"
          ref={bodyRef}
          onScroll={handleScroll}
        >
          {items.length === 0 && !loading && (
            <div className="dict-empty">暂无登录记录</div>
          )}

          <div className="log-list">
            {items.map(item => (
              <div key={item.id} className={`log-item${item.success ? '' : ' log-item-fail'}`}>
                <div className="log-item-header">
                  <span className="log-item-user">{item.username}</span>
                  <span className={`log-item-status ${item.success ? 'success' : 'fail'}`}>
                    {item.success ? '✅ 成功' : '❌ 失败'}
                  </span>
                  <span className="log-item-time">{item.created_at}</span>
                </div>
                <div className="log-item-detail">
                  <span className="log-item-ip">📡 {item.ip_address || '-'}</span>
                  {item.detail && (
                    <span className="log-item-reason">{item.detail}</span>
                  )}
                </div>
                {item.user_agent && (
                  <div className="log-item-ua" title={item.user_agent}>
                    {item.user_agent.length > 80
                      ? item.user_agent.slice(0, 80) + '...'
                      : item.user_agent}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* 加载更多 */}
          {loading && <div className="history-loading">加载中...</div>}
          {hasMore && !loading && (
            <button className="history-load-more" onClick={handleLoadMore}>
              加载更多（{items.length}/{total}）
            </button>
          )}
          {!hasMore && items.length > 0 && (
            <div className="history-end">已显示全部记录</div>
          )}
        </div>
      </div>
    </div>
  );
}
