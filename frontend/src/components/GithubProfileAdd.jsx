import { useState } from 'react';
import api from '../services/api';
import { FiGithub, FiTrash2, FiPlus } from 'react-icons/fi';

export default function GithubProfileAdd({ profiles, onRefresh }) {
  const [username, setUsername] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!username.trim()) return;
    setError('');
    setLoading(true);
    try {
      await api.post('/github', { github_username: username.trim() });
      setUsername('');
      onRefresh();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to add GitHub profile.');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.delete(`/github/${id}`);
      onRefresh();
    } catch (err) {
      setError(err.response?.data?.detail || 'Cannot delete profile.');
    }
  };

  return (
    <div className="glass-card" style={{ padding: 'var(--space-6)', flex: 1, minWidth: 300 }}>
      <h3 style={{ fontSize: 'var(--font-size-lg)', marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        <FiGithub size={20} color="var(--accent-primary)" /> GitHub Profiles
      </h3>

      <form onSubmit={handleAdd} style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
        <input
          type="text" className="input-field" placeholder="GitHub username"
          value={username} onChange={(e) => setUsername(e.target.value)}
          style={{ flex: 1 }} id="github-username-input"
        />
        <button type="submit" className="btn btn-primary" disabled={loading || !username.trim()} id="add-github-btn">
          {loading ? <div className="spinner" /> : <><FiPlus size={16} /> Add</>}
        </button>
      </form>

      {error && <p className="input-error" style={{ marginBottom: 'var(--space-3)' }}>{error}</p>}

      {profiles.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {profiles.map((p) => (
            <div key={p.id} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: 'var(--space-3) var(--space-4)', background: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-sm)', fontSize: 'var(--font-size-sm)',
            }}>
              <a href={p.github_url} target="_blank" rel="noopener noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <FiGithub size={14} /> {p.github_username}
              </a>
              <button className="btn btn-ghost btn-icon" onClick={() => handleDelete(p.id)} title="Delete">
                <FiTrash2 size={14} color="var(--error)" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
