import { supabase } from './lib/supabase'

const API_BASE = import.meta.env.VITE_API_URL || '';

async function authHeaders() {
  if (!supabase) return { 'Content-Type': 'application/json' }
  const { data: { session } } = await supabase.auth.getSession()
  const headers = { 'Content-Type': 'application/json' }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }
  return headers
}

export async function apiCall(endpoint, body, options = {}) {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();

  if (data.task_id && data.poll_url) {
    return pollTask(data.poll_url, options);
  }
  return data;
}

async function pollTask(pollUrl, { intervalMs = 2000, maxWaitMs = 240000, onProgress } = {}) {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, intervalMs));
    const headers = await authHeaders();
    const res = await fetch(`${API_BASE}${pollUrl}`, { method: 'GET', headers });
    if (!res.ok) throw new Error(`Poll failed: HTTP ${res.status}`);
    const task = await res.json();
    if (onProgress) onProgress(task.status);
    if (task.status === 'done') return task.result;
    if (task.status === 'error') throw new Error(task.error || 'Task failed');
  }
  throw new Error('Task timed out — please try again');
}

export async function apiGet(endpoint) {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'GET',
    headers,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function apiPut(endpoint, body) {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function apiPatch(endpoint, body) {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function apiUpload(endpoint, file) {
  const { data: { session } } = await supabase.auth.getSession()
  const headers = {}
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers,
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function apiDelete(endpoint) {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'DELETE',
    headers,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function apiGetBlob(endpoint) {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'GET',
    headers,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.blob();
}

export async function healthCheck() {
  const res = await fetch(`${API_BASE}/api/health`);
  return res.json();
}
