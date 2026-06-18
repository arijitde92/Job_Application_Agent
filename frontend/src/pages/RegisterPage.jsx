import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import api from '../services/api';
import { useAuth } from '../context/AuthContext';
import { FiUser, FiMail, FiLock, FiArrowLeft } from 'react-icons/fi';

export default function RegisterPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ first_name: '', last_name: '', email: '', password: '', confirmPassword: '' });
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState('');
  const [loading, setLoading] = useState(false);

  const validate = () => {
    const errs = {};
    if (!form.first_name.trim()) errs.first_name = 'First name is required';
    if (!form.last_name.trim()) errs.last_name = 'Last name is required';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errs.email = 'Invalid email address';
    if (form.password.length < 8) errs.password = 'Password must be at least 8 characters';
    if (form.password !== form.confirmPassword) errs.confirmPassword = 'Passwords do not match';
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!validate()) return;
    setServerError('');
    setLoading(true);
    try {
      const res = await api.post('/auth/register', {
        first_name: form.first_name, last_name: form.last_name,
        email: form.email, password: form.password,
      });
      login(res.data.access_token);
      navigate('/dashboard');
    } catch (err) {
      setServerError(err.response?.data?.detail || 'Registration failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (field) => (e) => {
    setForm({ ...form, [field]: e.target.value });
    if (errors[field]) setErrors({ ...errors, [field]: '' });
  };

  const fields = [
    { key: 'first_name', label: 'First Name', type: 'text', icon: <FiUser size={16} />, placeholder: 'John' },
    { key: 'last_name', label: 'Last Name', type: 'text', icon: <FiUser size={16} />, placeholder: 'Doe' },
    { key: 'email', label: 'Email', type: 'email', icon: <FiMail size={16} />, placeholder: 'you@example.com' },
    { key: 'password', label: 'Password', type: 'password', icon: <FiLock size={16} />, placeholder: 'At least 8 characters' },
    { key: 'confirmPassword', label: 'Confirm Password', type: 'password', icon: <FiLock size={16} />, placeholder: 'Re-enter your password' },
  ];

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 'var(--space-6)', background: 'linear-gradient(135deg, #0a0a0f, #1a1040)' }}>
      <div className="glass-card" style={{ padding: 'var(--space-8)', maxWidth: 480, width: '100%' }}>
        <Link to="/" style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-6)' }}>
          <FiArrowLeft size={14} /> Back to home
        </Link>
        <h1 style={{ fontSize: 'var(--font-size-2xl)', marginBottom: 'var(--space-2)' }}>Create Account</h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-6)' }}>
          Join and start tailoring your resumes with AI
        </p>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          {fields.map((f) => (
            <div className="input-group" key={f.key}>
              <label htmlFor={`reg-${f.key}`}>{f.label}</label>
              <div style={{ position: 'relative' }}>
                <span style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }}>{f.icon}</span>
                <input id={`reg-${f.key}`} type={f.type} className={`input-field ${errors[f.key] ? 'error' : ''}`}
                  placeholder={f.placeholder} value={form[f.key]} onChange={handleChange(f.key)}
                  style={{ paddingLeft: 36 }} />
              </div>
              {errors[f.key] && <span className="input-error">{errors[f.key]}</span>}
              {f.key === 'confirmPassword' && form.password && form.confirmPassword && form.password === form.confirmPassword && (
                <span className="input-success" style={{ fontSize: 'var(--font-size-xs)', color: 'var(--success)', marginTop: 'var(--space-1)', display: 'block' }}>
                  Passwords Matched
                </span>
              )}
            </div>
          ))}
          {serverError && <p className="input-error">{serverError}</p>}
          <button type="submit" className="btn btn-primary btn-lg" disabled={loading} id="register-submit">
            {loading ? <div className="spinner" /> : 'Create Account'}
          </button>
        </form>

        <p style={{ textAlign: 'center', marginTop: 'var(--space-6)', fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>
          Already have an account? <Link to="/" style={{ fontWeight: 600 }}>Sign in</Link>
        </p>
      </div>
    </div>
  );
}
