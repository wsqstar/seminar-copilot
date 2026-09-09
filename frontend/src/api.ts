import type { Health, SeminarPreset, SessionSnapshot } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'content-type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(payload.detail || response.statusText)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/api/health'),
  presets: () => request<SeminarPreset[]>('/api/presets'),
  start: (presetId: string, externalAiEnabled: boolean) =>
    request<SessionSnapshot>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({
        preset_id: presetId,
        external_ai_enabled: externalAiEnabled,
        recording_permission_confirmed: true,
      }),
    }),
  stop: (sessionId: string) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}/stop`, { method: 'POST', body: '{}' }),
  analyze: (sessionId: string) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}/analyze`, { method: 'POST', body: '{}' }),
  demo: (sessionId: string) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}/demo`, { method: 'POST', body: '{}' }),
  export: (sessionId: string) =>
    request<{ path: string }>(`/api/sessions/${sessionId}/export`, { method: 'POST', body: '{}' }),
}
