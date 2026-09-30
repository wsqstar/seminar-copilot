import type { AttachSeminarResponse, Health, IntakeConfirmPayload, IntakeParseResponse, ProjectDetail, ProjectNote, ProjectSummary, SeminarPreset, SessionSnapshot } from './types'

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
  intakeParse: (text: string) =>
    request<IntakeParseResponse>('/api/intake/parse', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  intakeConfirm: (payload: IntakeConfirmPayload) =>
    request<{ preset_id: string }>('/api/intake/confirm', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  projects: () => request<ProjectSummary[]>('/api/projects'),
  project: (projectId: string) => request<ProjectDetail>(`/api/projects/${projectId}`),
  start: (presetId: string, externalAiEnabled: boolean, autoQuestionsEnabled = false) =>
    request<SessionSnapshot>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({
        preset_id: presetId,
        external_ai_enabled: externalAiEnabled,
        auto_questions_enabled: autoQuestionsEnabled,
        recording_permission_confirmed: true,
      }),
    }),
  stop: (sessionId: string) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}/stop`, { method: 'POST', body: '{}' }),
  analyze: (sessionId: string) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}/analyze`, { method: 'POST', body: '{}' }),
  addTemporaryQuestion: (sessionId: string, draft: string, searchExternal: boolean) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}/temporary-questions`, {
      method: 'POST',
      body: JSON.stringify({ draft, search_external: searchExternal }),
    }),
  activeSession: (projectId: string) =>
    request<SessionSnapshot>(`/api/projects/${projectId}/active-session`),
  continueProject: (projectId: string, externalAiEnabled: boolean, autoQuestionsEnabled = false) =>
    request<SessionSnapshot>(`/api/projects/${projectId}/continue`, {
      method: 'POST',
      body: JSON.stringify({
        external_ai_enabled: externalAiEnabled,
        auto_questions_enabled: autoQuestionsEnabled,
        recording_permission_confirmed: true,
      }),
    }),
  attachProject: (projectId: string, text: string) =>
    request<AttachSeminarResponse>(`/api/projects/${projectId}/attach`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  addProjectNote: (projectId: string, text: string, audioSecond?: number | null) =>
    request<ProjectNote>(`/api/projects/${projectId}/notes`, {
      method: 'POST',
      body: JSON.stringify({ text, audio_second: audioSecond ?? null }),
    }),
  demo: (sessionId: string) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}/demo`, { method: 'POST', body: '{}' }),
  export: (sessionId: string) =>
    request<{ path: string }>(`/api/sessions/${sessionId}/export`, { method: 'POST', body: '{}' }),
}
