import { useState } from 'react';
import { login, saveToken } from '../api/auth';

interface LoginParams {
  username: string;
  password: string;
}

interface Props {
  onLoginSuccess: (username: string) => void;
}

export default function LoginPage({ onLoginSuccess }: Props) {
  const [form, setForm] = useState<LoginParams>({ username: '', password: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!form.username || !form.password) {
      setError('请输入用户名和密码');
      return;
    }
    setLoading(true);
    try {
      const result = await login(form);
      saveToken(result.token, result.username);
      onLoginSuccess(result.username);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-icon">🏥</div>
        <h1 className="login-title">星宝语料场景查询系统</h1>
        <p className="login-desc">基于自然语言的药店场景数据查询与可视化</p>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <input
              type="text"
              placeholder="用户名"
              value={form.username}
              onChange={e => setForm({ ...form, username: e.target.value })}
              autoFocus
            />
          </div>
          <div className="form-group">
            <input
              type="password"
              placeholder="密码"
              value={form.password}
              onChange={e => setForm({ ...form, password: e.target.value })}
            />
          </div>

          {error && <div className="login-error">{error}</div>}

          <button type="submit" className="login-btn" disabled={loading}>
            {loading ? '登录中...' : '登 录'}
          </button>
        </form>
      </div>
    </div>
  );
}
