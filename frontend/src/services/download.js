import api from './api';

/**
 * Extract the filename from a Content-Disposition header.
 *
 * Prefers the RFC 5987 `filename*=UTF-8''...` form (non-ASCII names) and
 * falls back to the plain `filename="..."` form.
 */
export function filenameFromDisposition(header, fallback = 'download') {
  if (!header) return fallback;
  const extended = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      /* malformed encoding — fall through to the plain filename */
    }
  }
  const plain = /filename\s*=\s*"([^"]*)"|filename\s*=\s*([^;]+)/i.exec(header);
  const name = plain && (plain[1] ?? plain[2])?.trim();
  return name || fallback;
}

/** Save a Blob to the user's device under `filename`. */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser a moment to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Download an authenticated API resource and save it, taking the content
 * type and filename from the server's response headers.
 */
export async function downloadFromApi(path, fallbackName = 'download') {
  const res = await api.get(path, { responseType: 'blob' });
  const type = res.headers['content-type'] || 'application/octet-stream';
  const blob = res.data instanceof Blob ? res.data : new Blob([res.data], { type });
  saveBlob(blob, filenameFromDisposition(res.headers['content-disposition'], fallbackName));
}

/** True when a job's tailored resume can be shown/downloaded as a PDF. */
export function jobHasPdf(job) {
  return Boolean(job?.tailored_resume_latex_gcs_path || job?.tailored_resume_pdf_gcs_path);
}
