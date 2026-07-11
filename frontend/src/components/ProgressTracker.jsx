import { useEffect, useState } from 'react';
import { FiCheck, FiLoader, FiClock, FiAlertTriangle } from 'react-icons/fi';

const STEPS = [
  { key: 'extracting_job_info', label: 'Extracting job information' },
  { key: 'parsing_resume', label: 'Parsing your resume' },
  { key: 'searching_projects', label: 'Searching GitHub projects' },
  { key: 'building_profile', label: 'Building your profile' },
  { key: 'tailoring_resume', label: 'Tailoring your resume' },
  { key: 'uploading_results', label: 'Uploading results' },
];

export default function ProgressTracker({ jobId, includeGithub = true, onComplete }) {
  const [currentStep, setCurrentStep] = useState('pending');
  const [error, setError] = useState(null);

  // Without GitHub, the backend never emits the 'searching_projects' step.
  const steps = includeGithub ? STEPS : STEPS.filter((s) => s.key !== 'searching_projects');

  useEffect(() => {
    if (!jobId) return;

    const eventSource = new EventSource(`/api/jobs/${jobId}/progress`);
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setCurrentStep(data.step);
        if (data.error) setError(data.error);
        if (data.step === 'completed' || data.step === 'failed') {
          eventSource.close();
          if (data.step === 'completed') onComplete?.();
        }
      } catch (e) {
        console.error('SSE parse error:', e);
      }
    };
    eventSource.onerror = () => {
      // Auto-reconnect is built into EventSource
    };
    return () => eventSource.close();
  }, [jobId, onComplete]);

  const getStepIndex = () => steps.findIndex((s) => s.key === currentStep);
  const activeIdx = getStepIndex();

  return (
    <div style={{
      padding: 'var(--space-6)', background: 'var(--bg-secondary)',
      borderRadius: 'var(--radius-lg)', border: '1px solid var(--border-subtle)',
    }}>
      <h4 style={{ fontSize: 'var(--font-size-base)', marginBottom: 'var(--space-6)', color: 'var(--text-secondary)' }}>
        {currentStep === 'completed' ? '✅ Tailoring Complete!' :
         currentStep === 'failed' ? '❌ Tailoring Failed' :
         '⚡ Tailoring in progress...'}
      </h4>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        {steps.map((step, idx) => {
          let status = 'pending';
          if (currentStep === 'completed') status = 'done';
          else if (currentStep === 'failed' && idx <= activeIdx) status = idx === activeIdx ? 'failed' : 'done';
          else if (idx < activeIdx) status = 'done';
          else if (idx === activeIdx) status = 'active';

          return (
            <div key={step.key} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <div style={{
                width: 32, height: 32, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: status === 'done' ? 'var(--success-bg)' :
                            status === 'active' ? 'var(--accent-primary-glow)' :
                            status === 'failed' ? 'var(--error-bg)' : 'var(--bg-surface)',
                border: `2px solid ${status === 'done' ? 'var(--success)' :
                         status === 'active' ? 'var(--accent-primary)' :
                         status === 'failed' ? 'var(--error)' : 'var(--border-medium)'}`,
                transition: 'all var(--transition-base)',
              }}>
                {status === 'done' && <FiCheck size={16} color="var(--success)" />}
                {status === 'active' && <FiLoader size={16} color="var(--accent-primary)" style={{ animation: 'spin 1s linear infinite' }} />}
                {status === 'failed' && <FiAlertTriangle size={14} color="var(--error)" />}
                {status === 'pending' && <FiClock size={14} color="var(--text-muted)" />}
              </div>
              <span style={{
                fontSize: 'var(--font-size-sm)', fontWeight: status === 'active' ? 600 : 400,
                color: status === 'done' ? 'var(--success)' :
                       status === 'active' ? 'var(--text-primary)' :
                       status === 'failed' ? 'var(--error)' : 'var(--text-muted)',
              }}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      {error && (
        <p style={{ marginTop: 'var(--space-4)', fontSize: 'var(--font-size-sm)', color: 'var(--error)', background: 'var(--error-bg)', padding: 'var(--space-3)', borderRadius: 'var(--radius-sm)' }}>
          {error}
        </p>
      )}
    </div>
  );
}
