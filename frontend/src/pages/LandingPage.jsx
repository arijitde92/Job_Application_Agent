import { Link } from 'react-router-dom';
import LoginSection from '../components/LoginSection';
import { FiUpload, FiLink, FiCpu, FiTarget, FiGithub, FiSearch, FiZap, FiFileText } from 'react-icons/fi';

const PROBLEMS = [
  { icon: <FiFileText size={28} />, text: 'Spending hours customizing resumes for each application?' },
  { icon: <FiSearch size={28} />, text: 'Struggling to pass ATS keyword screening?' },
  { icon: <FiGithub size={28} />, text: 'Not sure which projects to highlight for each role?' },
  { icon: <FiLink size={28} />, text: 'LinkedIn job postings are hard to decode quickly' },
];

const STEPS = [
  { num: '01', icon: <FiUpload size={32} />, title: 'Upload & Link', desc: 'Upload your resume and link your GitHub profile' },
  { num: '02', icon: <FiLink size={32} />, title: 'Paste Job URL', desc: 'Paste a LinkedIn job posting URL' },
  { num: '03', icon: <FiCpu size={32} />, title: 'AI Tailoring', desc: 'AI agents analyze, match, and tailor your resume' },
];

const FEATURES = [
  { icon: <FiTarget size={24} />, title: 'ATS Optimization', desc: 'Embed the right keywords to pass automated screening systems' },
  { icon: <FiGithub size={24} />, title: 'GitHub Analysis', desc: 'Automatically analyze your repos to highlight relevant projects' },
  { icon: <FiSearch size={24} />, title: 'Smart Matching', desc: 'AI matches your experience with job requirements precisely' },
  { icon: <FiZap size={24} />, title: 'Instant Tailoring', desc: 'Get a perfectly tailored resume in minutes, not hours' },
];

export default function LandingPage() {
  return (
    <div style={{ minHeight: '100vh' }}>
      {/* ── Hero ──────────────────────────────────────────────────── */}
      <section style={{
        position: 'relative', overflow: 'hidden',
        padding: 'var(--space-20) var(--space-6)',
        background: 'linear-gradient(135deg, #0a0a0f 0%, #1a1040 40%, #0d1b2a 100%)',
        textAlign: 'center',
      }}>
        {/* Gradient orbs */}
        <div style={{ position: 'absolute', top: '-20%', left: '-10%', width: 500, height: 500, borderRadius: '50%', background: 'radial-gradient(circle, rgba(108,99,255,0.15), transparent 70%)', filter: 'blur(80px)' }} />
        <div style={{ position: 'absolute', bottom: '-20%', right: '-10%', width: 400, height: 400, borderRadius: '50%', background: 'radial-gradient(circle, rgba(0,212,170,0.1), transparent 70%)', filter: 'blur(80px)' }} />

        <div className="container" style={{ position: 'relative', zIndex: 1 }}>
          <span className="badge" style={{ background: 'var(--accent-primary-glow)', color: 'var(--accent-primary)', fontSize: 'var(--font-size-sm)', padding: '6px 16px', marginBottom: 'var(--space-6)', display: 'inline-flex' }}>
            ✨ AI-Powered Resume Agent
          </span>
          <h1 style={{
            fontSize: 'clamp(2.5rem, 5vw, 4rem)', fontWeight: 900, lineHeight: 1.1,
            marginBottom: 'var(--space-6)',
            background: 'linear-gradient(135deg, #e8e8f0, #b4b0ff, #00d4aa)',
            WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
          }}>
            Your Dream Job Deserves<br />a Perfect Resume
          </h1>
          <p style={{ fontSize: 'var(--font-size-xl)', color: 'var(--text-secondary)', maxWidth: 600, margin: '0 auto var(--space-8)', lineHeight: 1.7 }}>
            AI agents analyze your GitHub projects, understand job requirements,
            and tailor your resume for maximum impact.
          </p>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 'var(--space-4)' }}>
            <a href="#auth-section" className="btn btn-primary btn-lg">Get Started</a>
            <a href="#how-it-works" className="btn btn-secondary btn-lg">Learn More</a>
          </div>
        </div>
      </section>

      {/* ── Problems ──────────────────────────────────────────────── */}
      <section style={{ padding: 'var(--space-20) var(--space-6)', background: 'var(--bg-secondary)' }}>
        <div className="container">
          <h2 style={{ textAlign: 'center', fontSize: 'var(--font-size-3xl)', marginBottom: 'var(--space-12)' }}>
            Sound <span style={{ color: 'var(--accent-primary)' }}>familiar</span>?
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: 'var(--space-6)' }}>
            {PROBLEMS.map((p, i) => (
              <div key={i} className="glass-card animate-fade-in-up" style={{
                padding: 'var(--space-6)', textAlign: 'center',
                animationDelay: `${i * 100}ms`, animationFillMode: 'backwards',
              }}>
                <div style={{ color: 'var(--accent-primary)', marginBottom: 'var(--space-3)' }}>{p.icon}</div>
                <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)', lineHeight: 1.6 }}>{p.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How It Works ──────────────────────────────────────────── */}
      <section id="how-it-works" style={{ padding: 'var(--space-20) var(--space-6)' }}>
        <div className="container">
          <h2 style={{ textAlign: 'center', fontSize: 'var(--font-size-3xl)', marginBottom: 'var(--space-4)' }}>
            How It <span style={{ color: 'var(--accent-secondary)' }}>Works</span>
          </h2>
          <p style={{ textAlign: 'center', color: 'var(--text-secondary)', marginBottom: 'var(--space-12)', maxWidth: 500, margin: '0 auto var(--space-12)' }}>
            Three simple steps to a perfectly tailored resume
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 'var(--space-8)' }}>
            {STEPS.map((s, i) => (
              <div key={i} style={{ textAlign: 'center' }}>
                <span style={{ display: 'block', marginBottom: 'var(--space-2)', fontSize: 'var(--font-size-xs)', color: 'var(--accent-primary)', fontWeight: 700, letterSpacing: '0.05em' }}>
                  STEP {s.num}
                </span>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 64, height: 64, borderRadius: '50%',
                  background: 'linear-gradient(135deg, var(--accent-primary), #8b7dff)',
                  marginBottom: 'var(--space-4)', boxShadow: '0 8px 30px var(--accent-primary-glow)',
                  color: 'white',
                }}>
                  {s.icon}
                </span>
                <h3 style={{ fontSize: 'var(--font-size-xl)', marginBottom: 'var(--space-2)' }}>{s.title}</h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)' }}>{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Features ──────────────────────────────────────────────── */}
      <section style={{ padding: 'var(--space-20) var(--space-6)', background: 'var(--bg-secondary)' }}>
        <div className="container">
          <h2 style={{ textAlign: 'center', fontSize: 'var(--font-size-3xl)', marginBottom: 'var(--space-12)' }}>
            Powerful <span style={{ color: 'var(--accent-primary)' }}>Features</span>
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: 'var(--space-6)' }}>
            {FEATURES.map((f, i) => (
              <div key={i} className="glass-card" style={{ padding: 'var(--space-6)' }}>
                <div style={{
                  width: 48, height: 48, borderRadius: 'var(--radius-md)',
                  background: 'var(--accent-primary-glow)', display: 'flex',
                  alignItems: 'center', justifyContent: 'center',
                  color: 'var(--accent-primary)', marginBottom: 'var(--space-4)',
                }}>
                  {f.icon}
                </div>
                <h3 style={{ fontSize: 'var(--font-size-lg)', marginBottom: 'var(--space-2)' }}>{f.title}</h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)', lineHeight: 1.6 }}>{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Auth Section ──────────────────────────────────────────── */}
      <section id="auth-section" style={{ padding: 'var(--space-20) var(--space-6)' }}>
        <div className="container">
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
            gap: 'var(--space-8)', maxWidth: 800, margin: '0 auto',
          }}>
            {/* Login side */}
            <div className="glass-card" style={{ padding: 'var(--space-8)' }}>
              <h3 style={{ fontSize: 'var(--font-size-2xl)', marginBottom: 'var(--space-2)' }}>Welcome Back</h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-6)' }}>
                Sign in to your account
              </p>
              <LoginSection />
            </div>

            {/* Register side */}
            <div className="glass-card" style={{
              padding: 'var(--space-8)', display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', textAlign: 'center',
            }}>
              <h3 style={{ fontSize: 'var(--font-size-2xl)', marginBottom: 'var(--space-2)' }}>New Here?</h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-6)', maxWidth: 280 }}>
                Create an account and start tailoring your resume with AI
              </p>
              <Link to="/register" className="btn btn-primary btn-lg" id="go-register-btn">
                Create Account
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ────────────────────────────────────────────────── */}
      <footer style={{
        textAlign: 'center', padding: 'var(--space-8)', borderTop: '1px solid var(--border-subtle)',
        color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)',
      }}>
        Built with CrewAI, FastAPI & React · {new Date().getFullYear()}
      </footer>
    </div>
  );
}
