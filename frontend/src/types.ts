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
}

export interface QuestionState {
  id: string
  question: string
  why_it_matters: string
  expected_slots: string[]
  status: QuestionStatus
  answer: string
  missing: string[]
  confidence: number
  evidence: Evidence[]
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
  transcript: TranscriptSegment[]
  provisional_text: string
  questions: QuestionState[]
  followups: string[]
  last_error: string
  export_path?: string | null
}

export interface Health {
  ok: boolean
  whisper_model: string
  whisper_state: 'cold' | 'loading' | 'ready' | 'error'
  whisper_error: string
  demo_enabled: boolean
  preset_count: number
}
