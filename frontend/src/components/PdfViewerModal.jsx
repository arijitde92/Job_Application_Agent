import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { FiDownload, FiExternalLink, FiFileText, FiRefreshCw, FiX } from 'react-icons/fi';
import api from '../services/api';
import { filenameFromDisposition, saveBlob } from '../services/download';

/**
 * Pop-up viewer for a job's tailored resume PDF.
 *
 * The PDF is compiled on the server from the stored LaTeX on demand (it is
 * never stored), so loading can take a few seconds. Download reuses the PDF
 * already fetched for viewing — no second compile.
 */
export default function PdfViewerModal({ job, onClose, onViewText }) {
  const [pdf, setPdf] = useState(null); // { url, blob, filename }
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  const fallbackName = `${job?.company_name || 'tailored'}_${job?.job_name || 'resume'}_resume.pdf`
    .replace(/\s+/g, '_');

  useEffect(() => {
    let cancelled = false;
    let objectUrl = null;
    api.get(`/jobs/${job.id}/resume/pdf`, { responseType: 'blob' })
      .then((res) => {
        if (cancelled) return;
        const blob = new Blob([res.data], { type: 'application/pdf' });
        objectUrl = URL.createObjectURL(blob);
        setPdf({
          url: objectUrl,
          blob,
          filename: filenameFromDisposition(res.headers['content-disposition'], fallbackName),
        });
        setLoading(false);
      })
      .catch(async (err) => {
        let detail = null;
        // Error bodies arrive as a Blob because of responseType: 'blob'.
        try { detail = JSON.parse(await err.response?.data?.text?.())?.detail; } catch { /* not JSON */ }
        if (cancelled) return;
        setError(detail || 'The PDF could not be loaded. Please try again.');
        setLoading(false);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [job.id, attempt, fallbackName]);

  const retry = () => {
    setPdf(null);
    setError(null);
    setLoading(true);
    setAttempt((n) => n + 1);
  };

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const title = job?.job_name
    ? `${job.job_name}${job.company_name ? ` at ${job.company_name}` : ''}`
    : 'Tailored Resume';

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-pdf"
        role="dialog"
        aria-modal="true"
        aria-label={`Tailored resume PDF: ${title}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-pdf-header">
          <div className="modal-pdf-title">
            <div style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>Tailored Resume (PDF)</div>
            <h2 title={title}>{title}</h2>
          </div>
          <div className="modal-pdf-actions">
            <button
              className="btn btn-primary btn-sm"
              onClick={() => pdf && saveBlob(pdf.blob, pdf.filename)}
              disabled={!pdf}
            >
              <FiDownload size={14} /> Download PDF
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => pdf && window.open(pdf.url, '_blank', 'noopener')}
              disabled={!pdf}
              title="Open in a new tab"
            >
              <FiExternalLink size={14} /> Open
            </button>
            <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
              <FiX size={20} />
            </button>
          </div>
        </div>

        <div className="modal-pdf-body">
          {loading ? (
            <div className="modal-pdf-state">
              <div className="spinner" style={{ width: 32, height: 32 }} />
              <span>Generating your PDF…</span>
            </div>
          ) : error ? (
            <div className="modal-pdf-state">
              <span style={{ color: 'var(--error)' }}>{error}</span>
              <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', justifyContent: 'center' }}>
                <button className="btn btn-secondary btn-sm" onClick={retry}>
                  <FiRefreshCw size={14} /> Retry
                </button>
                {onViewText && (
                  <button className="btn btn-ghost btn-sm" onClick={onViewText}>
                    <FiFileText size={14} /> View text version
                  </button>
                )}
              </div>
            </div>
          ) : (
            <iframe src={pdf.url} title={`Tailored resume PDF: ${title}`} />
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
