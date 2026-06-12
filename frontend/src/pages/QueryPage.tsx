import { useState, useEffect, useRef, useCallback } from 'react';
import * as echarts from 'echarts';
import { getUsername, clearAuth, authHeaders } from '../api/auth';
import HistoryPanel from '../components/HistoryPanel';
import DictionaryModal from '../components/DictionaryModal';
import LoginLogModal from '../components/LoginLogModal';
import ChangePasswordModal from '../components/ChangePasswordModal';
import DataExportPanel from '../components/DataExportPanel';

interface Template {
  id: string;
  label: string;
  question: string;
  description: string;
}

// ===== 意图卡片辅助 =====

const PATTERN_LABELS: Record<string, string> = {
  single_stat: '单值统计',
  top_n: 'TOP排行',
  distribution: '分布',
  trend: '趋势',
  detail: '明细',
  ranking: '全量排名',
  comparison: '对比',
};

const PATTERN_DESCRIPTIONS: Record<string, string> = {
  single_stat: '查询一个汇总数据（如总场景数、问症率）',
  top_n: '查询排名前N的维度（如疾病TOP10）',
  distribution: '按某个维度展示数据分布（如各省份场景数）',
  trend: '展示数据随时间的变化趋势（如月度趋势）',
  detail: '逐条展示原始数据明细',
  ranking: '展示所有维度的全量排名（不限量）',
  comparison: '对比不同维度的数据差异',
};

const CONDITION_LABELS: Record<string, string> = {
  disease: '疾病',
  drug_any: '药品',
  drug_named: '点名',
  drug_mentioned: '提及',
  drug_ordered: '订单',
  deal_yes: '成交',
  deal_no: '未成交',
  named_not_ordered: '点名未成交',
  mentioned_not_named: '提及未点名',
  mn_no_named_ordered: '提及未点名又成交',
  gender: '性别',
  combination: '联合用药',
  active_recommend: '活动推荐',
  active_participate: '活动参与',
  time_range: '日期',
  trust: '信任度',
};

const SOURCE_LABELS: Record<string, { icon: string; label: string }> = {
  template: { icon: '📋', label: '模板匹配' },
  cache: { icon: '⚡', label: '缓存命中' },
  llm_intent: { icon: '🤖', label: 'LLM 翻译' },
  moss: { icon: '🤖', label: 'Moss 翻译' },
};

function renderConditionLabel(type: string, value: string): string {
  const prefix = CONDITION_LABELS[type] || type;
  return `${prefix}: ${value}`;
}

function renderSourceTag(source: string): string {
  const info = SOURCE_LABELS[source] || { icon: '🤖', label: 'Moss 翻译' };
  return `${info.icon} ${info.label}`;
}

function renderIntentSource(source: string): string {
  const info = SOURCE_LABELS[source] || { icon: '🤖', label: 'Moss 翻译' };
  return `${info.icon} ${info.label}`;
}

// ===== 数据洞察看板 =====
function InsightsDashboard({ fontLarge }: { fontLarge: boolean }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    fetch('/api/insights', { headers: authHeaders() })
      .then(r => r.json())
      .then(d => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  // 趋势图
  useEffect(() => {
    if (!data?.trend || data.trend.length === 0) return;
    if (!chartRef.current) return;

    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }

    const dates = data.trend.map((t: any) => t.date.slice(5));
    const counts = data.trend.map((t: any) => t.count);

    instanceRef.current.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 30, right: 8, top: 16, bottom: 20 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 10, color: '#999' },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        min: 0,
        splitLine: { lineStyle: { color: '#f5f5f5' } },
        axisLabel: { fontSize: 10, color: '#999' },
      },
      series: [{
        type: 'line',
        data: counts,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: '#1890ff', width: 2 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(24,144,255,0.2)' },
            { offset: 1, color: 'rgba(24,144,255,0.02)' },
          ]),
        },
      }],
    }, true);

    const ro = new ResizeObserver(() => instanceRef.current?.resize());
    ro.observe(chartRef.current);
    return () => ro.disconnect();
  }, [data]);

  if (loading) {
    return (
      <div className="insights-bar loading">
        <div className="insights-loading">加载数据看板...</div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className={`insights-bar${collapsed ? ' collapsed' : ''}${fontLarge ? ' font-large' : ''}`}>
      <div className="insights-header">
        <span className="insights-title">📊 数据看板</span>
        <button
          className="insights-toggle"
          onClick={() => setCollapsed(!collapsed)}
          title={collapsed ? '展开' : '收起'}
        >
          {collapsed ? '▶ 展开' : '▼ 收起'}
        </button>
      </div>

      {!collapsed && (
        <>
          {/* 核心指标卡片 */}
          <div className="insights-cards">
            <div className="insight-card highlight">
              <div className="insight-card-label">今日场景</div>
              <div className="insight-card-value">{data.total.today_scenes.toLocaleString()}</div>
            </div>
            <div className="insight-card">
              <div className="insight-card-label">本周场景</div>
              <div className="insight-card-value">{data.total.week_scenes.toLocaleString()}</div>
            </div>
            <div className="insight-card">
              <div className="insight-card-label">总场景数</div>
              <div className="insight-card-value">{data.total.total_scenes.toLocaleString()}</div>
            </div>
            <div className="insight-card rate">
              <div className="insight-card-label">成交率</div>
              <div className="insight-card-value">{data.total.close_rate}%</div>
            </div>
            <div className="insight-card rate">
              <div className="insight-card-label">问症率</div>
              <div className="insight-card-value">{data.total.inquiry_rate}%</div>
            </div>
            <div className="insight-card rate">
              <div className="insight-card-label">联合用药率</div>
              <div className="insight-card-value">{data.total.combo_rate}%</div>
            </div>
          </div>

          {/* 趋势图 + TOP 列表 */}
          <div className="insights-detail">
            {/* 近7日趋势 */}
            {data.trend && data.trend.length > 0 && (
              <div className="insight-trend-chart">
                <div className="insight-subtitle">近7日场景数趋势</div>
                <div ref={chartRef} className="insight-trend-container" />
              </div>
            )}

            {/* 疾病TOP5 + 省份TOP5 */}
            <div className="insight-top-lists">
              {data.top_diseases && data.top_diseases.length > 0 && (
                <div className="insight-top-list">
                  <div className="insight-subtitle">🏥 疾病TOP5</div>
                  <div className="insight-tags">
                    {data.top_diseases.map((d: any, i: number) => (
                      <span key={i} className="insight-tag">
                        <span className="insight-tag-rank">#{i + 1}</span>
                        <span className="insight-tag-name">{d.name.split('-').pop()}</span>
                        <span className="insight-tag-count">{d.count.toLocaleString()}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {data.top_provinces && data.top_provinces.length > 0 && (
                <div className="insight-top-list">
                  <div className="insight-subtitle">📍 省份TOP5</div>
                  <div className="insight-tags">
                    {data.top_provinces.map((p: any, i: number) => (
                      <span key={i} className="insight-tag">
                        <span className="insight-tag-rank">#{i + 1}</span>
                        <span className="insight-tag-name">{p.name}</span>
                        <span className="insight-tag-count">{p.count.toLocaleString()}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 告警 */}
          {data.alerts && data.alerts.length > 0 && (
            <div className="insights-alerts">
              {data.alerts.map((a: any, i: number) => (
                <div key={i} className={`insight-alert ${a.level}`}>
                  {a.message}
                </div>
              ))}
            </div>
          )}

          {/* 数据日期 */}
          <div className="insights-footer">
            数据范围: {data.date_range?.min_date} ~ {data.date_range?.max_date}
          </div>
        </>
      )}
    </div>
  );
}

// ===== 反馈按钮 =====
function FeedbackButtons({ result }: { result: any }) {
  const [sentiment, setSentiment] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // 加载已有反馈
  useEffect(() => {
    if (!result?.query) return;
    const historyId = getHistoryId(result);
    if (!historyId) return;

    fetch(`/api/feedback/${historyId}`, { headers: authHeaders() })
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          setSentiment(data.sentiment);
        } else {
          setSentiment(null);
        }
      })
      .catch(() => setSentiment(null));
  }, [result]);

  // 从 result 中提取 history_id（使用问题+时间作为临时key）
  function getHistoryId(r: any): string | null {
    if (!r?.query?.natural) return null;
    // 用问题内容做简单哈希作为临时 history_id
    // 实际应用中应该从后端返回的 query 中带 history_id
    const q = r.query.natural;
    let hash = 0;
    for (let i = 0; i < q.length; i++) {
      hash = ((hash << 5) - hash) + q.charCodeAt(i);
      hash |= 0;
    }
    return `q_${Math.abs(hash).toString(16)}`;
  }

  const handleFeedback = async (s: 'like' | 'dislike') => {
    if (!result?.query) return;
    const historyId = getHistoryId(result);
    if (!historyId) return;

    // 如果点击的是当前已选中的，取消反馈
    const newSentiment = sentiment === s ? null : s;
    setSaving(true);

    try {
      if (newSentiment === null) {
        // 取消反馈
        await fetch(`/api/feedback/${historyId}`, {
          method: 'DELETE',
          headers: authHeaders(),
        });
      } else {
        // 提交反馈
        await fetch('/api/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({
            history_id: historyId,
            question: result.query.natural,
            sentiment: newSentiment,
            comment: '',
          }),
        });
      }
      setSentiment(newSentiment);
    } catch {
      // 静默失败
    } finally {
      setSaving(false);
    }
  };

  // 没有查询结果时隐藏
  if (!result?.query) return null;

  return (
    <div className="feedback-buttons">
      <span className="feedback-label">这个结果对你有帮助吗？</span>
      <button
        className={`feedback-btn like${sentiment === 'like' ? ' active' : ''}`}
        onClick={() => handleFeedback('like')}
        disabled={saving}
        title="有帮助"
      >
        👍 {sentiment === 'like' && '已赞'}
      </button>
      <button
        className={`feedback-btn dislike${sentiment === 'dislike' ? ' active' : ''}`}
        onClick={() => handleFeedback('dislike')}
        disabled={saving}
        title="没帮助"
      >
        👎 {sentiment === 'dislike' && '已踩'}
      </button>
    </div>
  );
}

// ===== 图表组件 =====
function ChartView({ option }: { option: any }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }
    instanceRef.current.setOption(option, true);

    const handleResize = () => instanceRef.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [option]);

  useEffect(() => {
    if (!chartRef.current) return;
    const ro = new ResizeObserver(() => instanceRef.current?.resize());
    ro.observe(chartRef.current);
    return () => ro.disconnect();
  }, []);

  return <div ref={chartRef} className="chart-container" />;
}

// ===== 主页面 =====
export default function QueryPage() {
  const [username] = useState(getUsername());
  const [templates, setTemplates] = useState<Template[]>([]);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const [showAlgorithm, setShowAlgorithm] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [historyVisible, setHistoryVisible] = useState(true);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false);
  const [fontLarge, setFontLarge] = useState(false);
  const [dictOpen, setDictOpen] = useState(false);
  const [loginLogOpen, setLoginLogOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [sqlEditing, setSqlEditing] = useState<string | null>(null);
  const [sqlExecuting, setSqlExecuting] = useState(false);
  const [sqlError, setSqlError] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [pagination, setPagination] = useState<any>(null);
  const [activeTab, setActiveTab] = useState(0); // 0=查询 1=导出
  const inputRef = useRef<HTMLInputElement>(null);

  // 数据导出权限校验
  const exportAllowedUsers = new Set(["admin", "ella", "hubo", "liumd", "dongjl", "amy", "wim"]);
  const canExport = exportAllowedUsers.has(username);

  // 移动端检测
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  useEffect(() => {
    fetch('/api/templates', { headers: authHeaders() })
      .then(r => r.json())
      .then(data => setTemplates(data || []))
      .catch(() => {});
  }, []);

  const handleQuery = useCallback(async (q: string, p?: number) => {
    if (!q) return;
    const targetPage = p ?? 1;
    setError('');
    setLoading(true);
    setResult(null);
    setShowAlgorithm(false);
    setSqlEditing(null);
    setSqlError('');
    try {
      const res = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ question: q, page: targetPage, page_size: pageSize }),
      });
      const data = await res.json();
      if (!data.success) {
        setError(data.error || '查询失败');
      } else {
        setResult(data);
        setPagination(data.result?.pagination || null);
        setPage(targetPage);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '请求失败');
    } finally {
      setLoading(false);
      // 刷新历史
      setRefreshKey(prev => prev + 1);
    }
  }, [pageSize]);

  // 从历史选择
  const handleHistorySelect = useCallback((q: string) => {
    setQuestion(q);
    handleQuery(q);
    if (isMobile) setMobilePanelOpen(false);
  }, [handleQuery, isMobile]);

  // 下载 CSV（分页模式→服务端导出全部数据；非分页→客户端导出当前页）
  const handleDownloadCSV = async () => {
    // 有分页信息 → 走服务端导出（全部数据）
    if (pagination && pagination.total_count > 0) {
      try {
        const res = await fetch('/api/query/export', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ question }),
        });
        if (!res.ok) {
          const errData = await res.json();
          alert('导出失败: ' + (errData.error || res.statusText));
          return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const ts = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '');
        a.download = `星宝查询_${ts}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch (e: any) {
        alert('导出失败: ' + e.message);
      }
      return;
    }

    // 无分页 → 客户端导出当前 rows
    if (!result?.result?.rows || result.result.rows.length === 0) return;

    const rows = result.result.rows;
    const headers = Object.keys(rows[0]);

    const csvContent = [
      headers.join(','),
      ...rows.map((row: Record<string, unknown>) =>
        headers.map(h => {
          const val = row[h];
          const str = val === null || val === undefined ? '' : String(val);
          return str.includes(',') || str.includes('"') || str.includes('\n')
            ? `"${str.replace(/"/g, '""')}"`
            : str;
        }).join(',')
      ),
    ].join('\n');

    const BOM = '\uFEFF';
    const blob = new Blob([BOM + csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ts = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '');
    a.download = `星宝查询_${ts}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleLogout = () => {
    clearAuth();
    window.location.reload();
  };

  const renderChart = (chartData: any) => {
    if (!chartData.option) return null;
    return <ChartView option={chartData.option} />;
  };

  return (
    <div className="query-page">
      <header className="query-header">
        <button
          className="sidebar-toggle"
          onClick={() => {
            if (isMobile) {
              setMobilePanelOpen(!mobilePanelOpen);
            } else {
              setHistoryVisible(!historyVisible);
            }
          }}
          title={isMobile ? (mobilePanelOpen ? '关闭历史' : '打开历史') : (historyVisible ? '收起历史' : '展开历史')}
        >
          {isMobile ? (mobilePanelOpen ? '✕' : '☰') : (historyVisible ? '◀' : '▶')}
        </button>
        <h1>{isMobile ? '🏥 星宝查询' : '🏥 星宝语料场景查询'}</h1>
        <div className="header-right">
          <button
            className="dict-btn"
            onClick={() => setDictOpen(true)}
            title="字段字典"
          >
            📖
          </button>
          <button
            className="dict-btn"
            onClick={() => setLoginLogOpen(true)}
            title="登录日志"
          >
            🔐
          </button>
          <button
            className="dict-btn"
            onClick={() => setPasswordOpen(true)}
            title="修改密码"
          >
            🔑
          </button>
          <button
            className={`font-toggle${fontLarge ? ' large' : ''}`}
            onClick={() => setFontLarge(!fontLarge)}
            title={fontLarge ? '切换为小字体' : '切换为大字体'}
          >
            {fontLarge ? (
              <><span className="ft-letter">A</span><span className="ft-plus">⁺</span></>
            ) : (
              <span className="ft-letter">A</span>
            )}
          </button>
          <span className="user-badge">{username}</span>
          <button className="logout-btn" onClick={handleLogout}>退出</button>
        </div>
      </header>

      {/* Tab 导航 */}
      <div className="query-tabs">
        <button
          className={`query-tab ${activeTab === 0 ? 'active' : ''}`}
          onClick={() => setActiveTab(0)}
        >
          🔍 场景查询
        </button>
        {canExport && (
          <button
            className={`query-tab ${activeTab === 1 ? 'active' : ''}`}
            onClick={() => setActiveTab(1)}
          >
            📥 数据导出
          </button>
        )}
      </div>

      {activeTab === 1 && canExport ? (
        <DataExportPanel />
      ) : (
        <div className="query-layout">
        {/* 移动端遮罩 */}
        {isMobile && mobilePanelOpen && (
          <div className="history-overlay" onClick={() => setMobilePanelOpen(false)} />
        )}

        {/* 历史侧边栏 */}
        {(isMobile ? mobilePanelOpen : historyVisible) && (
          <HistoryPanel
            onSelect={handleHistorySelect}
            refreshKey={refreshKey}
            isMobile={isMobile}
          />
        )}

        {/* 主内容区 */}
        <main className={`query-main${fontLarge ? ' font-large' : ''}`}>
          {/* 输入区 — 用户第一眼看到 */}
          <div className="query-input-area">
            <input
              ref={inputRef}
              type="text"
              className="query-input"
              placeholder="输入你的问题，例如：总场景数、疾病TOP10、城市分布..."
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && question && handleQuery(question)}
            />
            <button className="query-btn" onClick={() => question && handleQuery(question)} disabled={loading}>
              {loading ? '查询中...' : '查 询'}
            </button>
          </div>

          {/* 模板区 */}
          <div className="templates-bar">
            <span className="templates-label">常用查询：</span>
            {templates.map(t => (
              <button key={t.id} className="template-tag" onClick={() => handleQuery(t.question)} title={t.description}>
                {t.label}
              </button>
            ))}
          </div>

          {/* 错误提示 */}
          {error && <div className="result-error">{error}</div>}

          {/* (P2) 数据范围友好提示 */}
          {result?.warning && (
            <div className="scope-warning">
              <span className="scope-warning-icon">⚠️</span>
              <span className="scope-warning-text">{result.warning}</span>
            </div>
          )}

          {/* 无结果 → 展示洞察看板；有结果 → 覆盖隐藏 */}
          {!result && <InsightsDashboard fontLarge={fontLarge} />}

          {/* 结果区 */}
          {result && (
            <div className="result-area">
              {/* 意图卡片 */}
              {result.intent_info && (
                <div className="intent-card">
                  <div className="intent-card-header">
                    <span className="intent-card-icon">🧠</span>
                    <span className="intent-card-title">您查询的是</span>
                    <span className="intent-card-badge">{renderIntentSource(result.intent_info.route_source)}</span>
                  </div>
                  <div className="intent-card-body">
                    <span className="intent-chip" title={PATTERN_DESCRIPTIONS[result.intent_info.query_pattern] || ''}>
                      {PATTERN_LABELS[result.intent_info.query_pattern] || result.intent_info.query_pattern}
                    </span>
                    <span className="intent-arrow">→</span>
                    <span className="intent-chip intent-chip-agg">{result.intent_info.agg}</span>
                    {result.intent_info.dimension && (
                      <>
                        <span className="intent-arrow">→</span>
                        <span className="intent-chip intent-chip-dim">按 {result.intent_info.dimension}</span>
                      </>
                    )}
                    {result.intent_info.conditions && result.intent_info.conditions.length > 0 && (
                      <div className="intent-conditions">
                        {result.intent_info.conditions.map((c: any, i: number) => (
                          <span key={i} className="intent-condition-tag">
                            {renderConditionLabel(c.type, c.value)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  {result.warnings && result.warnings.length > 0 && (
                    <div className="intent-card-warnings">
                      {result.warnings.map((w: string, i: number) => (
                        <div key={i} className="intent-warning-item">⚠️ {w}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* 图表 */}
              {result.chart && result.chart.type !== 'table_only' && (
                <div className="result-chart">
                  {renderChart(result.chart)}
                </div>
              )}

              {/* SQL 信息 + 算法折叠 */}
              {result.query && (
                <div className="result-sql-info">
                  <span className="sql-source-tag">
                    {renderSourceTag(result.query.source)}
                  </span>
                  <span className="sql-elapsed">{result.query.elapsed_ms}ms</span>
                  {result.explanation && (
                    <button
                      className={`algo-toggle ${showAlgorithm ? 'active' : ''}`}
                      onClick={() => setShowAlgorithm(!showAlgorithm)}
                    >
                      📖 查看算法
                    </button>
                  )}
                </div>
              )}

              {/* 算法详情（折叠） */}
              {showAlgorithm && result.explanation && (
                <div className="algo-panel">
                  <div className="algo-section">
                    <div className="algo-section-title">
                      📝 SQL 语句
                      <span className="sql-editor-hint">（可直接修改后重新运行）</span>
                    </div>
                    <textarea
                      className="algo-sql-editor"
                      value={sqlEditing ?? result.explanation.sql}
                      onChange={e => setSqlEditing(e.target.value)}
                      rows={8}
                      spellCheck={false}
                    />
                    <div className="sql-editor-actions">
                      <button
                        className="sql-run-btn"
                        onClick={async () => {
                          const sql = sqlEditing ?? result.explanation.sql;
                          if (!sql.trim()) return;
                          setSqlExecuting(true);
                          setSqlError('');
                          try {
                            const res = await fetch('/api/query/sql', {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json', ...authHeaders() },
                              body: JSON.stringify({ sql: sql.trim() }),
                            });
                            const data = await res.json();
                            if (!data.success) {
                              setSqlError(data.error || 'SQL 执行失败');
                            } else {
                              setResult(data);
                              setShowAlgorithm(false);
                            }
                          } catch {
                            setSqlError('请求失败，请稍后重试');
                          } finally {
                            setSqlExecuting(false);
                          }
                        }}
                        disabled={sqlExecuting}
                      >
                        {sqlExecuting ? '⏳ 执行中...' : '▶ 修改并运行'}
                      </button>
                      {sqlEditing !== null && (
                        <button
                          className="sql-reset-btn"
                          onClick={() => setSqlEditing(null)}
                        >
                          ↩ 恢复默认
                        </button>
                      )}
                    </div>
                    {sqlError && <div className="sql-error-msg">❌ {sqlError}</div>}
                  </div>
                  {result.explanation.notes && result.explanation.notes.length > 0 && (
                    <div className="algo-section">
                      <div className="algo-section-title">📋 算法口径说明</div>
                      <div className="algo-notes">
                        {result.explanation.notes.map((note: any, i: number) => (
                          <div key={i} className="algo-note-item">
                            <span className="algo-note-label">{note.label}</span>
                            <span className="algo-note-content">{note.content}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* 数据表格 */}
              {result.result && result.result.rows && (
                <div className="result-table-wrapper">
                  <div className="result-summary">
                    <span>{result.result.summary}</span>
                    {result.result.rows.length > 0 && (
                      <button className="csv-download-btn" onClick={handleDownloadCSV} title="下载CSV">
                        📥 下载CSV
                      </button>
                    )}
                  </div>
                  <table className="result-table">
                    <thead>
                      <tr>
                        {result.result.rows.length > 0 && Object.keys(result.result.rows[0]).map(k => (
                          <th key={k}>{k}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.result.rows.map((row: Record<string, unknown>, i: number) => (
                        <tr key={i}>
                          {Object.values(row).map((v, j) => (
                            <td key={j}>{v === null || v === undefined ? '-' : typeof v === 'number' ? v.toLocaleString() : String(v)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {/* 分页导航 */}
                  {pagination && (
                    <div className="pagination-bar">
                      <span className="pagination-info">
                        共 {pagination.total_count.toLocaleString()} 条结果
                      </span>
                      <span className="pagination-controls">
                        <button
                          className="pagination-btn"
                          disabled={!pagination.has_prev}
                          onClick={() => handleQuery(question, page - 1)}
                        >
                          ‹ 上一页
                        </button>
                        <span className="pagination-pages">
                          第 {pagination.page}/{pagination.total_pages} 页
                        </span>
                        <button
                          className="pagination-btn"
                          disabled={!pagination.has_next}
                          onClick={() => handleQuery(question, page + 1)}
                        >
                          下一页 ›
                        </button>
                      </span>
                      <select
                        className="pagination-size"
                        value={pageSize}
                        onChange={e => {
                          const newSize = Number(e.target.value);
                          setPageSize(newSize);
                          handleQuery(question, 1);
                        }}
                      >
                        <option value={20}>20条/页</option>
                        <option value={50}>50条/页</option>
                        <option value={100}>100条/页</option>
                      </select>
                    </div>
                  )}

                  {result.confidence && (
                    <div className="confidence-info">
                      ⓘ 置信度: {result.confidence.level === 'high' ? '🟢 高' : result.confidence.level === 'medium' ? '🟡 中' : '🔴 低'}
                      {result.confidence.note ? ` — ${result.confidence.note}` : ''}
                    </div>
                  )}

                  {/* 反馈按钮 */}
                  <FeedbackButtons result={result} />
                </div>
              )}
            </div>
          )}
        </main>
      </div>
      )}

      {/* 字段字典 Modal */}
      <DictionaryModal open={dictOpen} onClose={() => setDictOpen(false)} fontLarge={fontLarge} />
      {/* 登录日志 Modal */}
      <LoginLogModal open={loginLogOpen} onClose={() => setLoginLogOpen(false)} fontLarge={fontLarge} />
      {/* 修改密码 Modal */}
      <ChangePasswordModal open={passwordOpen} onClose={() => setPasswordOpen(false)} />
    </div>
  );
}
