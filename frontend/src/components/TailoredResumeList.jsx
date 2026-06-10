import { useNavigate } from 'react-router-dom';
import { FiEye, FiDownload, FiBookOpen } from 'react-icons/fi';
import api from '../api/client';

export default function TailoredResumeList({ jobs, onPreviewResume }) {
  const navigate = useNavigate();

  const downloadFile = async (jobId, type) => {
    try {
      const url = type === 'resume'
        ? `/jobs/${jobId}/resume/download`
        : `/jobs/${jobId}/interview/download`;
      const res = await api.get(url, { responseType: 'blob' });
      const blob = new Blob([res.data], { type: 'text/markdown' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = res.headers['content-disposition']?.split('filename=')[1]?.replace(/"/g, '') || `${type}.md`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (err) {
      console.error('Download failed:', err);
    }
  };

  if (!jobs || jobs.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: 'var(--space-12)', color: 'var(--text-muted)' }}>
        <p style={{ fontSize: 'var(--font-size-lg)', marginBottom: 'var(--space-2)' }}>No tailored resumes yet</p>
        <p style={{ fontSize: 'var(--font-size-sm)' }}>Start by tailoring your first resume below!</p>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      {jobs.map((job) => (
        <div key={job.id} className="glass-card" style={{
          padding: 'var(--space-4) var(--space-6)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          flexWrap: 'wrap', gap: 'var(--space-3)',
        }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontWeight: 600, fontSize: 'var(--font-size-base)' }}>
              {job.job_name || 'Processing...'}
            </div>
            <div style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)' }}>
              {job.company_name || '—'} · {new Date(job.created_at).toLocaleDateString()}
            </div>
          </div>
          <span className={`badge badge-${job.status}`}>{job.status}</span>
          {job.status === 'completed' && (
            <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
              <button className="btn btn-secondary btn-sm" onClick={() => onPreviewResume(job.id)}>
                <FiEye size={14} /> View Resume
              </button>
              <button className="btn btn-secondary btn-sm" onClick={() => navigate(`/interview/${job.id}`)}>
                <FiBookOpen size={14} /> Interview Prep
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => downloadFile(job.id, 'resume')} title="Download Resume">
                <FiDownload size={14} /> Resume
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => downloadFile(job.id, 'interview')} title="Download Interview">
                <FiDownload size={14} /> Interview
              </button>
            </div>
          )}
          {job.status === 'failed' && job.error_message && (
            <p style={{ width: '100%', fontSize: 'var(--font-size-xs)', color: 'var(--error)', marginTop: 'var(--space-1)' }}>
              {job.error_message}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
