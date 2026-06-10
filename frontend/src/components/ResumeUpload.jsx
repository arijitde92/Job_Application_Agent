import { useState, useRef } from 'react';
import api from '../api/client';
import { FiUploadCloud, FiFile, FiTrash2, FiExternalLink } from 'react-icons/fi';

export default function ResumeUpload({ resumes, onRefresh }) {
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState('');
  const fileInputRef = useRef(null);

  const handleUpload = async (file) => {
    if (!file) return;
    if (!file.name.endsWith('.md')) {
      setError('Only Markdown (.md) files are accepted.');
      return;
    }
    setError('');
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      await api.post('/resumes/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      onRefresh();
    } catch (err) {
      setError(err.response?.data?.detail || 'Upload failed.');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.delete(`/resumes/${id}`);
      onRefresh();
    } catch (err) {
      setError(err.response?.data?.detail || 'Cannot delete resume.');
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragActive(false);
    const file = e.dataTransfer.files[0];
    handleUpload(file);
  };

  return (
    <div className="glass-card" style={{ padding: 'var(--space-6)', flex: 1, minWidth: 300 }}>
      <h3 style={{ fontSize: 'var(--font-size-lg)', marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        <FiUploadCloud size={20} color="var(--accent-primary)" /> Resume Upload
      </h3>

      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        style={{
          border: `2px dashed ${dragActive ? 'var(--accent-primary)' : 'var(--border-medium)'}`,
          borderRadius: 'var(--radius-md)', padding: 'var(--space-8)',
          textAlign: 'center', cursor: 'pointer',
          background: dragActive ? 'var(--accent-primary-glow)' : 'transparent',
          transition: 'all var(--transition-base)', marginBottom: 'var(--space-4)',
        }}
      >
        <FiUploadCloud size={32} color="var(--text-muted)" style={{ marginBottom: 'var(--space-2)' }} />
        <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)' }}>
          {uploading ? 'Uploading...' : 'Drag & drop or click to upload'}
        </p>
        <p style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)', marginTop: 'var(--space-1)' }}>
          Only .md files accepted
        </p>
        <input ref={fileInputRef} type="file" accept=".md" hidden
          onChange={(e) => handleUpload(e.target.files[0])} />
      </div>

      {/* Conversion links */}
      <div style={{ display: 'flex', gap: 'var(--space-4)', marginBottom: 'var(--space-4)', fontSize: 'var(--font-size-xs)' }}>
        <a href="https://pdf2md.morethan.io/" target="_blank" rel="noopener noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          PDF → MD <FiExternalLink size={10} />
        </a>
        <a href="https://word2md.com/" target="_blank" rel="noopener noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          Word → MD <FiExternalLink size={10} />
        </a>
      </div>

      {error && <p className="input-error" style={{ marginBottom: 'var(--space-3)' }}>{error}</p>}

      {/* Resume list */}
      {resumes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {resumes.map((r) => (
            <div key={r.id} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: 'var(--space-3) var(--space-4)', background: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-sm)', fontSize: 'var(--font-size-sm)',
            }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--text-secondary)' }}>
                <FiFile size={14} /> {r.original_filename}
              </span>
              <button className="btn btn-ghost btn-icon" onClick={() => handleDelete(r.id)} title="Delete">
                <FiTrash2 size={14} color="var(--error)" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
