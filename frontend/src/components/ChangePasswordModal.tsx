/** 星宝语料场景查询 — 修改密码 Modal */

import { useState } from 'react';
import { authHeaders } from '../api/auth';

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ChangePasswordModal({ open, onClose }: Props) {
  const [oldPwd, setOldPwd] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  if (!open) return null;

  const handleSubmit = async () => {
    // 清空消息
    setMessage(null);

    // 前端校验
    if (!oldPwd) {
      setMessage({ type: 'error', text: '请输入当前密码' });
      return;
    }
    if (!newPwd) {
      setMessage({ type: 'error', text: '请输入新密码' });
      return;
    }
    if (newPwd.length < 4) {
      setMessage({ type: 'error', text: '新密码至少4位' });
      return;
    }
    if (newPwd !== confirmPwd) {
      setMessage({ type: 'error', text: '两次输入的新密码不一致' });
      return;
    }

    setSaving(true);
    try {
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setMessage({ type: 'success', text: data.message || '密码修改成功' });
        setOldPwd('');
        setNewPwd('');
        setConfirmPwd('');
      } else {
        setMessage({ type: 'error', text: data.detail || data.message || '修改失败' });
      }
    } catch {
      setMessage({ type: 'error', text: '请求失败，请稍后重试' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content password-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>🔑 修改密码</h2>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>

        <div className="modal-body">
          {message && (
            <div className={`password-message ${message.type}`}>
              {message.type === 'success' ? '✅ ' : '❌ '}{message.text}
            </div>
          )}

          <div className="form-group">
            <label className="form-label">当前密码</label>
            <input
              type="password"
              className="form-input"
              placeholder="输入当前密码"
              value={oldPwd}
              onChange={e => setOldPwd(e.target.value)}
              autoFocus
            />
          </div>

          <div className="form-group">
            <label className="form-label">新密码</label>
            <input
              type="password"
              className="form-input"
              placeholder="输入新密码（至少4位）"
              value={newPwd}
              onChange={e => setNewPwd(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label className="form-label">确认新密码</label>
            <input
              type="password"
              className="form-input"
              placeholder="再次输入新密码"
              value={confirmPwd}
              onChange={e => setConfirmPwd(e.target.value)}
            />
          </div>

          <button
            className="password-submit-btn"
            onClick={handleSubmit}
            disabled={saving}
          >
            {saving ? '修改中...' : '确认修改'}
          </button>
        </div>
      </div>
    </div>
  );
}
