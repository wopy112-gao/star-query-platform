/** 管理后台 — 总览页 */

import { useState, useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { authHeaders } from '../api/auth';

interface DashboardData {
  total_users: number;
  today_active_users: number;
  pending_incidents: number;
  total_queries: number;
  trend: { date: string; count: number }[];
}

interface PendingIncident {
  id: string;
  type: string;
  status: string;
  question: string;
  created_at: string;
}

const WEEKDAY: Record<string, string> = {
  'Mon': '一', 'Tue': '二', 'Wed': '三', 'Thu': '四',
  'Fri': '五', 'Sat': '六', 'Sun': '日',
};

function formatDate(dateStr: string): string {
  // 2026-05-20 → 5/20(三)
  const d = new Date(dateStr + 'T00:00:00');
  const week = WEEKDAY[d.toLocaleDateString('en-US', { weekday: 'short' })] || '';
  return `${d.getMonth() + 1}/${d.getDate()}(${week})`;
}

export default function AdminOverview() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [pending, setPending] = useState<PendingIncident[]>([]);
  const [loading, setLoading] = useState(true);
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  // 加载 Dashboard 数据
  useEffect(() => {
    Promise.all([
      fetch('/api/admin/dashboard', { headers: authHeaders() }).then(r => r.json()),
      fetch('/api/admin/incidents?limit=5&status=pending', { headers: authHeaders() }).then(r => r.json()),
    ]).then(([dashboard, incidents]) => {
      setData(dashboard);
      setPending(incidents.items || []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  // 趋势图
  useEffect(() => {
    if (!data?.trend || data.trend.length === 0) return;
    if (!chartRef.current) return;

    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }

    const dates = data.trend.map(t => formatDate(t.date));
    const counts = data.trend.map(t => t.count);

    instanceRef.current.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const p = params[0];
          return `${p.name}<br/>📊 查询量: <strong>${p.value}</strong> 次`;
        },
      },
      grid: { left: 44, right: 16, top: 12, bottom: 24 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 11, color: '#999' },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        min: 0,
        splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
        axisLabel: { fontSize: 11, color: '#999' },
      },
      series: [{
        type: 'bar',
        data: counts,
        barWidth: '40%',
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#1890ff' },
            { offset: 1, color: '#69c0ff' },
          ]),
          borderRadius: [4, 4, 0, 0],
        },
        emphasis: {
          itemStyle: { color: '#096dd9' },
        },
      }],
    }, true);

    const ro = new ResizeObserver(() => instanceRef.current?.resize());
    ro.observe(chartRef.current);
    return () => ro.disconnect();
  }, [data]);

  if (loading) {
    return (
      <div className="admin-content">
        <h2 className="admin-page-title">📊 总览</h2>
        <div className="admin-loading">加载中...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="admin-content">
        <h2 className="admin-page-title">📊 总览</h2>
        <div className="admin-empty">数据加载失败</div>
      </div>
    );
  }

  return (
    <div className="admin-content">
      <h2 className="admin-page-title">📊 总览</h2>

      {/* 核心指标卡 */}
      <div className="overview-cards">
        <div className="overview-card">
          <div className="overview-card-icon">👥</div>
          <div className="overview-card-body">
            <div className="overview-card-value">{data.total_users}</div>
            <div className="overview-card-label">用户总数</div>
          </div>
        </div>
        <div className="overview-card highlight">
          <div className="overview-card-icon">🔥</div>
          <div className="overview-card-body">
            <div className="overview-card-value">{data.today_active_users}</div>
            <div className="overview-card-label">今日活跃</div>
          </div>
        </div>
        <div className="overview-card warn">
          <div className="overview-card-icon">⚠️</div>
          <div className="overview-card-body">
            <div className="overview-card-value">{data.pending_incidents}</div>
            <div className="overview-card-label">待处理事件</div>
          </div>
        </div>
        <div className="overview-card">
          <div className="overview-card-icon">🔍</div>
          <div className="overview-card-body">
            <div className="overview-card-value">{data.total_queries.toLocaleString()}</div>
            <div className="overview-card-label">总查询次数</div>
          </div>
        </div>
      </div>

      {/* 趋势图 + 待处理事件 */}
      <div className="overview-grid">
        {/* 近7日查询量趋势 */}
        <div className="overview-chart-card">
          <h3 className="overview-section-title">📈 近7日查询量趋势</h3>
          <div ref={chartRef} className="overview-chart-container" />
        </div>

        {/* 待处理事件 */}
        <div className="overview-pending-card">
          <h3 className="overview-section-title">
            ⏳ 待处理事件
            {pending.length > 0 && (
              <span className="overview-pending-badge">{pending.length}</span>
            )}
          </h3>
          {pending.length === 0 ? (
            <div className="overview-empty-state">🎉 暂无待处理事件</div>
          ) : (
            <div className="overview-pending-list">
              {pending.map(p => (
                <div key={p.id} className="overview-pending-item">
                  <span className={`overview-pending-type type-${p.type}`}>
                    {p.type === 'validation_fail' ? '🔴 校验' : '👎 踩'}
                  </span>
                  <span className="overview-pending-question" title={p.question}>
                    {p.question.length > 30 ? p.question.slice(0, 30) + '...' : p.question}
                  </span>
                  <span className="overview-pending-time">
                    {p.created_at?.slice(5, 16) || ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
