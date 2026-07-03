/** 管理后台 — 数据接口健康状态 */

import { useState, useEffect } from 'react';
import { authHeaders } from '../api/auth';

interface DataHealth {
  data_file: {
    path: string;
    size_mb: number;
    last_modified: string;
    filename: string;
  };
  data_coverage: {
    min_date: string;
    max_date: string;
    total_days: number;
    total_scenes: number;
  };
  daily_trend: { date: string; scenes: number }[];
  sync_status: {
    log_file?: string;
    last_5_runs: { time: string; rows_pulled: number; status: string }[];
    total_runs?: number;
    error?: string;
    note?: string;
  };
  alerts: { level: string; message: string }[];
}

const ALERT_ICONS: Record<string, string> = {
  error: '❌',
  warning: '⚠️',
  info: 'ℹ️',
};

const SYNC_STATUS_LABELS: Record<string, string> = {
  success: '✅ 成功',
  no_data: 'ℹ️ 无数据',
  error: '❌ 出错',
  unknown: '❓ 未知',
};

export default function AdminDataHealth() {
  const [data, setData] = useState<DataHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/api/admin/data-health', { headers: authHeaders() })
      .then(r => r.json())
      .then(d => {
        setData(d);
        setLoading(false);
      })
      .catch(() => {
        setError('数据加载失败');
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="admin-content">
        <h2 className="admin-page-title">📡 数据健康</h2>
        <div className="admin-loading">加载中...</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="admin-content">
        <h2 className="admin-page-title">📡 数据健康</h2>
        <div className="admin-empty">{error || '数据加载失败'}</div>
      </div>
    );
  }

  return (
    <div className="admin-content">
      <h2 className="admin-page-title">📡 数据健康</h2>

      {/* 告警区 */}
      {data.alerts.length > 0 && (
        <div className="dh-alerts">
          {data.alerts.map((a, i) => (
            <div key={i} className={`dh-alert dh-alert-${a.level}`}>
              <span className="dh-alert-icon">{ALERT_ICONS[a.level] || 'ℹ️'}</span>
              <span className="dh-alert-text">{a.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* 核心指标卡 */}
      <div className="overview-cards">
        <div className="overview-card">
          <div className="overview-card-icon">📦</div>
          <div className="overview-card-body">
            <div className="overview-card-value">
              {data.data_coverage.total_scenes?.toLocaleString()}
            </div>
            <div className="overview-card-label">总场景数</div>
          </div>
        </div>
        <div className="overview-card highlight">
          <div className="overview-card-icon">📅</div>
          <div className="overview-card-body">
            <div className="overview-card-value">{data.data_coverage.max_date || '-'}</div>
            <div className="overview-card-label">最新数据日期</div>
          </div>
        </div>
        <div className="overview-card">
          <div className="overview-card-icon">🗓️</div>
          <div className="overview-card-body">
            <div className="overview-card-value">{data.data_coverage.total_days}</div>
            <div className="overview-card-label">覆盖天数</div>
          </div>
        </div>
        <div className={`overview-card ${data.data_file?.last_modified ? '' : 'warn'}`}>
          <div className="overview-card-icon">🕐</div>
          <div className="overview-card-body">
            <div className="overview-card-value" style={{ fontSize: 14 }}>
              {data.data_file?.last_modified?.slice(0, 16) || '未知'}
            </div>
            <div className="overview-card-label">数据文件更新</div>
          </div>
        </div>
      </div>

      {/* 两列：同步日志 + 每日趋势 */}
      <div className="overview-grid">
        {/* 同步日志 */}
        <div className="overview-chart-card">
          <h3 className="overview-section-title">🔄 每日同步记录（最近5次）</h3>
          {data.sync_status.last_5_runs?.length > 0 ? (
            <div className="dh-sync-list">
              {[...data.sync_status.last_5_runs].reverse().map((run, i) => (
                <div key={i} className="dh-sync-item">
                  <span className="dh-sync-time">{run.time.slice(0, 16)}</span>
                  <span className={`dh-sync-status status-${run.status}`}>
                    {SYNC_STATUS_LABELS[run.status] || run.status}
                  </span>
                  <span className="dh-sync-rows">
                    {run.rows_pulled > 0 ? `${run.rows_pulled.toLocaleString()} 行` : '-'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="dh-empty-state">
              {data.sync_status.note || '暂无同步记录'}
            </div>
          )}
          {data.sync_status.total_runs !== undefined && (
            <div className="dh-sync-total">
              累计 {data.sync_status.total_runs} 次同步
              <span className="dh-sync-file" title={data.sync_status.log_file}>
                📄 查看日志
              </span>
            </div>
          )}
        </div>

        {/* 文件信息 */}
        <div className="overview-pending-card">
          <h3 className="overview-section-title">📁 数据文件</h3>
          <div className="dh-file-info">
            <div className="dh-file-row">
              <span className="dh-file-label">文件名</span>
              <span className="dh-file-value">{data.data_file?.filename || '-'}</span>
            </div>
            <div className="dh-file-row">
              <span className="dh-file-label">大小</span>
              <span className="dh-file-value">{data.data_file?.size_mb || 0} MB</span>
            </div>
            <div className="dh-file-row">
              <span className="dh-file-label">最后修改</span>
              <span className="dh-file-value">{data.data_file?.last_modified || '-'}</span>
            </div>
            <div className="dh-file-row">
              <span className="dh-file-label">日期范围</span>
              <span className="dh-file-value">
                {data.data_coverage.min_date || '-'} ~ {data.data_coverage.max_date || '-'}
              </span>
            </div>
            <div className="dh-file-row">
              <span className="dh-file-label">路径</span>
              <span className="dh-file-value dh-file-path">{data.data_file?.path || '-'}</span>
            </div>
          </div>
        </div>
      </div>

      {/* 每日场景数趋势表 */}
      <div className="dh-trend-section">
        <h3 className="overview-section-title">📊 最近每日场景数</h3>
        {data.daily_trend.length > 0 ? (
          <div className="dh-trend-table-wrapper">
            <table className="dh-trend-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>场景数</th>
                  <th>变化</th>
                </tr>
              </thead>
              <tbody>
                {data.daily_trend.map((d, i) => {
                  const prev = i < data.daily_trend.length - 1 ? data.daily_trend[i + 1].scenes : null;
                  const change = prev !== null ? d.scenes - prev : null;
                  const pct = prev !== null && prev > 0 ? ((change! / prev) * 100).toFixed(1) : null;
                  return (
                    <tr key={d.date}>
                      <td className="dh-trend-date">{d.date}</td>
                      <td className="dh-trend-value">{d.scenes.toLocaleString()}</td>
                      <td className="dh-trend-change">
                        {change !== null ? (
                          change > 0 ? (
                            <span className="dh-change-up">↑ {pct}%</span>
                          ) : change < 0 ? (
                            <span className="dh-change-down">↓ {pct}%</span>
                          ) : (
                            <span className="dh-change-flat">— 0%</span>
                          )
                        ) : '-'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="dh-empty-state">暂无每日趋势数据</div>
        )}
      </div>
    </div>
  );
}
