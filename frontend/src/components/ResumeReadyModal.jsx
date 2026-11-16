import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiEye, FiDownload, FiBookOpen, FiX } from 'react-icons/fi';
import { downloadFromApi, jobHasPdf } from '../services/download';

export default function ResumeReadyModal({ job, onClose, onPreviewResume }) {
  const navigate = useNavigate();
  const [downloading, setDownloading] = useState(null);

  const downloadFile = async (type) => {
    setDownloading(type);
    try {
      // The resume is the PDF (compiled on demand) when available, otherwise
      // the stored .docx/.md — the server sets the type and filename.
      const url = type === 'resume'
        ? `/jobs/${job.id}/resume/download`
        : `/jobs/${job.id}/interview/download`;
      await downloadFromApi(url, `${type}_${job.company_name || 'output'}`);
    } catch (err) {
      console.error('Download failed:', err);
      alert('Download failed. Please try again.');
    } finally {
      setDownloading(null);
    }
  };

  const icon = (type, size) => (downloading === type
    ? <span className="spinner" style={{ width: size, height: size }} />
    : <FiDownload size={size} />);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ textAlign: 'center', maxWidth: 500 }}>
        <button className="btn btn-ghost btn-icon" onClick={onClose} style={{ position: 'absolute', top: 'var(--space-4)', right: 'var(--space-4)' }}>
          <FiX size={20} />
        </button>

        <div style={{ fontSize: '3rem', marginBottom: 'var(--space-4)' }}>🎉</div>
        <h2 style={{ fontSize: 'var(--font-size-2xl)', marginBottom: 'var(--space-2)' }}>
          Your tailored resume is ready!
        </h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-8)' }}>
          {job.job_name} at {job.company_name}
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <button className="btn btn-primary btn-lg" onClick={() => onPreviewResume(job.id)} style={{ width: '100%' }}>
            <FiEye size={18} /> View Tailored Resume
          </button>
          <button className="btn btn-secondary btn-lg" onClick={() => navigate(`/interview/${job.id}`)} style={{ width: '100%' }}>
            <FiBookOpen size={18} /> View Interview Materials
          </button>
          <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
            <button className="btn btn-ghost btn-lg" onClick={() => downloadFile('resume')}
              disabled={downloading === 'resume'} style={{ flex: 1 }}>
              {icon('resume', 16)} {jobHasPdf(job) ? 'Resume (PDF)' : 'Resume'}
            </button>
            <button className="btn btn-ghost btn-lg" onClick={() => downloadFile('interview')}
              disabled={downloading === 'interview'} style={{ flex: 1 }}>
              {icon('interview', 16)} Interview
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
