/** 星宝语料场景查询 — 字段字典 Modal */

import { useState, useMemo, useRef, useEffect } from 'react';
import { DICTIONARY } from '../data/dictionary';
import type { DictItem } from '../data/dictionary';

interface Props {
  open: boolean;
  onClose: () => void;
  fontLarge: boolean;
}

const CATEGORIES = ['全部', '业务指标', '字段释义', '常见概念'] as const;

// 分组名称 — emoji 映射
const CAT_ICON: Record<string, string> = {
  '业务指标': '📊',
  '字段释义': '📄',
  '常见概念': '💡',
};

export default function DictionaryModal({ open, onClose, fontLarge }: Props) {
  const [search, setSearch] = useState('');
  const [tab, setTab] = useState<(typeof CATEGORIES)[number]>('全部');
  const inputRef = useRef<HTMLInputElement>(null);

  // 打开时自动聚焦搜索框，清空搜索/重置 tab
  useEffect(() => {
    if (open) {
      setSearch('');
      setTab('全部');
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  // ESC 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  // 过滤 + 排序
  const filtered = useMemo(() => {
    let items = DICTIONARY;

    // 分类过滤
    if (tab !== '全部') {
      items = items.filter(d => d.category === tab);
    }

    // 搜索过滤
    if (search.trim()) {
      const kw = search.trim().toLowerCase();
      items = items.filter(
        d =>
          d.term.toLowerCase().includes(kw) ||
          d.shortDesc.toLowerCase().includes(kw) ||
          d.detail.toLowerCase().includes(kw) ||
          (d.example && d.example.toLowerCase().includes(kw)),
      );
    }

    return items;
  }, [tab, search]);

  // 分组（搜索模式下不分组）
  const grouped = useMemo(() => {
    if (search.trim() || tab !== '全部') return null;
    const groups: Record<string, DictItem[]> = { 业务指标: [], 字段释义: [], 常见概念: [] };
    for (const d of DICTIONARY) {
      groups[d.category]?.push(d);
    }
    return groups;
  }, [search, tab]);

  if (!open) return null;

  return (
    <div className="dict-overlay" onClick={onClose}>
      <div
        className={`dict-modal${fontLarge ? ' font-large' : ''}`}
        onClick={e => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="dict-header">
          <span className="dict-title">📖 字段字典</span>
          <button className="dict-close" onClick={onClose}>✕</button>
        </div>

        {/* 搜索框 */}
        <div className="dict-search">
          <input
            ref={inputRef}
            type="text"
            className="dict-search-input"
            placeholder="🔍 搜索指标、字段或概念..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="dict-search-clear" onClick={() => setSearch('')}>✕</button>
          )}
        </div>

        {/* 分类 Tab */}
        {!search.trim() && (
          <div className="dict-tabs">
            {CATEGORIES.map(c => (
              <button
                key={c}
                className={`dict-tab${tab === c ? ' active' : ''}`}
                onClick={() => setTab(c)}
              >
                {c !== '全部' && CAT_ICON[c]} {c}
              </button>
            ))}
          </div>
        )}

        {/* 列表区 */}
        <div className="dict-body">
          {/* Tab 模式（不分group） */}
          {tab !== '全部' || search.trim() ? (
            filtered.length === 0 ? (
              <div className="dict-empty">未找到匹配的条目</div>
            ) : (
              <div className="dict-list">
                {filtered.map((item, i) => (
                  <DictCard key={`${item.term}-${i}`} item={item} />
                ))}
              </div>
            )
          ) : (
            /* 全部模式：按分类分组 */
            grouped && (
              <div className="dict-groups">
                {(['业务指标', '字段释义', '常见概念'] as const).map(cat => {
                  const items = grouped[cat];
                  if (!items || items.length === 0) return null;
                  return (
                    <div key={cat} className="dict-group">
                      <div className="dict-group-title">
                        {CAT_ICON[cat]} {cat}
                        <span className="dict-group-count">{items.length}</span>
                      </div>
                      <div className="dict-list">
                        {items.map((item, i) => (
                          <DictCard key={`${item.term}-${i}`} item={item} />
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )
          )}
        </div>

        {/* 底部统计 */}
        <div className="dict-footer">
          共 {filtered.length} 条
        </div>
      </div>
    </div>
  );
}

/** 单条字典卡片 */
function DictCard({ item }: { item: DictItem }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={`dict-card${expanded ? ' expanded' : ''}`}
      onClick={() => setExpanded(!expanded)}
    >
      <div className="dict-card-header">
        <span className="dict-card-term">{item.term}</span>
        <span className="dict-card-cat">{item.category}</span>
        <span className="dict-card-arrow">{expanded ? '▲' : '▼'}</span>
      </div>
      <div className="dict-card-short">{item.shortDesc}</div>
      {expanded && (
        <div className="dict-card-detail">
          <p>{item.detail}</p>
          {item.example && (
            <div className="dict-card-example">
              <span className="dict-example-label">示例：</span>
              {item.example}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
