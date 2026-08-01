import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import api from '../services/api';
import { FiX } from 'react-icons/fi';

export default function ResumePreviewModal({ jobId, resumeId, hasPdf = false, onClose }) {
  const [content, setContent] = useState('');
  const [pdfUrl, setPdfUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const [title, setTitle] = useState('Resume Preview');

  useEffect(() => {
    let objectUrl = null;
    const fetchMarkdown = async () => {
      const res = await api.get(`/jobs/${jobId}/resume/content`);
      setContent(res.data.content);
    };
    const fetchContent = async () => {
      setLoading(true);
      try {
        if (jobId) {
          setTitle('Tailored Resume');
          if (hasPdf) {
            // Render the generated PDF in-browser; fall back to the markdown
            // view on any failure (e.g. a stale jobs list claiming a PDF).
            try {
              const res = await api.get(`/jobs/${jobId}/resume/pdf`, { responseType: 'blob' });
              objectUrl = URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
              setPdfUrl(objectUrl);
            } catch {
              await fetchMarkdown();
            }
          } else {
            await fetchMarkdown();
          }
        } else if (resumeId) {
          // Preview uploaded resume
          const res = await api.get(`/resumes/${resumeId}/content`);
          setContent(res.data.content);
          setTitle(res.data.filename || 'Resume Preview');
        }
      } catch (err) {
        setContent('Failed to load resume content.');
      } finally {
        setLoading(false);
      }
    };
    fetchContent();
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [jobId, resumeId, hasPdf]);

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-6)' }}>
          <h2 style={{ fontSize: 'var(--font-size-xl)' }}>{title}</h2>
          <button className="btn btn-ghost btn-icon" onClick={onClose}><FiX size={20} /></button>
        </div>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-12)' }}>
            <div className="spinner" style={{ width: 32, height: 32 }} />
          </div>
        ) : pdfUrl ? (
          <iframe
            src={pdfUrl}
            title="Tailored Resume PDF"
            style={{ width: '100%', height: '70vh', border: 'none', borderRadius: 'var(--radius-md, 8px)' }}
          />
        ) : (
          <div className="markdown-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
