import { useAuth } from '../context/AuthContext';
import { useNavigate, Link } from 'react-router-dom';
import { FiLogOut, FiZap } from 'react-icons/fi';

export default function Navbar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  return (
    <nav style={{
      position: 'sticky', top: 0, zIndex: 100,
      background: 'rgba(10, 10, 15, 0.85)', backdropFilter: 'blur(20px)',
      borderBottom: '1px solid var(--border-subtle)',
      padding: 'var(--space-4) var(--space-6)',
    }}>
      <div className="container" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Link to={user ? '/dashboard' : '/'} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--text-primary)', textDecoration: 'none' }}>
          <FiZap size={22} color="var(--accent-primary)" />
          <span style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            JobAgent
          </span>
        </Link>
        {user && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)' }}>
            <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>
              {user.first_name} {user.last_name}
            </span>
            <button className="btn btn-ghost btn-sm" onClick={handleLogout} id="logout-btn">
              <FiLogOut size={16} /> Logout
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
