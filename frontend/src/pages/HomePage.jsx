import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import api from '../services/api';
import Navbar from '../components/Navbar';
import ResumeUpload from '../components/ResumeUpload';
import GithubProfileAdd from '../components/GithubProfileAdd';
import TailoredResumeList from '../components/TailoredResumeList';
import TailorResumeForm from '../components/TailorResumeForm';
import ProgressTracker from '../components/ProgressTracker';
import ResumePreviewModal from '../components/ResumePreviewModal';
import ResumeReadyModal from '../components/ResumeReadyModal';
import { FiActivity, FiSettings, FiSend } from 'react-icons/fi';

export default function HomePage() {
  const { user } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [resumes, setResumes] = useState([]);
  const [githubProfiles, setGithubProfiles] = useState([]);
  const [activeJobId, setActiveJobId] = useState(null);
  const [activeJobHasGithub, setActiveJobHasGithub] = useState(true);
  const [completedJob, setCompletedJob] = useState(null);
  const [previewJobId, setPreviewJobId] = useState(null);
  const [previewResumeId, setPreviewResumeId] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [j, r, g] = await Promise.all([
        api.get('/jobs'), api.get('/resumes'), api.get('/github'),
      ]);
      setJobs(j.data); setResumes(r.data); setGithubProfiles(g.data);
    } catch (err) { console.error('Fetch error:', err); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleJobCreated = (job, hasGithub = true) => {
    setActiveJobId(job.id);
    setActiveJobHasGithub(hasGithub);
    setJobs((prev) => [job, ...prev]);
  };

  const handleRetry = (job) => {
    setActiveJobId(job.id);
    setActiveJobHasGithub(job.github_profile_id != null);
    fetchData();
  };

  const handleComplete = useCallback(async () => {
    try {
      const res = await api.get(`/jobs/${activeJobId}`);
      setCompletedJob(res.data);
      const jobsRes = await api.get('/jobs');
      setJobs(jobsRes.data);
    } catch { setCompletedJob({ id: activeJobId }); }
    setActiveJobId(null);
  }, [activeJobId]);

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)' }}>
      <Navbar />
      <main className="container" style={{ padding: 'var(--space-8) var(--space-6)', maxWidth: 1000 }}>
        <div style={{ marginBottom: 'var(--space-10)' }}>
          <h1 style={{ fontSize: 'var(--font-size-3xl)', marginBottom: 'var(--space-2)' }}>
            Welcome back, <span style={{ color: 'var(--accent-primary)' }}>{user?.first_name}</span>!
          </h1>
          <p style={{ color: 'var(--text-secondary)' }}>Manage your resumes and tailor them for your next opportunity.</p>
        </div>

        <section style={{ marginBottom: 'var(--space-10)' }}>
          <h2 style={{ fontSize: 'var(--font-size-xl)', marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <FiActivity size={20} color="var(--accent-secondary)" /> Previously Tailored Resumes
          </h2>
          <TailoredResumeList jobs={jobs} onPreviewResume={(id) => setPreviewJobId(id)} onRefresh={fetchData} onRetry={handleRetry} />
        </section>

        <section style={{ marginBottom: 'var(--space-10)' }}>
          <h2 style={{ fontSize: 'var(--font-size-xl)', marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <FiSettings size={20} color="var(--accent-secondary)" /> Manage Resources
          </h2>
          <div style={{ display: 'flex', gap: 'var(--space-6)', flexWrap: 'wrap' }}>
            <ResumeUpload resumes={resumes} onRefresh={fetchData} />
            <GithubProfileAdd profiles={githubProfiles} onRefresh={fetchData} />
          </div>
        </section>

        <section style={{ marginBottom: 'var(--space-10)' }}>
          <h2 style={{ fontSize: 'var(--font-size-xl)', marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <FiSend size={20} color="var(--accent-secondary)" /> Tailor Resume
          </h2>
          <div className="glass-card" style={{ padding: 'var(--space-6)' }}>
            <TailorResumeForm githubProfiles={githubProfiles} resumes={resumes}
              onJobCreated={handleJobCreated} onPreviewUploadedResume={(id) => setPreviewResumeId(id)} />
          </div>
        </section>

        {activeJobId && (
          <section style={{ marginBottom: 'var(--space-10)' }}>
            <ProgressTracker jobId={activeJobId} includeGithub={activeJobHasGithub} onComplete={handleComplete} />
          </section>
        )}
      </main>

      {previewJobId && <ResumePreviewModal jobId={previewJobId} onClose={() => setPreviewJobId(null)} />}
      {previewResumeId && <ResumePreviewModal resumeId={previewResumeId} onClose={() => setPreviewResumeId(null)} />}
      {completedJob && <ResumeReadyModal job={completedJob}
        onClose={() => { setCompletedJob(null); fetchData(); }}
        onPreviewResume={(id) => { setCompletedJob(null); setPreviewJobId(id); }} />}
    </div>
  );
}
