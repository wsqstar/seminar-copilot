export type QuestionStatus = 'unanswered' | 'mention' | 'partial' | 'answered'

export interface QuestionDefinition {
  id: string
  question: string
  why_it_matters: string
  keywords: string[]
  expected_slots: string[]
}

export interface SeminarPreset {
  id: string
  title: string
  speaker: string
  date: string
  source_path?: string | null
  glossary: string[]
  questions: QuestionDefinition[]
}

export interface TranscriptSegment {
  id: string
  start: number
  end: number
  text: string
  final: boolean
}

export interface Evidence {
  segment_id: string
  start: number
  end: number
  quote: string
  relation: 'keyword_match' | 'direct_answer' | 'background' | 'contradiction'
  confidence: number
  source_session?: string | null
}

export interface QuestionState {
  id: string
  question: string
  why_it_matters: string
  expected_slots: string[]
  keywords?: string[]
  status: QuestionStatus
  answer: string
  missing: string[]
  confidence: number
  evidence: Evidence[]
  question_en?: string
  temporary?: boolean
  created_at_audio_second?: number | null
  research_status?: 'not_requested' | 'pending' | 'complete' | 'limited' | 'error'
  research_summary?: string
  research_sources?: ResearchSource[]
}

export interface ResearchSource {
  source_type: 'transcript' | 'openalex' | 'crossref'
  title: string
  url?: string | null
  authors: string[]
  year?: number | null
  snippet: string
  confidence: number
}

export interface SessionSnapshot {
  id: string
  status: 'recording' | 'stopping' | 'stopped' | 'error'
  preset: SeminarPreset
  elapsed_seconds: number
  committed_until: number
  asr_state: string
  analyzer_state: string
  external_ai_enabled: boolean
  recovered_sessions?: RecoveredSessionState[]
  timeline_offset_seconds?: number
  transcript: TranscriptSegment[]
  provisional_text: string
  questions: QuestionState[]
  followups: string[]
  last_error: string
  export_path?: string | null
}

export interface RecoveredSessionState {
  session_id: string
  started_at: string
  audio_seconds: number
  timeline_offset_seconds: number
  gap_after_seconds: number
  transcript: TranscriptSegment[]
  ai_analysis_runs: number
}

export interface Health {
  ok: boolean
  whisper_model: string
  whisper_state: 'cold' | 'loading' | 'ready' | 'error'
  whisper_error: string
  demo_enabled: boolean
  preset_count: number
}
