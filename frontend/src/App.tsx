import { useState, useEffect } from 'react';
import LoginPage from './pages/LoginPage';
import QueryPage from './pages/QueryPage';
import AdminPage from './pages/AdminPage';
import { getToken, verifyToken, clearAuth, getUsername } from './api/auth';
import './App.css';

/** 从 URL hash 获取模式（刷新后也能保留） */
function getModeFromHash(): 'query' | 'admin' {
  return window.location.hash === '#admin' ? 'admin' : 'query';
}

export default function App() {
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(true);
  const [mode, setMode] = useState<'query' | 'admin'>(getModeFromHash);

  const username = getUsername();
  const isAdmin = username === 'admin';

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setChecking(false);
      return;
    }
    verifyToken(token).then(valid => {
      if (!valid) clearAuth();
      setAuthed(valid);
      setChecking(false);
    });
  }, []);

  const enterAdmin = () => {
    window.location.hash = 'admin';
    setMode('admin');
  };

  const backToQuery = () => {
    window.location.hash = '';
    setMode('query');
  };

  if (checking) {
    return (
      <div className="loading-screen">
        <div className="loading-spinner" />
        <p>加载中...</p>
      </div>
    );
  }

  if (!authed) {
    return <LoginPage onLoginSuccess={() => setAuthed(true)} />;
  }

  // 查询页顶部显示管理后台入口
  if (mode === 'query') {
    return (
      <>
        {isAdmin && (
          <div className="admin-nav-bar">
            <span className="admin-nav-title">🏥 星宝语料场景查询系统</span>
            <button className="admin-nav-btn" onClick={enterAdmin}>
              ⚙️ 管理后台
            </button>
          </div>
        )}
        <QueryPage />
      </>
    );
  }

  // 管理后台视图
  return <AdminPage onBackToQuery={backToQuery} />;
}
