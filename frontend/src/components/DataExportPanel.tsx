import { useState, useEffect, useRef, useCallback } from 'react';
import { authHeaders } from '../api/auth';

// ===== 多选下拉组件 =====
function MultiSelect({ label, options, selected, onChange, placeholder }: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const filtered = options.filter(o =>
    o.toLowerCase().includes(search.toLowerCase())
  );

  const toggle = (v: string) => {
    if (selected.includes(v)) {
      onChange(selected.filter(x => x !== v));
    } else {
      onChange([...selected, v]);
    }
  };

  return (
    <div className="ms-wrapper" ref={ref}>
      <div className="ms-label">{label}</div>
      <div className="ms-trigger" onClick={() => setOpen(!open)}>
        {selected.length === 0
          ? <span className="ms-placeholder">{placeholder || `选择${label}`}</span>
          : <span className="ms-count">已选 {selected.length} 项</span>
        }
        <span className="ms-arrow">{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div className="ms-dropdown">
          <input
            className="ms-search"
            placeholder="搜索..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            autoFocus
          />
          <div className="ms-options">
            {filtered.length === 0 && <div className="ms-empty">无匹配</div>}
            {filtered.map(o => (
              <label key={o} className="ms-option">
                <input
                  type="checkbox"
                  checked={selected.includes(o)}
                  onChange={() => toggle(o)}
                />
                <span>{o}</span>
              </label>
            ))}
          </div>
          {selected.length > 0 && (
            <div className="ms-actions">
              <button onClick={() => onChange([])}>清除</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ===== 导出标签页 =====
export default function DataExportPanel() {
  const [filterOptions, setFilterOptions] = useState<any>(null);
  const [optionsLoading, setOptionsLoading] = useState(true);

  // 筛选条件
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [diseases, setDiseases] = useState<string[]>([]);
  const [products, setProducts] = useState<string[]>([]);
  const [productInput, setProductInput] = useState('');
  const [provinces, setProvinces] = useState<string[]>([]);
  const [chains, setChains] = useState<string[]>([]);
  const [cities, setCities] = useState<string[]>([]);
  const [confidence, setConfidence] = useState(0);
  const [isCommercial, setIsCommercial] = useState<string>('all');
  const [format, setFormat] = useState('parquet');

  // 预览 & 下载
  const [preview, setPreview] = useState<any>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState('');

  // 下载历史
  const [records, setRecords] = useState<any[]>([]);
  const [recordsTotal, setRecordsTotal] = useState(0);
  const [historyTab, setHistoryTab] = useState(false);

  // 加载筛选选项
  useEffect(() => {
    fetch('/api/data/filter-options', { headers: authHeaders() })
      .then(r => r.json())
      .then(d => {
        if (d.success) {
          setFilterOptions(d.data);
          // 默认置信度设为0.5
          const r = d.data.confidence_range;
          const mid = r ? Math.round((r.min + r.max) * 50) / 100 : 0.5;
          setConfidence(mid);
        }
      })
      .catch(() => {})
      .finally(() => setOptionsLoading(false));
  }, []);

  // 加载下载历史
  const loadRecords = useCallback(() => {
    fetch('/api/data/export/records?page=1&limit=10', { headers: authHeaders() })
      .then(r => r.json())
      .then(d => {
        setRecords(d.items || []);
        setRecordsTotal(d.total || 0);
      })
      .catch(() => {});
  }, []);

  useEffect(() => { loadRecords(); }, [loadRecords]);

  // 预览（防抖）
  const previewTimer = useRef<any>(null);
  const doPreview = useCallback(async () => {
    setPreviewLoading(true);
    try {
      const params: any = { format };
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      if (diseases.length) params.diseases = diseases;
      if (products.length) params.products = products;
      if (provinces.length) params.provinces = provinces;
      if (chains.length) params.chains = chains;
      if (cities.length) params.cities = cities;
      if (confidence > 0) params.confidence_min = confidence;
      if (isCommercial !== 'all') params.is_commercial = isCommercial === '1' ? 1 : 0;

      const res = await fetch('/api/data/export/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(params),
      });
      const data = await res.json();
      if (data.success) setPreview(data);
    } catch {} finally {
      setPreviewLoading(false);
    }
  }, [dateFrom, dateTo, diseases, products, provinces, chains, cities, confidence, isCommercial, format]);

  // 自动预览（防抖500ms）
  useEffect(() => {
    if (previewTimer.current) clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(doPreview, 500);
    return () => { if (previewTimer.current) clearTimeout(previewTimer.current); };
  }, [doPreview]);

  // 导出
  const handleExport = async () => {
    setDownloading(true);
    setDownloadProgress('正在导出数据...');
    try {
      const params: any = { format };
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      if (diseases.length) params.diseases = diseases;
      if (products.length) params.products = products;
      if (provinces.length) params.provinces = provinces;
      if (chains.length) params.chains = chains;
      if (cities.length) params.cities = cities;
      if (confidence > 0) params.confidence_min = confidence;
      if (isCommercial !== 'all') params.is_commercial = isCommercial === '1' ? 1 : 0;

      const res = await fetch('/api/data/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(params),
      });

      if (!res.ok) {
        const err = await res.json();
        setDownloadProgress(`❌ ${err.error || '导出失败'}`);
        setTimeout(() => setDownloading(false), 2000);
        return;
      }

      const totalRows = res.headers.get('X-Total-Rows');
      const fileSize = res.headers.get('X-File-Size');
      const formatInfo: Record<string, string> = { csv: 'CSV', parquet: 'Parquet', csv_gz: 'CSV.GZ' };

      setDownloadProgress(
        `✅ 导出成功 • ${Number(totalRows).toLocaleString()} 行 • ${formatSize(Number(fileSize))}`
      );

      // 触发浏览器下载
      const blob = await res.blob();
      const disposition = res.headers.get('content-disposition') || '';
      const match = disposition.match(/filename\*?=(?:utf-8''|UTF-8'')(.+)/);
      const filename = match ? decodeURIComponent(match[1]) : `星宝数据.${params.format === 'parquet' ? 'parquet' : 'csv'}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      setTimeout(() => { setDownloading(false); setDownloadProgress(''); }, 3000);
      loadRecords();
    } catch {
      setDownloadProgress('❌ 网络异常，请重试');
      setTimeout(() => setDownloading(false), 2000);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes >= 1024*1024*1024) return `${(bytes/1024/1024/1024).toFixed(1)} GB`;
    if (bytes >= 1024*1024) return `${(bytes/1024/1024).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes/1024).toFixed(0)} KB`;
    return `${bytes} B`;
  };

  const dateRange = filterOptions?.date_range || {};
  const confRange = filterOptions?.confidence_range || { min: 0, max: 1 };

  const formatLabels: Record<string, string> = {
    parquet: 'Parquet（推荐，压缩~6x，Python 直接读）',
    csv: 'CSV（通用，Excel 直接打开）',
    csv_gz: 'CSV+GZIP（压缩传输，需手动解压）',
  };

  if (optionsLoading) {
    return <div className="export-panel"><div className="export-loading">加载筛选选项...</div></div>;
  }

  return (
    <div className="export-panel">
      {/* 筛选条件 */}
      <div className="export-section">
        <div className="export-section-title">📋 筛选条件</div>
        <div className="export-grid">
          {/* 月份 */}
          <div className="export-field">
            <div className="export-field-label">场景月份</div>
            <div className="export-month-range">
              <input
                type="month"
                className="export-input"
                value={dateFrom}
                min={dateRange.min_date}
                max={dateTo || dateRange.max_date}
                onChange={e => setDateFrom(e.target.value)}
                placeholder="开始月份"
              />
              <span className="export-range-sep">~</span>
              <input
                type="month"
                className="export-input"
                value={dateTo}
                min={dateFrom || dateRange.min_date}
                max={dateRange.max_date}
                onChange={e => setDateTo(e.target.value)}
                placeholder="结束月份"
              />
            </div>
          </div>

          {/* 疾病 */}
          <MultiSelect
            label="疾病"
            options={filterOptions?.diseases || []}
            selected={diseases}
            onChange={setDiseases}
            placeholder="选择疾病..."
          />

          {/* 产品 */}
          <div className="export-field">
            <div className="export-field-label">产品</div>
            <div className="export-tag-input">
              <input
                className="export-input"
                placeholder="输入产品名，按回车添加"
                value={productInput}
                onChange={e => setProductInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && productInput.trim()) {
                    const v = productInput.trim();
                    if (!products.includes(v)) setProducts([...products, v]);
                    setProductInput('');
                  }
                }}
              />
              {products.length > 0 && (
                <div className="export-tags">
                  {products.map(p => (
                    <span key={p} className="export-tag">
                      {p}
                      <button onClick={() => setProducts(products.filter(x => x !== p))}>×</button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 省份 */}
          <MultiSelect
            label="省份"
            options={filterOptions?.provinces || []}
            selected={provinces}
            onChange={setProvinces}
            placeholder="选择省份..."
          />

          {/* 连锁 */}
          <MultiSelect
            label="连锁"
            options={filterOptions?.chains || []}
            selected={chains}
            onChange={setChains}
            placeholder="选择连锁..."
          />

          {/* 城市 */}
          <MultiSelect
            label="城市"
            options={filterOptions?.cities || []}
            selected={cities}
            onChange={setCities}
            placeholder="选择城市..."
          />

          {/* 置信度 */}
          <div className="export-field">
            <div className="export-field-label">综合置信度 ≥ {confidence.toFixed(2)}</div>
            <input
              type="range"
              className="export-range"
              min={0}
              max={1}
              step={0.05}
              value={confidence}
              onChange={e => setConfidence(Number(e.target.value))}
            />
            <div className="export-range-labels">
              <span>0</span>
              <span>{confRange.max.toFixed(2)}</span>
            </div>
          </div>

          {/* 是否商用 */}
          <div className="export-field">
            <div className="export-field-label">是否商用</div>
            <select
              className="export-select"
              value={isCommercial}
              onChange={e => setIsCommercial(e.target.value)}
            >
              <option value="all">不限</option>
              <option value="1">仅商用数据</option>
              <option value="0">非商用数据</option>
            </select>
          </div>
        </div>
      </div>

      {/* 导出格式 */}
      <div className="export-section">
        <div className="export-section-title">📦 导出格式</div>
        <div className="export-formats">
          {(['parquet', 'csv', 'csv_gz'] as const).map(f => (
            <label key={f} className={`export-format-option ${format === f ? 'active' : ''}`}>
              <input
                type="radio"
                name="format"
                value={f}
                checked={format === f}
                onChange={() => setFormat(f)}
              />
              <span className="export-format-label">{formatLabels[f]}</span>
              {preview?.formats?.[f] && (
                <span className="export-format-size">{preview.formats[f].size_label}</span>
              )}
            </label>
          ))}
        </div>
      </div>

      {/* 预览 & 下载 */}
      <div className="export-section">
        <div className="export-preview-bar">
          {previewLoading ? (
            <div className="export-preview-loading">⏳ 估算中...</div>
          ) : preview ? (
            <div className="export-preview-info">
              <span className="export-preview-icon">📊</span>
              <span>
                预估 <strong>{Number(preview.total_rows).toLocaleString()}</strong> 条场景
                · <strong>{preview.estimated_size_label}</strong>
                {preview.estimated_size_csv && preview.format !== 'csv' && (
                  <span className="export-preview-csv">（CSV 约 {preview.estimated_size_csv}）</span>
                )}
              </span>
            </div>
          ) : (
            <div className="export-preview-info dim">调整筛选条件后将自动估算</div>
          )}
        </div>

        <button
          className="export-download-btn"
          onClick={handleExport}
          disabled={downloading || (preview && preview.total_rows === 0)}
        >
          {downloading ? (
            <>{downloadProgress || '⏳ 导出中...'}</>
          ) : (
            <>{'📥 导出数据'}</>
          )}
        </button>
      </div>

      {/* 下载历史 */}
      <div className="export-section">
        <div className="export-section-title" style={{ cursor: 'pointer' }} onClick={() => setHistoryTab(!historyTab)}>
          📄 下载历史 {recordsTotal > 0 && <span className="export-history-count">{recordsTotal}</span>}
          <span className="ms-arrow" style={{ marginLeft: 8 }}>{historyTab ? '▲' : '▼'}</span>
        </div>
        {historyTab && (
          <div className="export-history">
            {records.length === 0 ? (
              <div className="export-history-empty">暂无下载记录</div>
            ) : (
              records.map(r => (
                <div key={r.id} className="export-history-item">
                  <div className="export-history-name">{r.file_name}</div>
                  <div className="export-history-meta">
                    {r.row_count.toLocaleString()} 条 · {r.file_size_mb}MB · {Math.round(r.elapsed_ms)}ms · {r.created_at}
                  </div>
                  <div className="export-history-filters">
                    {Object.entries(r.filters || {}).filter(([, v]) => {
                      if (v === null || v === '' || (Array.isArray(v) && v.length === 0)) return false;
                      return true;
                    }).map(([k, v]) => (
                      <span key={k} className="export-history-filter-tag">{k}: {Array.isArray(v) ? v.join(',') : String(v)}</span>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
