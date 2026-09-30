import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Archive,
  BrainCircuit,
  Check,
  ChevronLeft,
  Circle,
  Clock3,
  Download,
  FileText,
  Headphones,
  History,
  Loader2,
  MessageSquarePlus,
  Mic,
  NotebookPen,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Search,
  X,
} from 'lucide-react'
import { api } from './api'
import { listAudioInputs, startAudioCapture, type AudioCapture } from './audio'
import type {
  Health,
  IntakeParseResponse,
  ParsedSeminar,
  ProjectDetail,
  ProjectSummary,
  QuestionDefinition,
  QuestionState,
  QuestionStatus,
  RelevanceReport,
  ResearchSource,
  SeminarPreset,
  SessionSnapshot,
} from './types'

const statusMeta: Record<QuestionStatus, { label: string; className: string }> = {
  unanswered: { label: '未回答', className: 'status-unanswered' },
  mention: { label: '提及', className: 'status-mention' },
  partial: { label: '部分回答', className: 'status-partial' },
  answered: { label: '已回答', className: 'status-answered' },
}

function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`
}

function QuestionCard({ question, currentSessionId }: { question: QuestionState; currentSessionId: string }) {
  const meta = statusMeta[question.status]
  const evidence = question.evidence.at(-1)
  const transcriptSources = (question.research_sources ?? []).filter((source) => source.source_type === 'transcript')
  const academicSources = (question.research_sources ?? []).filter((source) => source.source_type !== 'transcript')
  return (
    <article className={`question-card ${meta.className}`}>
      <div className="question-heading">
        <span className="status-dot" aria-hidden="true" />
        <h3>{question.question}</h3>
        <span className="status-label">{meta.label}</span>
      </div>
      {question.temporary && <span className="temporary-badge">现场临时问题</span>}
      {question.question_en && <p className="question-en">{question.question_en}</p>}
      {question.answer && <p className="answer-text">{question.answer}</p>}
      {evidence && (
        <blockquote>
          <time>{formatTime(evidence.start)}{evidence.source_session && evidence.source_session !== currentSessionId && <small>合并</small>}</time>
          <span>{evidence.quote}</span>
        </blockquote>
      )}
      {question.missing.length > 0 && question.status !== 'unanswered' && (
        <div className="missing-row">
          <span>仍缺</span>
          <p>{question.missing.join('；')}</p>
        </div>
      )}
      {question.temporary && question.research_status && (
        <div className={`research-box research-${question.research_status}`}>
          <div><Search size={13} /><strong>{question.research_status === 'pending' ? '正在检索' : '问题依据'}</strong></div>
          <p>{question.research_summary}</p>
          {transcriptSources.length > 0 && <small className="source-group-label">讲座证据</small>}
          {transcriptSources.slice(0, 2).map((source, index) => (
            <span key={`transcript-${index}`}>{source.title} · {source.snippet}</span>
          ))}
          {academicSources.length > 0 && <small className="source-group-label">外部文献候选</small>}
          {academicSources.slice(0, 3).map((source, index) => (
            source.url
              ? <a href={source.url} target="_blank" rel="noreferrer" key={`${source.source_type}-${index}`}>{source.title}{source.year ? ` · ${source.year}` : ''}</a>
              : <span key={`${source.source_type}-${index}`}>{source.title}</span>
          ))}
        </div>
      )}
      <div className="question-footer">
        <span>{question.why_it_matters}</span>
        {question.confidence > 0 && <span>置信度 {Math.round(question.confidence * 100)}%</span>}
      </div>
    </article>
  )
}

function SetupPanel({
  health,
  presets,
  preferredPresetId,
  onStart,
  onHistory,
  onIntake,
  busy,
}: {
  health: Health | null
  presets: SeminarPreset[]
  preferredPresetId?: string
  onStart: (presetId: string, externalAi: boolean, deviceId: string, demo: boolean) => Promise<void>
  onHistory: () => Promise<void>
  onIntake: () => void
  busy: boolean
}) {
  const [selected, setSelected] = useState('')
  const [externalAi, setExternalAi] = useState(false)
  const [consent, setConsent] = useState(false)
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [deviceId, setDeviceId] = useState('')
  const [deviceError, setDeviceError] = useState('')

  useEffect(() => {
    if (preferredPresetId && presets.some((item) => item.id === preferredPresetId)) {
      setSelected(preferredPresetId)
      return
    }
    if (!selected && presets[0]) setSelected(presets[0].id)
  }, [presets, selected, preferredPresetId])

  const inspectMicrophones = async () => {
    setDeviceError('')
    try {
      const permission = await navigator.mediaDevices.getUserMedia({ audio: true })
      permission.getTracks().forEach((track) => track.stop())
      const inputs = await listAudioInputs()
      setDevices(inputs)
      if (!deviceId && inputs[0]) setDeviceId(inputs[0].deviceId)
    } catch (error) {
      setDeviceError(error instanceof Error ? error.message : '无法读取麦克风')
    }
  }

  const preset = presets.find((item) => item.id === selected)

  return (
    <main className="setup-shell">
      <header className="app-header setup-header">
        <div className="brand-mark"><Radio size={20} /></div>
        <div>
          <p className="eyebrow">SEMINAR COPILOT</p>
          <h1>讲座问题追踪器</h1>
        </div>
        <div className="health-state">
          <span className={health?.whisper_state === 'ready' ? 'health-ok' : 'health-wait'} />
          {!health?.ok
            ? '正在连接后端'
            : health.whisper_state === 'ready'
              ? '本地转录已就绪'
              : health.whisper_state === 'error'
                ? 'Whisper 预热失败'
              : 'Whisper 预热中'}
        </div>
        <div className="header-actions">
          <button className="icon-text-button" onClick={onIntake} type="button">
            <Plus size={16} />新建 Seminar
          </button>
          <button className="icon-text-button header-history-button" onClick={onHistory} type="button">
            <Archive size={16} />历史项目
          </button>
        </div>
      </header>

      <section className="setup-grid">
        <div className="setup-form">
          <div className="section-title">
            <span>01</span>
            <div><h2>选择讲座</h2><p>问题、术语和证据槽位来自会前画像。</p></div>
          </div>
          <label className="field-label" htmlFor="preset">讲座预设</label>
          <select id="preset" value={selected} onChange={(event) => setSelected(event.target.value)}>
            {presets.map((item) => <option value={item.id} key={item.id}>{item.speaker} · {item.title}</option>)}
          </select>
          {preset && (
            <div className="preset-summary">
              <strong>{preset.speaker}</strong>
              <span>{preset.date}</span>
              <p>{preset.questions.length} 个待追踪问题 · {preset.glossary.length} 个术语提示</p>
            </div>
          )}

          <div className="section-title compact-title">
            <span>02</span>
            <div><h2>音频输入</h2><p>音频与 Whisper 转录只保存在本机。</p></div>
          </div>
          <div className="input-row">
            <select aria-label="麦克风" value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
              <option value="">系统默认麦克风</option>
              {devices.map((device, index) => (
                <option key={device.deviceId} value={device.deviceId}>
                  {device.label || `音频输入 ${index + 1}`}
                </option>
              ))}
            </select>
            <button className="icon-button" onClick={inspectMicrophones} title="检测麦克风" type="button">
              <RefreshCw size={18} />
            </button>
          </div>
          {deviceError && <p className="inline-error"><AlertCircle size={15} />{deviceError}</p>}

          <label className="toggle-row">
            <input type="checkbox" checked={externalAi} onChange={(event) => setExternalAi(event.target.checked)} />
            <span className="toggle-control" aria-hidden="true" />
            <span><strong>DeepSeek 深层判断</strong><small>每分钟发送最近 120 秒稳定转录和问题状态。</small></span>
          </label>

          <label className="consent-row">
            <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
            <span><ShieldCheck size={17} />我已确认现场允许录音，并知悉开启 DeepSeek 后稳定转录会发送到模型服务。</span>
          </label>

          <div className="setup-actions">
            <button
              className="primary-button"
              disabled={!selected || !consent || busy || health?.whisper_state !== 'ready'}
              onClick={() => onStart(selected, externalAi, deviceId, false)}
            >
              {busy ? <Loader2 className="spin" size={18} /> : <Mic size={18} />}
              开始录音
            </button>
            {health?.demo_enabled && (
              <button className="secondary-button" disabled={!selected || busy} onClick={() => onStart(selected, false, '', true)}>
                <Play size={17} />载入演示
              </button>
            )}
          </div>
        </div>

        <aside className="question-preview">
          <div className="preview-heading">
            <span>监听清单</span>
            <strong>{preset?.questions.length ?? 0}</strong>
          </div>
          {preset?.questions.map((question, index) => (
            <div className="preview-question" key={question.id}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <p>{question.question}</p>
            </div>
          ))}
        </aside>
      </section>
    </main>
  )
}

type HistoryTab = 'audio' | 'transcript' | 'questions' | 'notes'

const projectStatusLabel = {
  recording: '录音中',
  stopped: '已完成',
  interrupted: '可恢复',
  empty: '空项目',
} as const

function HistoryPanel({
  projects,
  detail,
  busy,
  onBack,
  onSelect,
  onContinue,
  onRejoin,
  onAddNote,
}: {
  projects: ProjectSummary[]
  detail: ProjectDetail | null
  busy: boolean
  onBack: () => void
  onSelect: (projectId: string) => Promise<void>
  onContinue: (projectId: string, externalAi: boolean, deviceId: string) => Promise<void>
  onRejoin: (projectId: string, deviceId: string) => Promise<void>
  onAddNote: (projectId: string, text: string, audioSecond?: number | null) => Promise<void>
}) {
  const isLive = detail?.status === 'recording'
  const [tab, setTab] = useState<HistoryTab>('audio')
  const [continueOpen, setContinueOpen] = useState(false)
  const [externalAi, setExternalAi] = useState(true)
  const [consent, setConsent] = useState(false)
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [deviceId, setDeviceId] = useState('')
  const [deviceError, setDeviceError] = useState('')
  const [noteText, setNoteText] = useState('')
  const [noteBusy, setNoteBusy] = useState(false)

  useEffect(() => {
    setTab('audio')
    setContinueOpen(false)
    setConsent(false)
    setNoteText('')
  }, [detail?.id])

  const inspectMicrophones = async () => {
    setDeviceError('')
    try {
      const permission = await navigator.mediaDevices.getUserMedia({ audio: true })
      permission.getTracks().forEach((track) => track.stop())
      const inputs = await listAudioInputs()
      setDevices(inputs)
      const builtIn = inputs.find((device) => /MacBook Pro.*麦克风|MacBook Pro.*Microphone/i.test(device.label))
      setDeviceId((builtIn ?? inputs[0])?.deviceId ?? '')
    } catch (error) {
      setDeviceError(error instanceof Error ? error.message : '无法读取麦克风')
    }
  }

  const saveNote = async () => {
    if (!detail || !noteText.trim()) return
    setNoteBusy(true)
    try {
      await onAddNote(detail.id, noteText)
      setNoteText('')
    } finally {
      setNoteBusy(false)
    }
  }

  return (
    <main className="history-shell">
      <header className="history-header">
        <button className="icon-button compact" onClick={onBack} title="返回新录音"><ChevronLeft size={18} /></button>
        <div className="brand-mark"><Archive size={18} /></div>
        <div><p className="eyebrow">SEMINAR ARCHIVE</p><h1>历史项目</h1></div>
        <button className="primary-button history-new-button" onClick={onBack}><Plus size={16} />新录音</button>
      </header>

      <section className="history-layout">
        <aside className="project-list-pane">
          <div className="history-pane-title"><span>完整讲座</span><strong>{projects.length}</strong></div>
          <div className="project-list">
            {projects.map((project) => (
              <button
                className={`project-list-item ${detail?.id === project.id ? 'selected' : ''}`}
                key={project.id}
                onClick={() => onSelect(project.id)}
              >
                <div><strong>{project.speaker}</strong><span className={`project-status ${project.status}`}>{projectStatusLabel[project.status]}</span></div>
                <p>{project.title}</p>
                <small>{project.date} · {project.phase_count} 个阶段 · {formatTime(project.audio_seconds)}</small>
              </button>
            ))}
            {projects.length === 0 && <div className="history-empty"><Archive size={28} /><p>还没有可用的录音项目</p></div>}
          </div>
        </aside>

        <section className="project-detail-pane">
          {!detail ? (
            <div className="history-empty detail-empty"><Headphones size={34} /><p>{busy ? '正在读取项目…' : '选择一个历史项目'}</p></div>
          ) : (
            <>
              <header className="project-detail-header">
                <div>
                  <span>{detail.date} · {detail.speaker}</span>
                  <h2>{detail.title}</h2>
                  <p>{detail.phase_count} 个录音阶段 · {formatTime(detail.audio_seconds)} 音频 · {detail.transcript_segments} 段稳定转录</p>
                </div>
                <button
                  className="primary-button"
                  onClick={() => setContinueOpen((value) => !value)}
                  disabled={busy}
                ><Mic size={16} />{isLive ? '回到录音' : '继续录音'}</button>
              </header>

              {continueOpen && (
                <section className="continue-panel">
                  <div className="continue-fields">
                    <div>
                      <label className="field-label" htmlFor="continue-microphone">麦克风</label>
                      <div className="input-row">
                        <select id="continue-microphone" value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
                          <option value="">先检测并选择麦克风</option>
                          {devices.map((device, index) => <option value={device.deviceId} key={device.deviceId}>{device.label || `音频输入 ${index + 1}`}</option>)}
                        </select>
                        <button className="icon-button" onClick={inspectMicrophones} title="检测麦克风"><RefreshCw size={17} /></button>
                      </div>
                    </div>
                    {!isLive && (
                      <label className="toggle-row compact-toggle">
                        <input type="checkbox" checked={externalAi} onChange={(event) => setExternalAi(event.target.checked)} />
                        <span className="toggle-control" aria-hidden="true" />
                        <span><strong>DeepSeek 判断</strong><small>继续使用历史问题状态。</small></span>
                      </label>
                    )}
                  </div>
                  {deviceError && <p className="inline-error"><AlertCircle size={15} />{deviceError}</p>}
                  <label className="consent-row compact-consent">
                    <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                    <span><ShieldCheck size={16} />我已确认本次{isLive ? '回到录音' : '继续录音'}获得许可，并核对了麦克风。</span>
                  </label>
                  <div className="continue-actions">
                    <button className="secondary-button" onClick={() => setContinueOpen(false)}>取消</button>
                    <button className="primary-button" disabled={!consent || !deviceId || busy} onClick={() => (isLive ? onRejoin(detail.id, deviceId) : onContinue(detail.id, externalAi, deviceId))}>
                      {busy ? <Loader2 className="spin" size={16} /> : <Mic size={16} />}{isLive ? '回到录音界面' : '开始新阶段'}
                    </button>
                  </div>
                </section>
              )}

              <nav className="history-tabs" aria-label="项目内容">
                <button className={tab === 'audio' ? 'active' : ''} onClick={() => setTab('audio')}><Headphones size={15} />录音 <span>{detail.phase_count}</span></button>
                <button className={tab === 'transcript' ? 'active' : ''} onClick={() => setTab('transcript')}><FileText size={15} />转录 <span>{detail.transcript_segments}</span></button>
                <button className={tab === 'questions' ? 'active' : ''} onClick={() => setTab('questions')}><BrainCircuit size={15} />问题 <span>{detail.questions.length}</span></button>
                <button className={tab === 'notes' ? 'active' : ''} onClick={() => setTab('notes')}><NotebookPen size={15} />笔记 <span>{detail.note_count}</span></button>
              </nav>

              <div className="project-detail-content">
                {tab === 'audio' && detail.phases.map((phase, index) => (
                  <article className="phase-row" key={phase.session_id}>
                    <div className="phase-index">{String(index + 1).padStart(2, '0')}</div>
                    <div className="phase-main">
                      <div><strong>录音阶段 {index + 1}</strong><span className={`project-status ${phase.status}`}>{projectStatusLabel[phase.status]}</span></div>
                      <p>{new Date(phase.started_at).toLocaleString('zh-CN')} · {formatTime(phase.audio_seconds)} · {phase.transcript_segments} 段转录 · {phase.analysis_runs} 次 AI 判断</p>
                      {phase.audio_url && <audio controls preload="none" src={phase.audio_url} />}
                      {phase.gap_after_seconds > 0 && <small>到下一阶段间隔 {formatTime(phase.gap_after_seconds)}</small>}
                      {phase.overlap_after_seconds > 0 && <small className="phase-overlap">与下一阶段重叠 {formatTime(phase.overlap_after_seconds)}，转录按实际开始时间对齐</small>}
                    </div>
                  </article>
                ))}

                {tab === 'transcript' && (
                  <div className="history-transcript">
                    {detail.transcript.map((segment) => (
                      <div className="transcript-segment" key={`${segment.session_id}-${segment.start}`}>
                        <time>{formatTime(segment.start)}</time><p>{segment.text}</p>
                      </div>
                    ))}
                    {detail.transcript.length === 0 && <div className="history-empty"><FileText size={28} /><p>没有稳定转录</p></div>}
                  </div>
                )}

                {tab === 'questions' && (
                  <div className="history-questions">
                    {detail.questions.map((question) => <QuestionCard key={question.id} question={question} currentSessionId={detail.phases.at(-1)?.session_id ?? ''} />)}
                  </div>
                )}

                {tab === 'notes' && (
                  <div className="notes-workspace">
                    <div className="note-composer-inline">
                      <textarea value={noteText} onChange={(event) => setNoteText(event.target.value)} rows={3} placeholder="记录需要核验的数字、方法疑问或会后行动…" />
                      <button className="primary-button" disabled={!noteText.trim() || noteBusy} onClick={saveNote}>{noteBusy ? <Loader2 className="spin" size={16} /> : <NotebookPen size={16} />}保存笔记</button>
                    </div>
                    <div className="note-list">
                      {[...detail.notes].reverse().map((note) => (
                        <article key={note.id}><time>{new Date(note.created_at).toLocaleString('zh-CN')}{note.audio_second != null ? ` · ${formatTime(note.audio_second)}` : ''}</time><p>{note.text}</p></article>
                      ))}
                      {detail.notes.length === 0 && <div className="history-empty"><NotebookPen size={28} /><p>还没有人工笔记</p></div>}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </section>
    </main>
  )
}

function LiveWorkbench({
  snapshot,
  onStop,
  onAnalyze,
  onExport,
  onHistory,
  onAddNote,
  busy,
}: {
  snapshot: SessionSnapshot
  onStop: () => Promise<void>
  onAnalyze: () => Promise<void>
  onExport: () => Promise<void>
  onHistory: () => Promise<void>
  onAddNote: (text: string, audioSecond: number) => Promise<void>
  busy: boolean
}) {
  const transcriptEnd = useRef<HTMLDivElement>(null)
  const [composerOpen, setComposerOpen] = useState(false)
  const [questionDraft, setQuestionDraft] = useState('')
  const [searchExternal, setSearchExternal] = useState(true)
  const [questionBusy, setQuestionBusy] = useState(false)
  const [composerError, setComposerError] = useState('')
  const [noteComposerOpen, setNoteComposerOpen] = useState(false)
  const [noteText, setNoteText] = useState('')
  const [noteBusy, setNoteBusy] = useState(false)
  const [noteSaved, setNoteSaved] = useState('')
  const counts = useMemo(() => {
    return snapshot.questions.reduce<Record<QuestionStatus, number>>(
      (result, question) => ({ ...result, [question.status]: result[question.status] + 1 }),
      { unanswered: 0, mention: 0, partial: 0, answered: 0 },
    )
  }, [snapshot.questions])

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [snapshot.transcript.length, snapshot.provisional_text])

  const isStopped = snapshot.status === 'stopped'
  const recovered = snapshot.recovered_sessions ?? []
  const timelineOffset = snapshot.timeline_offset_seconds ?? 0
  const recoveredSegments = recovered.reduce((total, item) => total + item.transcript.length, 0)
  const recoveredAudio = recovered.reduce((total, item) => total + item.audio_seconds, 0)
  const recoveryGap = recovered.reduce((total, item) => total + item.gap_after_seconds, 0)

  const submitTemporaryQuestion = async () => {
    setQuestionBusy(true)
    setComposerError('')
    try {
      await api.addTemporaryQuestion(snapshot.id, questionDraft, searchExternal)
      setQuestionDraft('')
      setComposerOpen(false)
    } catch (reason) {
      setComposerError(reason instanceof Error ? reason.message : '临时问题保存失败')
    } finally {
      setQuestionBusy(false)
    }
  }

  const submitNote = async () => {
    if (!noteText.trim()) return
    setNoteBusy(true)
    setComposerError('')
    try {
      const audioSecond = timelineOffset + snapshot.elapsed_seconds
      await onAddNote(noteText.trim(), audioSecond)
      setNoteText('')
      setNoteComposerOpen(false)
      setNoteSaved(`笔记已保存到项目时间 ${formatTime(audioSecond)}`)
    } catch (reason) {
      setComposerError(reason instanceof Error ? reason.message : '笔记保存失败')
    } finally {
      setNoteBusy(false)
    }
  }

  return (
    <main className="workbench-shell">
      <header className="live-header">
        <div className="brand-line">
          <div className="brand-mark"><Radio size={19} /></div>
          <div><p className="eyebrow">SEMINAR COPILOT</p><strong>{snapshot.preset.speaker}</strong></div>
        </div>
        <div className="lecture-title">
          <span>{snapshot.preset.date}</span>
          <h1>{snapshot.preset.title}</h1>
        </div>
        <div className="live-actions">
          {!isStopped && (
            <button className="icon-text-button" onClick={() => setComposerOpen(true)} title="添加现场临时问题">
              <MessageSquarePlus size={17} />临时问题
            </button>
          )}
          {!isStopped && (
            <button className="icon-text-button" onClick={() => setNoteComposerOpen(true)} title="记录人工笔记">
              <NotebookPen size={17} />笔记
            </button>
          )}
          {snapshot.external_ai_enabled && !isStopped && (
            <button className="icon-text-button" onClick={onAnalyze} disabled={busy} title="立即分析最近 120 秒">
              <BrainCircuit size={17} />立即判断
            </button>
          )}
          {!isStopped ? (
            <button className="stop-button" onClick={onStop} disabled={busy}>
              {busy ? <Loader2 className="spin" size={17} /> : <Pause size={17} />}结束录音
            </button>
          ) : (<>
            <button className="icon-text-button" onClick={onHistory} disabled={busy}>
              <Archive size={17} />历史项目
            </button>
            <button className="primary-button export-button" onClick={onExport} disabled={busy}>
              <Download size={17} />导出到 Obsidian
            </button>
          </>)}
        </div>
      </header>

      <section className="status-strip">
        <div><Clock3 size={16} /><span>录音</span><strong>{formatTime(snapshot.elapsed_seconds)}</strong></div>
        <div><FileText size={16} /><span>稳定至</span><strong>{formatTime(snapshot.committed_until)}</strong></div>
        <div><Mic size={16} /><span>Whisper</span><strong>{snapshot.asr_state}</strong></div>
        <div><Sparkles size={16} /><span>分析</span><strong>{snapshot.analyzer_state}</strong></div>
        <div className="coverage-meter">
          <span>覆盖</span>
          <strong>{counts.answered}/{snapshot.questions.length}</strong>
          <div><i style={{ width: `${(counts.answered / snapshot.questions.length) * 100}%` }} /></div>
        </div>
      </section>

      {snapshot.last_error && <div className="error-banner"><AlertCircle size={17} />{snapshot.last_error}</div>}
      {snapshot.export_path && <div className="success-banner"><Check size={17} />已导出：{snapshot.export_path}</div>}
      {noteSaved && <div className="success-banner"><Check size={17} />{noteSaved}</div>}
      {recovered.length > 0 && (
        <div className="recovery-banner">
          <History size={17} />
          <strong>已合并 {recovered.length} 个重启前阶段</strong>
          <span>恢复 {formatTime(recoveredAudio)} 录音、{recoveredSegments} 段字幕、{recovered.reduce((total, item) => total + item.ai_analysis_runs, 0)} 次 AI 判断</span>
          {recoveryGap > 0 && <em>已标记约 {formatTime(recoveryGap)} 的录音缺口</em>}
        </div>
      )}

      {composerOpen && (
        <div className="composer-backdrop" role="presentation">
          <section className="question-composer" role="dialog" aria-modal="true" aria-labelledby="composer-title">
            <div className="composer-heading">
              <div><MessageSquarePlus size={18} /><h2 id="composer-title">现场临时问题</h2></div>
              <button className="icon-button compact" onClick={() => setComposerOpen(false)} title="关闭"><X size={17} /></button>
            </div>
            <p>写下粗略想法，或留空让系统根据尚未回答的关键缺口形成问题。</p>
            <textarea
              value={questionDraft}
              onChange={(event) => setQuestionDraft(event.target.value)}
              placeholder="例如：兄弟姐妹固定效应仍无法排除哪些个体层面的选择？"
              rows={4}
              autoFocus
            />
            <label className="composer-search-toggle">
              <input type="checkbox" checked={searchExternal} onChange={(event) => setSearchExternal(event.target.checked)} />
              <span><strong>检索学术依据</strong><small>先查完整讲座字幕，再查 OpenAlex 与 Crossref；不抓 Google Scholar。</small></span>
            </label>
            {composerError && <p className="inline-error"><AlertCircle size={15} />{composerError}</p>}
            <div className="composer-actions">
              <button className="secondary-button" onClick={() => setComposerOpen(false)}>取消</button>
              <button className="primary-button" onClick={submitTemporaryQuestion} disabled={questionBusy}>
                {questionBusy ? <Loader2 className="spin" size={17} /> : <Search size={17} />}
                保存并检索
              </button>
            </div>
          </section>
        </div>
      )}

      {noteComposerOpen && (
        <div className="composer-backdrop" role="presentation">
          <section className="question-composer" role="dialog" aria-modal="true" aria-labelledby="note-composer-title">
            <div className="composer-heading">
              <div><NotebookPen size={18} /><h2 id="note-composer-title">现场笔记</h2></div>
              <button className="icon-button compact" onClick={() => setNoteComposerOpen(false)} title="关闭"><X size={17} /></button>
            </div>
            <p>笔记会绑定当前项目时间 {formatTime(timelineOffset + snapshot.elapsed_seconds)}，并与录音、转录和问题一起保留。</p>
            <textarea
              value={noteText}
              onChange={(event) => setNoteText(event.target.value)}
              placeholder="记录需要核验的数字、方法疑问或会后行动…"
              rows={4}
              autoFocus
            />
            {composerError && <p className="inline-error"><AlertCircle size={15} />{composerError}</p>}
            <div className="composer-actions">
              <button className="secondary-button" onClick={() => setNoteComposerOpen(false)}>取消</button>
              <button className="primary-button" onClick={submitNote} disabled={!noteText.trim() || noteBusy}>
                {noteBusy ? <Loader2 className="spin" size={17} /> : <NotebookPen size={17} />}保存笔记
              </button>
            </div>
          </section>
        </div>
      )}

      <section className="workbench-grid">
        <div className="transcript-pane">
          <div className="pane-heading">
            <div><span className={isStopped ? 'record-dot stopped' : 'record-dot'} /><h2>现场转录</h2></div>
            <span>{snapshot.transcript.length + recoveredSegments} 个稳定片段</span>
          </div>
          <div className="transcript-scroll" aria-live="polite">
            {snapshot.transcript.length === 0 && recovered.length === 0 && (
              <div className="empty-state"><Mic size={28} /><p>等待第一段稳定语音</p><span>通常需要 10–15 秒。</span></div>
            )}
            {recovered.map((phase, index) => (
              <div className="recovered-phase" key={phase.session_id}>
                <div className="timeline-divider"><span>重启前阶段 {index + 1}</span><small>{phase.ai_analysis_runs} 次 AI 判断</small></div>
                {phase.transcript.map((segment) => (
                  <div className="transcript-segment recovered" key={`${phase.session_id}-${segment.id}`}>
                    <time>{formatTime(phase.timeline_offset_seconds + segment.start)}</time>
                    <p>{segment.text}</p>
                  </div>
                ))}
                {phase.gap_after_seconds > 0 && (
                  <div className="timeline-gap">录音重启缺口约 {formatTime(phase.gap_after_seconds)}</div>
                )}
                {phase.overlap_after_seconds > 0 && (
                  <div className="timeline-overlap">与下一录音阶段重叠约 {formatTime(phase.overlap_after_seconds)}，均保留供核验</div>
                )}
              </div>
            ))}
            {recovered.length > 0 && <div className="timeline-divider current"><span>当前录音阶段</span></div>}
            {snapshot.transcript.map((segment) => (
              <div className="transcript-segment" key={segment.id}>
                <time>{formatTime(timelineOffset + segment.start)}</time>
                <p>{segment.text}</p>
              </div>
            ))}
            {snapshot.provisional_text && (
              <div className="transcript-segment provisional">
                <time>识别中</time><p>{snapshot.provisional_text}</p>
              </div>
            )}
            <div ref={transcriptEnd} />
          </div>
        </div>

        <div className="questions-pane">
          <div className="pane-heading question-pane-heading">
            <div><Circle size={14} /><h2>问题覆盖</h2></div>
            <div className="status-legend">
              <span className="legend-answer">{counts.answered} 已回答</span>
              <span className="legend-partial">{counts.partial} 部分</span>
              <span>{counts.unanswered + counts.mention} 待确认</span>
            </div>
          </div>
          <div className="question-scroll">
            {snapshot.questions.map((question) => <QuestionCard key={question.id} question={question} currentSessionId={snapshot.id} />)}
          </div>
        </div>
      </section>

      <section className="followup-bar">
        <div><BrainCircuit size={18} /><span>追问候选</span></div>
        <p>{snapshot.followups[0] || 'DeepSeek 完成问题覆盖判断后，会在这里给出最值得现场追问的一句话。'}</p>
      </section>
    </main>
  )
}

type IntakeStage = 'paste' | 'review'

const relevanceLabel = (score: number) => {
  if (score <= 0) return '未评估'
  if (score <= 2) return '弱相关'
  if (score <= 3) return '中等相关'
  return '高度相关'
}

function IntakePanel({
  onBack,
  onSaved,
}: {
  onBack: () => void
  onSaved: (presetId: string) => Promise<void>
}) {
  const [stage, setStage] = useState<IntakeStage>('paste')
  const [rawText, setRawText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [parsed, setParsed] = useState<ParsedSeminar | null>(null)
  const [relevance, setRelevance] = useState<RelevanceReport | null>(null)
  const [sources, setSources] = useState<ResearchSource[]>([])
  const [researchNotes, setResearchNotes] = useState<string[]>([])
  const [questions, setQuestions] = useState<QuestionDefinition[]>([])
  const [questionMethod, setQuestionMethod] = useState<'deepseek' | 'none'>('none')

  const parse = async () => {
    if (rawText.trim().length < 20) {
      setError('请粘贴完整的 Seminar 通告（至少 20 个字符）')
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.intakeParse(rawText)
      setParsed(result.parsed)
      setRelevance(result.relevance)
      setSources(result.research_sources)
      setResearchNotes(result.research_notes)
      setQuestions(result.questions)
      setQuestionMethod(result.question_method)
      setStage('review')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '解析失败')
    } finally {
      setBusy(false)
    }
  }

  const updateQuestion = (index: number, patch: Partial<QuestionDefinition>) => {
    setQuestions((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  const addQuestion = () => {
    setQuestions((rows) => [...rows, { id: `q${rows.length + 1}-manual`, question: '', why_it_matters: '', keywords: [], expected_slots: [] }])
  }

  const confirm = async () => {
    if (!parsed || !relevance) return
    const valid = questions.filter((question) => question.question.trim())
    if (valid.length === 0) {
      setError('至少保留一个备讲问题')
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.intakeConfirm({
        parsed,
        relevance,
        research_sources: sources,
        research_notes: researchNotes,
        raw_text: rawText,
        glossary: parsed.topic_keywords,
        questions: valid.map((question, index) => ({ ...question, id: `q${index + 1}` })),
      })
      await onSaved(result.preset_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="setup-shell intake-shell">
      <header className="app-header setup-header">
        <button className="icon-button compact" onClick={onBack} title="返回设置"><ChevronLeft size={18} /></button>
        <div>
          <p className="eyebrow">SEMINAR INTAKE</p>
          <h1>新建 Seminar</h1>
        </div>
      </header>

      {stage === 'paste' && (
        <section className="intake-paste">
          <div className="section-title">
            <span>01</span>
            <div><h2>粘贴通告</h2><p>粘贴 Seminar 邮件或网页通告全文，系统会解析演讲者、检查与你的研究的相关性、检索文献并生成备讲问题。</p></div>
          </div>
          <textarea
            value={rawText}
            onChange={(event) => setRawText(event.target.value)}
            rows={12}
            placeholder="粘贴包含题目、演讲者、时间、摘要的完整通告…"
            autoFocus
          />
          {error && <p className="inline-error"><AlertCircle size={15} />{error}</p>}
          <div className="setup-actions">
            <button className="primary-button" onClick={parse} disabled={busy || rawText.trim().length < 20}>
              {busy ? <Loader2 className="spin" size={18} /> : <Sparkles size={18} />}
              解析并检索
            </button>
          </div>
        </section>
      )}

      {stage === 'review' && parsed && relevance && (
        <section className="intake-review">
          <div className="section-title">
            <span>02</span>
            <div><h2>审阅并录入</h2><p>核对解析结果，编辑备讲问题后保存；保存后即可在设置页选择该讲座开始录音。</p></div>
          </div>

          <div className="intake-fields">
            <div>
              <label className="field-label" htmlFor="intake-title">题目</label>
              <input id="intake-title" value={parsed.title} onChange={(event) => setParsed({ ...parsed, title: event.target.value })} />
            </div>
            <div className="intake-field-row">
              <div>
                <label className="field-label" htmlFor="intake-speaker">演讲者</label>
                <input id="intake-speaker" value={parsed.speaker} onChange={(event) => setParsed({ ...parsed, speaker: event.target.value })} />
              </div>
              <div>
                <label className="field-label" htmlFor="intake-date">日期</label>
                <input id="intake-date" value={parsed.date} onChange={(event) => setParsed({ ...parsed, date: event.target.value })} placeholder="YYYY-MM-DD" />
              </div>
            </div>
            {parsed.speaker_affiliation && (
              <p className="intake-affiliation">{parsed.speaker_affiliation}</p>
            )}
            {parsed.abstract && <p className="intake-abstract">{parsed.abstract}</p>}
          </div>

          <div className={`intake-relevance score-${Math.min(5, Math.max(1, relevance.score))}`}>
            <div><Sparkles size={15} /><strong>与我研究的相关性：{relevanceLabel(relevance.score)}</strong></div>
            <p>{relevance.summary || '尚未填写研究方向画像（backend/config/research_profile.md）。'}</p>
            {relevance.overlap_directions.length > 0 && (
              <small>重叠方向：{relevance.overlap_directions.join('；')}</small>
            )}
          </div>

          {sources.length > 0 && (
            <div className="intake-sources">
              <div><Search size={15} /><strong>相关文献（OpenAlex / Crossref）</strong></div>
              {sources.map((source, index) => (
                source.url
                  ? <a href={source.url} target="_blank" rel="noreferrer" key={`${source.source_type}-${index}`}>{source.title}{source.year ? ` · ${source.year}` : ''}</a>
                  : <span key={`${source.source_type}-${index}`}>{source.title}{source.year ? ` · ${source.year}` : ''}</span>
              ))}
            </div>
          )}
          {researchNotes.length > 0 && <p className="intake-notes">{researchNotes.join('；')}</p>}

          <div className="intake-questions">
            <div className="intake-questions-heading">
              <h3>备讲问题（{questions.length}）</h3>
              <button className="secondary-button" onClick={addQuestion} type="button"><Plus size={15} />新增问题</button>
            </div>
            {questionMethod === 'none' && questions.length === 0 && (
              <p className="intake-notes">未配置 DeepSeek（dsh），无法自动生成问题；请手动新增备讲问题。</p>
            )}
            {questions.map((question, index) => (
              <div className="intake-question-card" key={question.id}>
                <div className="intake-question-tools">
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <button className="icon-button compact" title="删除问题" onClick={() => setQuestions((rows) => rows.filter((_, i) => i !== index))}>
                    <X size={15} />
                  </button>
                </div>
                <textarea
                  value={question.question}
                  onChange={(event) => updateQuestion(index, { question: event.target.value })}
                  rows={2}
                  placeholder="用中文写一个具体到可以当场提出的问题…"
                />
                <input
                  value={question.why_it_matters}
                  onChange={(event) => updateQuestion(index, { why_it_matters: event.target.value })}
                  placeholder="为什么值得问（可选）"
                />
                <input
                  value={question.keywords.join(', ')}
                  onChange={(event) => updateQuestion(index, { keywords: event.target.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean) })}
                  placeholder="关键词（逗号分隔，用于现场匹配）"
                />
              </div>
            ))}
          </div>

          {error && <p className="inline-error"><AlertCircle size={15} />{error}</p>}
          <div className="setup-actions">
            <button className="secondary-button" onClick={() => setStage('paste')} disabled={busy}>返回修改</button>
            <button className="primary-button" onClick={confirm} disabled={busy}>
              {busy ? <Loader2 className="spin" size={18} /> : <Check size={18} />}
              保存并录入
            </button>
          </div>
        </section>
      )}
    </main>
  )
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [presets, setPresets] = useState<SeminarPreset[]>([])
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null)
  const [capture, setCapture] = useState<AudioCapture | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [screen, setScreen] = useState<'setup' | 'history' | 'intake'>('setup')
  const [preferredPresetId, setPreferredPresetId] = useState<string | undefined>(undefined)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [projectDetail, setProjectDetail] = useState<ProjectDetail | null>(null)

  useEffect(() => {
    const refreshHealth = () => api.health()
      .then(setHealth)
      .catch((reason) => setError(reason instanceof Error ? reason.message : '无法连接本地后端'))
    refreshHealth()
    api.presets()
      .then(setPresets)
      .catch((reason) => setError(reason instanceof Error ? reason.message : '无法载入讲座预设'))
    const timer = window.setInterval(refreshHealth, 2_000)
    return () => window.clearInterval(timer)
  }, [])

  const start = async (presetId: string, externalAi: boolean, deviceId: string, demo: boolean) => {
    setBusy(true)
    setError('')
    let created: SessionSnapshot | null = null
    try {
      created = await api.start(presetId, externalAi)
      setScreen('setup')
      setSnapshot(created)
      if (demo) {
        setSnapshot(await api.demo(created.id))
      } else {
        const audio = await startAudioCapture(created.id, deviceId, setSnapshot, setError)
        setCapture(audio)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '启动失败')
      if (created) await api.stop(created.id).catch(() => undefined)
      if (!demo) setSnapshot(null)
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    if (!snapshot) return
    setBusy(true)
    setError('')
    try {
      await capture?.stop()
      setCapture(null)
      setSnapshot(await api.stop(snapshot.id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '停止失败')
    } finally {
      setBusy(false)
    }
  }

  const analyze = async () => {
    if (!snapshot) return
    setBusy(true)
    setError('')
    try {
      setSnapshot(await api.analyze(snapshot.id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '分析失败')
    } finally {
      setBusy(false)
    }
  }

  const exportNotes = async () => {
    if (!snapshot) return
    setBusy(true)
    setError('')
    try {
      const result = await api.export(snapshot.id)
      setSnapshot({ ...snapshot, export_path: result.path })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '导出失败')
    } finally {
      setBusy(false)
    }
  }

  const selectProject = async (projectId: string) => {
    setBusy(true)
    setError('')
    try {
      setProjectDetail(await api.project(projectId))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '历史项目读取失败')
    } finally {
      setBusy(false)
    }
  }

  const openHistory = async () => {
    if (snapshot && snapshot.status !== 'stopped') {
      setError('录音期间不能切换到历史项目；请先结束录音。')
      return
    }
    setBusy(true)
    setError('')
    try {
      const rows = await api.projects()
      setProjects(rows)
      setScreen('history')
      setSnapshot(null)
      if (rows.length > 0) setProjectDetail(await api.project(rows[0].id))
      else setProjectDetail(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '历史项目读取失败')
    } finally {
      setBusy(false)
    }
  }

  const continueProject = async (projectId: string, externalAi: boolean, deviceId: string) => {
    setBusy(true)
    setError('')
    let created: SessionSnapshot | null = null
    try {
      created = await api.continueProject(projectId, externalAi)
      setSnapshot(created)
      setScreen('setup')
      const audio = await startAudioCapture(created.id, deviceId, setSnapshot, setError)
      setCapture(audio)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '继续录音失败')
      if (created) await api.stop(created.id).catch(() => undefined)
      setSnapshot(null)
      setScreen('history')
    } finally {
      setBusy(false)
    }
  }

  const rejoinProject = async (projectId: string, deviceId: string) => {
    setBusy(true)
    setError('')
    try {
      const active = await api.activeSession(projectId)
      setSnapshot(active)
      setScreen('setup')
      const audio = await startAudioCapture(active.id, deviceId, setSnapshot, setError)
      setCapture(audio)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '回到录音失败')
      setSnapshot(null)
      const [rows, refreshed] = await Promise.all([api.projects(), api.project(projectId)])
      setProjects(rows)
      setProjectDetail(refreshed)
    } finally {
      setBusy(false)
    }
  }

  const addProjectNote = async (projectId: string, text: string, audioSecond?: number | null) => {
    await api.addProjectNote(projectId, text, audioSecond)
    if (screen === 'history' && projectDetail?.id === projectId) {
      const [rows, refreshed] = await Promise.all([api.projects(), api.project(projectId)])
      setProjects(rows)
      setProjectDetail(refreshed)
    }
  }

  const addLiveNote = async (text: string, audioSecond: number) => {
    if (!snapshot) throw new Error('当前没有录音项目')
    await addProjectNote(snapshot.project_id, text, audioSecond)
  }

  if (!snapshot && screen === 'history') {
    return <><HistoryPanel
      projects={projects}
      detail={projectDetail}
      busy={busy}
      onBack={() => { setScreen('setup'); setProjectDetail(null) }}
      onSelect={selectProject}
      onContinue={continueProject}
      onRejoin={rejoinProject}
      onAddNote={addProjectNote}
    />{error && <div className="global-error"><AlertCircle size={17} />{error}</div>}</>
  }

  if (!snapshot && screen === 'intake') {
    return <><IntakePanel
      onBack={() => setScreen('setup')}
      onSaved={async (presetId) => {
        setPresets(await api.presets())
        setPreferredPresetId(presetId)
        setScreen('setup')
      }}
    />{error && <div className="global-error"><AlertCircle size={17} />{error}</div>}</>
  }

  if (!snapshot) {
    return <><SetupPanel health={health} presets={presets} preferredPresetId={preferredPresetId} onStart={start} onHistory={openHistory} onIntake={() => { setError(''); setScreen('intake') }} busy={busy} />{error && <div className="global-error"><AlertCircle size={17} />{error}</div>}</>
  }

  return <><LiveWorkbench snapshot={snapshot} onStop={stop} onAnalyze={analyze} onExport={exportNotes} onHistory={openHistory} onAddNote={addLiveNote} busy={busy} />{error && <div className="global-error"><AlertCircle size={17} />{error}</div>}</>
}
