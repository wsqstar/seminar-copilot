import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  BrainCircuit,
  Check,
  Circle,
  Clock3,
  Download,
  FileText,
  Loader2,
  Mic,
  Pause,
  Play,
  Radio,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { api } from './api'
import { listAudioInputs, startAudioCapture, type AudioCapture } from './audio'
import type {
  Health,
  QuestionState,
  QuestionStatus,
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

function QuestionCard({ question }: { question: QuestionState }) {
  const meta = statusMeta[question.status]
  const evidence = question.evidence.at(-1)
  return (
    <article className={`question-card ${meta.className}`}>
      <div className="question-heading">
        <span className="status-dot" aria-hidden="true" />
        <h3>{question.question}</h3>
        <span className="status-label">{meta.label}</span>
      </div>
      {question.answer && <p className="answer-text">{question.answer}</p>}
      {evidence && (
        <blockquote>
          <time>{formatTime(evidence.start)}</time>
          <span>{evidence.quote}</span>
        </blockquote>
      )}
      {question.missing.length > 0 && question.status !== 'unanswered' && (
        <div className="missing-row">
          <span>仍缺</span>
          <p>{question.missing.join('；')}</p>
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
  onStart,
  busy,
}: {
  health: Health | null
  presets: SeminarPreset[]
  onStart: (presetId: string, externalAi: boolean, deviceId: string, demo: boolean) => Promise<void>
  busy: boolean
}) {
  const [selected, setSelected] = useState('')
  const [externalAi, setExternalAi] = useState(false)
  const [consent, setConsent] = useState(false)
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [deviceId, setDeviceId] = useState('')
  const [deviceError, setDeviceError] = useState('')

  useEffect(() => {
    if (!selected && presets[0]) setSelected(presets[0].id)
  }, [presets, selected])

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

function LiveWorkbench({
  snapshot,
  onStop,
  onAnalyze,
  onExport,
  busy,
}: {
  snapshot: SessionSnapshot
  onStop: () => Promise<void>
  onAnalyze: () => Promise<void>
  onExport: () => Promise<void>
  busy: boolean
}) {
  const transcriptEnd = useRef<HTMLDivElement>(null)
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
          {snapshot.external_ai_enabled && !isStopped && (
            <button className="icon-text-button" onClick={onAnalyze} disabled={busy} title="立即分析最近 120 秒">
              <BrainCircuit size={17} />立即判断
            </button>
          )}
          {!isStopped ? (
            <button className="stop-button" onClick={onStop} disabled={busy}>
              {busy ? <Loader2 className="spin" size={17} /> : <Pause size={17} />}结束录音
            </button>
          ) : (
            <button className="primary-button export-button" onClick={onExport} disabled={busy}>
              <Download size={17} />导出到 Obsidian
            </button>
          )}
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

      <section className="workbench-grid">
        <div className="transcript-pane">
          <div className="pane-heading">
            <div><span className={isStopped ? 'record-dot stopped' : 'record-dot'} /><h2>现场转录</h2></div>
            <span>{snapshot.transcript.length} 个稳定片段</span>
          </div>
          <div className="transcript-scroll" aria-live="polite">
            {snapshot.transcript.length === 0 && (
              <div className="empty-state"><Mic size={28} /><p>等待第一段稳定语音</p><span>通常需要 10–15 秒。</span></div>
            )}
            {snapshot.transcript.map((segment) => (
              <div className="transcript-segment" key={segment.id}>
                <time>{formatTime(segment.start)}</time>
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
            {snapshot.questions.map((question) => <QuestionCard key={question.id} question={question} />)}
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

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [presets, setPresets] = useState<SeminarPreset[]>([])
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null)
  const [capture, setCapture] = useState<AudioCapture | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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

  if (!snapshot) {
    return <><SetupPanel health={health} presets={presets} onStart={start} busy={busy} />{error && <div className="global-error"><AlertCircle size={17} />{error}</div>}</>
  }

  return <><LiveWorkbench snapshot={snapshot} onStop={stop} onAnalyze={analyze} onExport={exportNotes} busy={busy} />{error && <div className="global-error"><AlertCircle size={17} />{error}</div>}</>
}
