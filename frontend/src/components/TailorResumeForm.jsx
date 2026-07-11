import { useState } from 'react';
import api from '../services/api';
import { FiLink, FiSend, FiEye } from 'react-icons/fi';

export default function TailorResumeForm({ githubProfiles, resumes, onJobCreated, onPreviewUploadedResume }) {
  const [jobUrl, setJobUrl] = useState('');
  const [githubId, setGithubId] = useState('');
  const [resumeId, setResumeId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!jobUrl || !resumeId) {
      setError('Job URL and resume are required.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const payload = {
        linkedin_job_url: jobUrl,
        resume_id: parseInt(resumeId),
      };
      if (githubId) payload.github_profile_id = parseInt(githubId);
      const res = await api.post('/jobs/tailor', payload);
      setJobUrl('');
      onJobCreated(res.data, !!githubId);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to start tailoring.');
    } finally {
      setLoading(false);
    }
  };

  const canPreview = resumeId && resumes.length > 0;

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div className="input-group">
        <label htmlFor="job-url">LinkedIn Job URL</label>
        <div style={{ position: 'relative' }}>
          <FiLink size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input id="job-url" type="url" className="input-field" placeholder="https://www.linkedin.com/jobs/view/..."
            value={jobUrl} onChange={(e) => setJobUrl(e.target.value)} required
            style={{ paddingLeft: 36 }} />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
        <div className="input-group" style={{ flex: 1, minWidth: 200 }}>
          <label htmlFor="github-select">GitHub Profile (optional)</label>
          <select id="github-select" className="input-field" value={githubId} onChange={(e) => setGithubId(e.target.value)}>
            <option value="">None / no GitHub</option>
            {githubProfiles.map((p) => (
              <option key={p.id} value={p.id}>{p.github_username}</option>
            ))}
          </select>
        </div>
        <div className="input-group" style={{ flex: 1, minWidth: 200 }}>
          <label htmlFor="resume-select">Resume</label>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <select id="resume-select" className="input-field" value={resumeId} onChange={(e) => setResumeId(e.target.value)} required style={{ flex: 1 }}>
              <option value="">Select a resume...</option>
              {resumes.map((r) => (
                <option key={r.id} value={r.id}>{r.original_filename}</option>
              ))}
            </select>
            {canPreview && (
              <button type="button" className="btn btn-secondary" onClick={() => onPreviewUploadedResume(parseInt(resumeId))} title="Preview Resume">
                <FiEye size={16} />
              </button>
            )}
          </div>
        </div>
      </div>

      {error && <p className="input-error">{error}</p>}

      <button type="submit" className="btn btn-success btn-lg" disabled={loading || !jobUrl || !resumeId} id="tailor-submit">
        {loading ? <div className="spinner" /> : <><FiSend size={18} /> Tailor My Resume</>}
      </button>
    </form>
  );
}
