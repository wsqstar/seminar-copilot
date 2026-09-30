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
  source_type: 'transcript' | 'openalex' | 'crossref' | 'openalex_author'
  title: string
  url?: string | null
  authors: string[]
  year?: number | null
  snippet: string
  confidence: number
}

export interface SessionSnapshot {
  id: string
  project_id: string
  status: 'recording' | 'stopping' | 'stopped' | 'error'
  preset: SeminarPreset
  elapsed_seconds: number
  committed_until: number
  asr_state: string
  analyzer_state: string
  external_ai_enabled: boolean
  auto_questions_enabled: boolean
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
  overlap_after_seconds: number
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

export type ProjectStatus = 'recording' | 'stopped' | 'interrupted' | 'empty'

export interface ProjectSummary {
  id: string
  preset_id: string
  title: string
  speaker: string
  date: string
  status: ProjectStatus
  phase_count: number
  audio_seconds: number
  transcript_segments: number
  analysis_runs: number
  temporary_questions: number
  note_count: number
  updated_at: string
}

export interface ProjectPhase {
  session_id: string
  started_at: string
  status: ProjectStatus
  audio_seconds: number
  timeline_offset_seconds: number
  gap_after_seconds: number
  overlap_after_seconds: number
  transcript_segments: number
  analysis_runs: number
  temporary_questions: number
  has_audio: boolean
  audio_url?: string | null
}

export interface HistoricalTranscriptSegment {
  session_id: string
  start: number
  end: number
  text: string
}

export interface ProjectNote {
  id: string
  text: string
  created_at: string
  audio_second?: number | null
}

export interface ProjectDetail extends ProjectSummary {
  phases: ProjectPhase[]
  transcript: HistoricalTranscriptSegment[]
  questions: QuestionState[]
  notes: ProjectNote[]
}

export interface ParsedSeminar {
  title: string
  speaker: string
  speaker_affiliation: string
  date: string
  abstract: string
  topic_keywords: string[]
  parse_method: 'deepseek' | 'heuristic'
}

export interface RelevanceReport {
  score: number
  summary: string
  overlap_directions: string[]
  method: 'deepseek' | 'keyword'
}

export interface IntakeParseResponse {
  parsed: ParsedSeminar
  relevance: RelevanceReport
  research_sources: ResearchSource[]
  research_notes: string[]
  questions: QuestionDefinition[]
  question_method: 'deepseek' | 'none'
}

export interface IntakeConfirmPayload {
  parsed: ParsedSeminar
  relevance: RelevanceReport
  research_sources: ResearchSource[]
  research_notes: string[]
  raw_text: string
  glossary: string[]
  questions: QuestionDefinition[]
}

export interface AttachSeminarResponse {
  preset_id: string
  preset: SeminarPreset
  questions: QuestionDefinition[]
  question_method: 'deepseek' | 'none'
  matched_questions: number
}
