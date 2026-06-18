import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import api from '../services/api';
import Navbar from '../components/Navbar';
import { FiArrowLeft, FiDownload } from 'react-icons/fi';

export default function InterviewMaterialsPage() {
  const { jobId } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get(`/jobs/${jobId}/interview/content`);
        setData(res.data);
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to load interview materials.');
      } finally {
        setLoading(false);
      }
    })();
  }, [jobId]);

  const handleDownload = async () => {
    try {
      const res = await api.get(`/jobs/${jobId}/interview/download`, { responseType: 'blob' });
      const blob = new Blob([res.data], { type: 'text/markdown' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `interview_materials_${data?.company_name || 'prep'}.md`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (err) {
      console.error('Download failed:', err);
    }
  };

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)' }}>
      <Navbar />
      <main className="container" style={{ padding: 'var(--space-8) var(--space-6)', maxWidth: 900 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-6)', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
          <Link to="/dashboard" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)' }}>
            <FiArrowLeft size={14} /> Back to Dashboard
          </Link>
          {data && (
            <button className="btn btn-secondary btn-sm" onClick={handleDownload}>
              <FiDownload size={14} /> Download
            </button>
          )}
        </div>

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-16)' }}>
            <div className="spinner" style={{ width: 40, height: 40 }} />
          </div>
        ) : error ? (
          <div style={{ textAlign: 'center', padding: 'var(--space-12)', color: 'var(--error)' }}>
            <p>{error}</p>
          </div>
        ) : (
          <>
            <div style={{ marginBottom: 'var(--space-6)' }}>
              <h1 style={{ fontSize: 'var(--font-size-2xl)', marginBottom: 'var(--space-2)' }}>
                Interview Preparation
              </h1>
              <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)' }}>
                {data.job_name} at {data.company_name} · {data.created_at ? new Date(data.created_at).toLocaleDateString() : ''}
              </p>
            </div>
            <div className="glass-card" style={{ padding: 'var(--space-8)' }}>
              <div className="markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.content}</ReactMarkdown>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
