/** 星宝管理后台 — 主框架 */

import { useState } from 'react';
import { getUsername, clearAuth, authHeaders } from '../api/auth';
import AdminOverview from './AdminOverview';
import AdminUsers from './AdminUsers';
import AdminIncidents from './AdminIncidents';
import AdminQueries from './AdminQueries';
import AdminDataHealth from './AdminDataHealth';

type Tab = 'overview' | 'users' | 'incidents' | 'queries' | 'data-health';

interface Props {
  onBackToQuery: () => void;
}

interface SyncChange {
  file: string;
  diff: string;
  type: string;
}

const TABS: { key: Tab; icon: string; label: string }[] = [
  { key: 'overview', icon: '📊', label: '总览' },
  { key: 'users', icon: '👥', label: '用户管理' },
  { key: 'incidents', icon: '⚠️', label: '反馈事件' },
  { key: 'queries', icon: '🔍', label: '查询记录' },
  { key: 'data-health', icon: '📡', label: '数据健康' },
];

export default function AdminPage({ onBackToQuery }: Props) {
  const [tab, setTab] = useState<Tab>('overview');
  const [queryUsername, setQueryUsername] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<{
    show: boolean;
    changes: SyncChange[];
    message: string;
    success: boolean;
    log: string[];
    errors: string[];
  } | null>(null);
  const username = getUsername() || 'admin';

  const handleLogout = () => {
    clearAuth();
    onBackToQuery();
  };

  // 用户管理 → 查询记录钻取
  const handleViewQueries = (targetUsername: string) => {
    setQueryUsername(targetUsername);
    setTab('queries');
  };

  // 切换 Tab 时清除钻取状态
  const switchTab = (t: Tab) => {
    if (t !== 'queries') setQueryUsername('');
    setTab(t);
  };

  // 一键同步到正式环境
  const handleSync = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const res = await fetch('/api/admin/sync-to-prod', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
      });
      const data = await res.json();
      setSyncResult({
        show: true,
        changes: data.changes || [],
        message: data.message || '',
        success: data.success,
        log: data.sync_log || [],
        errors: data.errors || [],
      });
    } catch {
      setSyncResult({
        show: true,
        changes: [],
        message: '❌ 网络异常',
        success: false,
        log: [],
        errors: ['请求失败'],
      });
    } finally {
      setSyncing(false);
    }
  };

  const renderContent = () => {
    switch (tab) {
      case 'overview':
        return <AdminOverview />;
      case 'users':
        return <AdminUsers onViewQueries={handleViewQueries} />;
      case 'incidents':
        return <AdminIncidents />;
      case 'queries':
        return <AdminQueries initialUsername={queryUsername} />;
      case 'data-health':
        return <AdminDataHealth />;
      default:
        return <AdminOverview />;
    }
  };

  return (
    <div className="admin-page">
      {/* 顶栏 */}
      <header className="admin-header">
        <div className="admin-header-left">
          <span className="admin-header-icon">🏥</span>
          <h1 className="admin-header-title">星宝管理后台</h1>
        </div>
        <div className="admin-header-right">
          <span className="admin-user-badge">🔧 {username}</span>
          <button
            className="admin-sync-btn"
            onClick={handleSync}
            disabled={syncing}
            title="将测试环境的修改同步到正式环境"
          >
            {syncing ? '⏳ 同步中...' : '📤 同步'}
          </button>
          <button className="admin-back-btn" onClick={onBackToQuery}>
            ← 返回查询
          </button>
          <button className="admin-logout-btn" onClick={handleLogout}>
            退出
          </button>
        </div>
      </header>

      <div className="admin-body">
        {/* 侧边栏 */}
        <nav className="admin-sidebar">
          {TABS.map(t => (
            <button
              key={t.key}
              className={`admin-sidebar-item${tab === t.key ? ' active' : ''}`}
              onClick={() => switchTab(t.key)}
            >
              <span className="admin-sidebar-icon">{t.icon}</span>
              <span className="admin-sidebar-label">{t.label}</span>
            </button>
          ))}
        </nav>

        {/* 内容区 */}
        <main className="admin-main">
          {renderContent()}
        </main>
      </div>

      {/* 同步结果弹窗 */}
      {syncResult?.show && (
        <div className="modal-overlay" onClick={() => setSyncResult(null)}>
          <div className="modal-content sync-result-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{syncResult.success ? '✅ 同步完成' : '⚠️ 同步结果'}</h2>
              <button className="modal-close" onClick={() => setSyncResult(null)}>✕</button>
            </div>
            <div className="modal-body">
              {/* 变更清单 */}
              {syncResult.changes.length > 0 ? (
                <div className="sync-result-section">
                  <div className="sync-result-label">📋 同步文件</div>
                  <div className="sync-result-files">
                    {syncResult.changes.map((c, i) => (
                      <div key={i} className={`sync-result-file ${c.type}`}>
                        <span className="sync-result-filename">{c.file}</span>
                        <span className="sync-result-diff">{c.diff}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="sync-result-section">
                  <div className="sync-result-label">ℹ️ 无需同步，两边已一致</div>
                </div>
              )}

              {/* 执行日志 */}
              {syncResult.log.length > 0 && (
                <div className="sync-result-section">
                  <div className="sync-result-label">📝 执行日志</div>
                  <pre className="sync-result-log">
                    {syncResult.log.join('\n')}
                  </pre>
                </div>
              )}

              {/* 错误 */}
              {syncResult.errors.length > 0 && (
                <div className="sync-result-section">
                  <div className="sync-result-label error">❌ 错误</div>
                  <pre className="sync-result-log error">
                    {syncResult.errors.join('\n')}
                  </pre>
                </div>
              )}

              <div className="sync-result-close">
                <button className="admin-btn-primary" onClick={() => setSyncResult(null)}>
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
