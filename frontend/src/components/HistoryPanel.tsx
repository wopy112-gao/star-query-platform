import { useState, useEffect, useRef } from 'react';
import { authHeaders } from '../api/auth';

interface HistoryItem {
  id: string;
  question: string;
  sql: string;
  elapsed_ms: number;
  created_at: string;
  success: boolean;
}

interface Props {
  onSelect: (question: string) => void;
  refreshKey: number;
  isMobile?: boolean;
}

const PAGE_LIMIT = 20;

export default function HistoryPanel({ onSelect, refreshKey, isMobile }: Props) {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [total, setTotal] = useState(0);
  const [keyword, setKeyword] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // 加载历史
  const fetchHistory = async (pageNum: number, kw: string, append: boolean) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(pageNum),
        limit: String(PAGE_LIMIT),
      });
      if (kw) params.set('keyword', kw);

      const res = await fetch(`/api/history?${params}`, {
        headers: authHeaders(),
      });
      const data = await res.json();

      if (append) {
        setItems(prev => [...prev, ...data.items]);
      } else {
        setItems(data.items);
      }
      setHasMore(data.has_more);
      setTotal(data.total);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  // 初始加载 & 刷新
  useEffect(() => {
    setPage(1);
    setKeyword('');
    setSearchInput('');
    fetchHistory(1, '', false);
  }, [refreshKey]);

  // 搜索
  const handleSearch = () => {
    setPage(1);
    setKeyword(searchInput);
    fetchHistory(1, searchInput, false);
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch();
  };

  // 加载更多
  const handleLoadMore = () => {
    const nextPage = page + 1;
    setPage(nextPage);
    fetchHistory(nextPage, keyword, true);
  };

  // 删除单条
  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setDeletingId(id);
    try {
      await fetch(`/api/history/${id}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      setItems(prev => prev.filter(item => item.id !== id));
      setTotal(prev => Math.max(0, prev - 1));
    } catch {
      // silent
    } finally {
      setDeletingId(null);
    }
  };

  // 清空全部
  const handleClearAll = async () => {
    if (!window.confirm('确定清空全部查询历史记录？此操作不可撤销。')) return;
    try {
      await fetch('/api/history', {
        method: 'DELETE',
        headers: authHeaders(),
      });
      setItems([]);
      setTotal(0);
      setHasMore(false);
    } catch {
      // silent
    }
  };

  // 点击历史项
  const handleSelect = (item: HistoryItem) => {
    if (item.success && item.question) {
      onSelect(item.question);
    }
  };

  // 格式化时间
  const formatTime = (dateStr: string) => {
    try {
      const d = new Date(dateStr);
      const h = d.getHours().toString().padStart(2, '0');
      const m = d.getMinutes().toString().padStart(2, '0');
      return `${h}:${m}`;
    } catch {
      return '';
    }
  };

  // 格式化日期（区分今天/昨天/更早）
  const formatDate = (dateStr: string) => {
    try {
      const d = new Date(dateStr);
      const now = new Date();
      const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
      const itemDate = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      if (itemDate === today) return '今天';
      const yesterday = new Date(now);
      yesterday.setDate(yesterday.getDate() - 1);
      const yStr = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, '0')}-${String(yesterday.getDate()).padStart(2, '0')}`;
      if (itemDate === yStr) return '昨天';
      return `${d.getMonth() + 1}/${d.getDate()}`;
    } catch {
      return '';
    }
  };

  return (
    <div className={`history-panel${isMobile ? ' mobile-open' : ''}`} ref={panelRef}>
      {/* 面板头部 */}
      <div className="history-header">
        <span className="history-title">📋 历史记录</span>
        <span className="history-count">{total > 0 ? `${total}条` : ''}</span>
      </div>

      {/* 搜索栏 */}
      <div className="history-search">
        <input
          type="text"
          className="history-search-input"
          placeholder="搜索历史..."
          value={searchInput}
          onChange={e => setSearchInput(e.target.value)}
          onKeyDown={handleSearchKeyDown}
        />
        {searchInput && (
          <button
            className="history-search-clear"
            onClick={() => { setSearchInput(''); setKeyword(''); fetchHistory(1, '', false); }}
          >
            ✕
          </button>
        )}
      </div>

      {/* 列表区 */}
      <div className="history-list">
        {items.length === 0 && !loading && (
          <div className="history-empty">
            {keyword ? '没有匹配的历史记录' : '还没有查询记录'}
          </div>
        )}

        {items.length > 0 && (
          <>
            {/* 今天/更早分组 */}
            {['今天', '昨天', '更早'].map(group => {
              const groupItems = items.filter(item => {
                const label = formatDate(item.created_at);
                if (group === '更早') return label !== '今天' && label !== '昨天';
                return label === group;
              });
              if (groupItems.length === 0) return null;

              return (
                <div key={group}>
                  <div className="history-date-group">{group}</div>
                  {groupItems.map(item => (
                    <div
                      key={item.id}
                      className={`history-item ${!item.success ? 'history-item-failed' : ''} ${deletingId === item.id ? 'history-item-deleting' : ''}`}
                      onClick={() => handleSelect(item)}
                      title={item.success ? '点击重新查询' : '该查询失败，无法重试'}
                    >
                      <span className="history-item-icon">
                        {item.success ? '✅' : '❌'}
                      </span>
                      <span className="history-item-text">
                        {item.question || '(空查询)'}
                      </span>
                      <span className="history-item-time">
                        {formatTime(item.created_at)}
                      </span>
                      <button
                        className="history-item-delete"
                        onClick={(e) => handleDelete(item.id, e)}
                        title="删除"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              );
            })}
          </>
        )}

        {/* 加载更多 */}
        {hasMore && (
          <button
            className="history-load-more"
            onClick={handleLoadMore}
            disabled={loading}
          >
            {loading ? '加载中...' : '加载更多 ↓'}
          </button>
        )}

        {/* 加载状态 */}
        {loading && items.length === 0 && (
          <div className="history-loading">
            <div className="history-loading-item" />
            <div className="history-loading-item" />
            <div className="history-loading-item" />
          </div>
        )}
      </div>

      {/* 清空按钮 */}
      {items.length > 0 && (
        <div className="history-footer">
          <button className="history-clear-btn" onClick={handleClearAll}>
            清空全部
          </button>
        </div>
      )}
    </div>
  );
}
