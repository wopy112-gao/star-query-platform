/** 管理后台 — 用户管理 */

import { useState, useEffect, useCallback } from 'react';
import { authHeaders } from '../api/auth';

interface User {
  username: string;
  role: string;
  status: string;
  display_name: string;
  created_at: string;
  last_login_at: string | null;
  note: string;
  query_count: number;
}

interface UserListResponse {
  items: User[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

interface Props {
  onViewQueries?: (username: string) => void;
}

export default function AdminUsers({ onViewQueries }: Props) {
  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  const [searchInput, setSearchInput] = useState('');

  // Modal 状态
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editUser, setEditUser] = useState<User | null>(null);
  const [confirmOpen, setConfirmOpen] = useState<{
    type: 'delete' | 'reset-password' | 'toggle';
    user: User;
  } | null>(null);

  // 表单
  const [createForm, setCreateForm] = useState({
    username: '',
    password: '',
    role: 'user',
    display_name: '',
  });
  const [editForm, setEditForm] = useState({
    role: 'user',
    display_name: '',
    note: '',
  });
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const limit = 20;

  const fetchUsers = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), limit: String(limit) });
      if (keyword) params.set('keyword', keyword);
      const res = await fetch(`/api/admin/users?${params}`, { headers: authHeaders() });
      const data: UserListResponse = await res.json();
      setUsers(data.items);
      setTotal(data.total);
      setPage(data.page);
      setHasMore(data.has_more);
    } catch {
      // 静默
    } finally {
      setLoading(false);
    }
  }, [keyword]);

  useEffect(() => {
    fetchUsers(1);
  }, [fetchUsers]);

  // 搜索
  const handleSearch = () => {
    setKeyword(searchInput.trim());
    setPage(1);
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch();
  };

  // 翻页
  const prevPage = () => { if (page > 1) fetchUsers(page - 1); };
  const nextPage = () => { if (hasMore) fetchUsers(page + 1); };

  // ===== 新增用户 =====
  const handleCreate = async () => {
    setFormError('');
    if (!createForm.username.trim()) { setFormError('用户名不能为空'); return; }
    if (!createForm.password.trim()) { setFormError('密码不能为空'); return; }

    setSaving(true);
    try {
      const res = await fetch('/api/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(createForm),
      });
      const data = await res.json();
      if (!res.ok) {
        setFormError(data.detail || '创建失败');
        return;
      }
      setCreateOpen(false);
      setCreateForm({ username: '', password: '', role: 'user', display_name: '' });
      setSuccessMsg(`用户「${createForm.username}」创建成功`);
      setTimeout(() => setSuccessMsg(''), 3000);
      fetchUsers(1);
    } catch {
      setFormError('请求失败');
    } finally {
      setSaving(false);
    }
  };

  // ===== 编辑用户 =====
  const openEdit = (u: User) => {
    setEditUser(u);
    setEditForm({ role: u.role, display_name: u.display_name, note: u.note });
    setEditOpen(true);
    setFormError('');
  };

  const handleEdit = async () => {
    if (!editUser) return;
    setFormError('');
    setSaving(true);
    try {
      const res = await fetch(`/api/admin/users/${editUser.username}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(editForm),
      });
      const data = await res.json();
      if (!res.ok) {
        setFormError(data.detail || '更新失败');
        return;
      }
      setEditOpen(false);
      setSuccessMsg(`用户「${editUser.username}」已更新`);
      setTimeout(() => setSuccessMsg(''), 3000);
      fetchUsers(page);
    } catch {
      setFormError('请求失败');
    } finally {
      setSaving(false);
    }
  };

  // ===== 切换状态 =====
  const handleToggle = async () => {
    if (!confirmOpen) return;
    const u = confirmOpen.user;
    setConfirmOpen(null);
    try {
      const res = await fetch(`/api/admin/users/${u.username}/toggle-status`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const data = await res.json();
      if (res.ok) {
        const newLabel = data.status === 'disabled' ? '已禁用' : '已启用';
        setSuccessMsg(`用户「${u.username}」${newLabel}`);
        setTimeout(() => setSuccessMsg(''), 3000);
      }
      fetchUsers(page);
    } catch {
      // 静默
    }
  };

  // ===== 重置密码 =====
  const handleResetPassword = async () => {
    if (!confirmOpen) return;
    const u = confirmOpen.user;
    setConfirmOpen(null);
    try {
      const res = await fetch(`/api/admin/users/${u.username}/reset-password`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (res.ok) {
        setSuccessMsg(`用户「${u.username}」密码已恢复为默认值`);
        setTimeout(() => setSuccessMsg(''), 3000);
      }
      fetchUsers(page);
    } catch {
      // 静默
    }
  };

  // ===== 删除用户 =====
  const handleDelete = async () => {
    if (!confirmOpen) return;
    const u = confirmOpen.user;
    setConfirmOpen(null);
    try {
      const res = await fetch(`/api/admin/users/${u.username}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (res.ok) {
        setSuccessMsg(`用户「${u.username}」已删除`);
        setTimeout(() => setSuccessMsg(''), 3000);
      }
      fetchUsers(1);
    } catch {
      // 静默
    }
  };

  // ===== Modal 通用关闭 =====
  const closeAllModals = () => {
    setCreateOpen(false);
    setEditOpen(false);
    setConfirmOpen(null);
    setFormError('');
  };

  // ===== 渲染 =====
  return (
    <div className="admin-content">
      {/* 顶栏 */}
      <div className="admin-toolbar">
        <h2 className="admin-page-title">👥 用户管理</h2>
        <div className="admin-toolbar-right">
          <div className="admin-search-box">
            <input
              type="text"
              className="admin-search-input"
              placeholder="搜索用户名..."
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              onKeyDown={handleSearchKeyDown}
            />
            <button className="admin-search-btn" onClick={handleSearch}>🔍</button>
            {keyword && (
              <button className="admin-search-clear" onClick={() => { setKeyword(''); setSearchInput(''); setPage(1); }}>
                ✕
              </button>
            )}
          </div>
          <button className="admin-btn-primary" onClick={() => { setCreateOpen(true); setFormError(''); }}>
            ＋ 新增用户
          </button>
        </div>
      </div>

      {/* 成功提示 */}
      {successMsg && <div className="admin-toast success">{successMsg}</div>}

      {/* 统计 */}
      <div className="admin-table-meta">
        共 <strong>{total}</strong> 个用户
      </div>

      {/* 用户表格 */}
      <div className="admin-table-wrapper">
        <table className="admin-table">
          <thead>
            <tr>
              <th>用户名</th>
              <th>角色</th>
              <th>状态</th>
              <th>展示名</th>
              <th>创建时间</th>
              <th>最后登录</th>
              <th>查询次数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="admin-table-loading">加载中...</td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={8} className="admin-table-empty">暂无用户数据</td></tr>
            ) : (
              users.map(u => (
                <tr key={u.username}>
                  <td className="admin-cell-username">
                    {u.username}
                    {u.username === 'admin' && <span className="admin-role-tag admin">ADMIN</span>}
                  </td>
                  <td>
                    <span className={`admin-role-tag ${u.role}`}>
                      {u.role === 'admin' ? '管理员' : '用户'}
                    </span>
                  </td>
                  <td>
                    <span className={`admin-status-tag ${u.status}`}>
                      {u.status === 'active' ? '🟢 正常' : '🔴 禁用'}
                    </span>
                  </td>
                  <td className="admin-cell-desc">{u.display_name || '-'}</td>
                  <td className="admin-cell-time">{u.created_at?.slice(0, 16) || '-'}</td>
                  <td className="admin-cell-time">{u.last_login_at?.slice(0, 16) || '-'}</td>
                  <td className="admin-cell-num">{u.query_count}</td>
                  <td className="admin-cell-actions">
                    {onViewQueries && (
                      <button
                        className="admin-action-btn"
                        onClick={() => onViewQueries(u.username)}
                        title="查看该用户的所有查询记录"
                      >🔍</button>
                    )}
                    <button
                      className="admin-action-btn"
                      onClick={() => openEdit(u)}
                      title="编辑"
                    >✏️</button>
                    <button
                      className="admin-action-btn"
                      onClick={() => setConfirmOpen({ type: 'reset-password', user: u })}
                      title="重置密码"
                    >🔑</button>
                    <button
                      className="admin-action-btn"
                      onClick={() => setConfirmOpen({ type: 'toggle', user: u })}
                      title={u.status === 'active' ? '禁用' : '启用'}
                    >
                      {u.status === 'active' ? '⛔' : '✅'}
                    </button>
                    {u.username !== 'admin' && (
                      <button
                        className="admin-action-btn danger"
                        onClick={() => setConfirmOpen({ type: 'delete', user: u })}
                        title="删除"
                      >🗑️</button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* 分页 */}
      {total > limit && (
        <div className="admin-pagination">
          <button disabled={page <= 1} onClick={prevPage}>‹ 上一页</button>
          <span>第 {page}/{Math.ceil(total / limit)} 页</span>
          <button disabled={!hasMore} onClick={nextPage}>下一页 ›</button>
        </div>
      )}

      {/* ===== 新增用户 Modal ===== */}
      {createOpen && (
        <div className="modal-overlay" onClick={closeAllModals}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>＋ 新增用户</h2>
              <button className="modal-close" onClick={closeAllModals}>✕</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">用户名 *</label>
                <input
                  className="form-input"
                  value={createForm.username}
                  onChange={e => setCreateForm({ ...createForm, username: e.target.value })}
                  placeholder="登录用用户名"
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label className="form-label">密码 *</label>
                <input
                  type="password"
                  className="form-input"
                  value={createForm.password}
                  onChange={e => setCreateForm({ ...createForm, password: e.target.value })}
                  placeholder="初始密码"
                />
              </div>
              <div className="form-group">
                <label className="form-label">角色</label>
                <select
                  className="form-select"
                  value={createForm.role}
                  onChange={e => setCreateForm({ ...createForm, role: e.target.value })}
                >
                  <option value="user">用户</option>
                  <option value="admin">管理员</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">展示名</label>
                <input
                  className="form-input"
                  value={createForm.display_name}
                  onChange={e => setCreateForm({ ...createForm, display_name: e.target.value })}
                  placeholder="可选，用户可见的展示名称"
                />
              </div>
              {formError && <div className="admin-form-error">{formError}</div>}
              <button
                className="password-submit-btn"
                onClick={handleCreate}
                disabled={saving}
              >
                {saving ? '创建中...' : '创建用户'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ===== 编辑用户 Modal ===== */}
      {editOpen && editUser && (
        <div className="modal-overlay" onClick={closeAllModals}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>✏️ 编辑用户 — {editUser.username}</h2>
              <button className="modal-close" onClick={closeAllModals}>✕</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">角色</label>
                <select
                  className="form-select"
                  value={editForm.role}
                  onChange={e => setEditForm({ ...editForm, role: e.target.value })}
                  disabled={editUser.username === 'admin'}
                >
                  <option value="user">用户</option>
                  <option value="admin">管理员</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">展示名</label>
                <input
                  className="form-input"
                  value={editForm.display_name}
                  onChange={e => setEditForm({ ...editForm, display_name: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">备注</label>
                <input
                  className="form-input"
                  value={editForm.note}
                  onChange={e => setEditForm({ ...editForm, note: e.target.value })}
                />
              </div>
              {formError && <div className="admin-form-error">{formError}</div>}
              <button
                className="password-submit-btn"
                onClick={handleEdit}
                disabled={saving}
              >
                {saving ? '保存中...' : '保存修改'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ===== 确认对话框 ===== */}
      {confirmOpen && (
        <div className="modal-overlay" onClick={closeAllModals}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>
                {confirmOpen.type === 'delete' ? '🗑️ 删除用户' :
                 confirmOpen.type === 'reset-password' ? '🔑 重置密码' :
                 '⛔ 切换状态'}
              </h2>
              <button className="modal-close" onClick={closeAllModals}>✕</button>
            </div>
            <div className="modal-body">
              <p className="admin-confirm-text">
                {confirmOpen.type === 'delete' && `确定要删除用户「${confirmOpen.user.username}」吗？此操作不可撤销。`}
                {confirmOpen.type === 'reset-password' && `确定要将「${confirmOpen.user.username}」的密码恢复为 .env 默认值吗？`}
                {confirmOpen.type === 'toggle' && `确定要${confirmOpen.user.status === 'active' ? '禁用' : '启用'}用户「${confirmOpen.user.username}」吗？`}
              </p>
              <div className="admin-confirm-actions">
                <button className="admin-btn-cancel" onClick={closeAllModals}>取消</button>
                <button
                  className={`admin-btn-confirm ${confirmOpen.type === 'delete' ? 'danger' : ''}`}
                  onClick={
                    confirmOpen.type === 'delete' ? handleDelete :
                    confirmOpen.type === 'reset-password' ? handleResetPassword :
                    handleToggle
                  }
                >
                  {confirmOpen.type === 'delete' ? '确认删除' :
                   confirmOpen.type === 'reset-password' ? '确认重置' :
                   confirmOpen.user.status === 'active' ? '确认禁用' : '确认启用'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
