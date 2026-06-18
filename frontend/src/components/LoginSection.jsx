import { useState } from 'react';
import api from '../services/api';
import { useAuth } from '../context/AuthContext';
import { FiMail, FiLock } from 'react-icons/fi';

export default function LoginSection() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await api.post('/auth/login', { email, password });
      login(res.data.access_token);
    } catch (err) {
      setError(err.response?.data?.detail || 'Login failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div className="input-group">
        <label htmlFor="login-email">Email</label>
        <div style={{ position: 'relative' }}>
          <FiMail size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input id="login-email" type="email" className="input-field" placeholder="you@example.com"
            value={email} onChange={(e) => setEmail(e.target.value)} required
            style={{ paddingLeft: 36 }} />
        </div>
      </div>
      <div className="input-group">
        <label htmlFor="login-password">Password</label>
        <div style={{ position: 'relative' }}>
          <FiLock size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input id="login-password" type="password" className="input-field" placeholder="Enter your password"
            value={password} onChange={(e) => setPassword(e.target.value)} required
            style={{ paddingLeft: 36 }} />
        </div>
      </div>
      {error && <p className="input-error">{error}</p>}
      <button type="submit" className="btn btn-primary btn-lg" disabled={loading} id="login-submit">
        {loading ? <div className="spinner" /> : 'Sign In'}
      </button>
    </form>
  );
}
