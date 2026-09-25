import { useState, useRef } from 'react';
import api from '../services/api';
import { FiLink, FiSend, FiEye, FiFileText, FiUploadCloud } from 'react-icons/fi';

// Mirrors the backend limits in app/services/extractors/text_job_extractor.py.
const MIN_JD_CHARS = 50;
const MAX_JD_CHARS = 50000;
const JD_FILE_PATTERN = /\.(pdf|docx|md|txt)$/i;

const nonWhitespaceLength = (text) => text.replace(/\s/g, '').length;

export default function TailorResumeForm({ githubProfiles, resumes, onJobCreated, onPreviewUploadedResume }) {
  // Job source: 'url' (LinkedIn job URL) or 'text' (pasted / uploaded description).
  const [jobSource, setJobSource] = useState('url');
  const [jobUrl, setJobUrl] = useState('');
  const [jobText, setJobText] = useState('');
  const [jobTextFile, setJobTextFile] = useState('');
  const [extracting, setExtracting] = useState(false);
  const [githubId, setGithubId] = useState('');
  const [resumeId, setResumeId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const fileInputRef = useRef(null);

  const hasJob = jobSource === 'url' ? !!jobUrl.trim() : !!jobText.trim();

  const handleJobFile = async (file) => {
    if (!file) return;
    if (!JD_FILE_PATTERN.test(file.name)) {
      setError('Only PDF (.pdf), Word (.docx), Markdown (.md) or text (.txt) files are accepted.');
      return;
    }
    setError('');
    setExtracting(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await api.post('/jobs/description/extract', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setJobText(res.data.text);
      setJobTextFile(res.data.filename);
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not read the job description file.');
    } finally {
      setExtracting(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!hasJob || !resumeId) {
      setError(jobSource === 'url'
        ? 'Job URL and resume are required.'
        : 'Job description and resume are required.');
      return;
    }
    if (jobSource === 'text' && nonWhitespaceLength(jobText) < MIN_JD_CHARS) {
      setError('The job description is too short — paste the full job posting.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const payload = { resume_id: parseInt(resumeId) };
      if (jobSource === 'url') payload.linkedin_job_url = jobUrl.trim();
      else payload.job_description_text = jobText.trim();
      if (githubId) payload.github_profile_id = parseInt(githubId);
      const res = await api.post('/jobs/tailor', payload);
      setJobUrl('');
      setJobText('');
      setJobTextFile('');
      onJobCreated(res.data, !!githubId);
    } catch (err) {
      const detail = err.response?.data?.detail;
      // Pydantic validation errors arrive as a list of {msg, ...} objects.
      setError(Array.isArray(detail)
        ? detail.map((d) => d.msg.replace(/^Value error, /, '')).join(' ')
        : detail || 'Failed to start tailoring.');
    } finally {
      setLoading(false);
    }
  };

  const switchSource = (source) => {
    setJobSource(source);
    setError('');
  };

  const canPreview = resumeId && resumes.length > 0;

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div className="input-group">
        <label id="job-source-label">Job posting</label>
        <div className="segmented" role="radiogroup" aria-labelledby="job-source-label">
          <button type="button" role="radio" aria-checked={jobSource === 'url'} id="job-source-url"
            onClick={() => switchSource('url')}>
            <FiLink size={14} /> LinkedIn URL
          </button>
          <button type="button" role="radio" aria-checked={jobSource === 'text'} id="job-source-text"
            onClick={() => switchSource('text')}>
            <FiFileText size={14} /> Job description
          </button>
        </div>
      </div>

      {jobSource === 'url' ? (
        <div className="input-group">
          <label htmlFor="job-url">LinkedIn Job URL</label>
          <div style={{ position: 'relative' }}>
            <FiLink size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input id="job-url" type="url" className="input-field" placeholder="https://www.linkedin.com/jobs/view/..."
              value={jobUrl} onChange={(e) => setJobUrl(e.target.value)} required
              style={{ paddingLeft: 36 }} />
          </div>
        </div>
      ) : (
        <div className="input-group">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
            <label htmlFor="job-text">Job description</label>
            <button type="button" className="btn btn-secondary btn-sm" id="job-file-upload"
              onClick={() => fileInputRef.current?.click()} disabled={extracting}>
              {extracting ? <div className="spinner" /> : <><FiUploadCloud size={14} /> Upload file</>}
            </button>
            <input ref={fileInputRef} type="file" accept=".pdf,.docx,.md,.txt" hidden
              onChange={(e) => { handleJobFile(e.target.files[0]); e.target.value = ''; }} />
          </div>
          <textarea id="job-text" className="input-field" rows={10} maxLength={MAX_JD_CHARS}
            placeholder="Paste the full job posting here — title, company, responsibilities and requirements — or upload a .pdf, .docx, .md or .txt file."
            value={jobText} onChange={(e) => setJobText(e.target.value)} required />
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--space-2)', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
            <span>{jobTextFile ? `Loaded from ${jobTextFile} — review and edit before submitting.` : ''}</span>
            <span>{jobText.length.toLocaleString()} / {MAX_JD_CHARS.toLocaleString()}</span>
          </div>
        </div>
      )}

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

      <button type="submit" className="btn btn-success btn-lg" disabled={loading || extracting || !hasJob || !resumeId} id="tailor-submit">
        {loading ? <div className="spinner" /> : <><FiSend size={18} /> Tailor My Resume</>}
      </button>
    </form>
  );
}
