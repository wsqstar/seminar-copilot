/**
 * Seminar Copilot 的 DSH Web 客户端插件（外部动态插件，纯 JS 无构建）。
 *
 * 在右侧栏「开始」引导页「新建终端」下方注册「会议录音」入口；
 * 点击后打开嵌入 Seminar Copilot 前端（Vite dev server，127.0.0.1:5173）的
 * 侧栏 tab。打开时先用 no-cors 探测服务（绕开 8765 仅允许 5173 源的 CORS
 * 限制）；未运行时自动请求插件宿主侧的 127.0.0.1:8766 控制端点拉起
 * scripts/dev.sh，并轮询直至就绪；控制端点不可用时回退为手动命令提示。
 * keepMounted 保证录音中的 iframe 在切换 tab / 会话时不被卸载。
 */

var PACKAGE_NAME = 'seminar-copilot-dsh-plugin'
var LOCALE_NS = 'seminarCopilot'
var FRONTEND_URL = 'http://127.0.0.1:5173/'
var HEALTH_URL = 'http://127.0.0.1:8765/api/health'
var CONTROL_URL = 'http://127.0.0.1:8766'
var START_COMMAND = 'cd ~/Coding/github/wsqstar/seminar-copilot && ./scripts/dev.sh'
var POLL_INTERVAL_MS = 2000
var POLL_MAX_ATTEMPTS = 60

var CSS = [
  '[data-seminar-copilot] { display: flex; flex-direction: column; width: 100%; height: 100%; min-height: 0; }',
  '[data-seminar-copilot] iframe { flex: 1; width: 100%; border: 0; display: block; background: #fff; }',
  '[data-seminar-copilot-status] { margin: auto; max-width: 420px; padding: 32px 24px; text-align: center; line-height: 1.7; }',
  '[data-seminar-copilot-status] h3 { margin: 0 0 8px; font-size: 15px; }',
  '[data-seminar-copilot-status] p { margin: 0 0 12px; font-size: 13px; opacity: 0.72; }',
  '[data-seminar-copilot-status] code { display: block; margin: 0 0 16px; padding: 10px 12px; font-size: 12px; text-align: left; word-break: break-all; border-radius: 8px; background: rgba(127, 127, 127, 0.14); }',
  '[data-seminar-copilot-status] .sc-actions { display: flex; gap: 8px; justify-content: center; margin-bottom: 12px; }',
  '[data-seminar-copilot-status] .sc-spinner { display: inline-block; width: 14px; height: 14px; margin-right: 8px; vertical-align: -2px; border: 2px solid rgba(127, 127, 127, 0.35); border-top-color: currentColor; border-radius: 50%; animation: sc-spin 0.9s linear infinite; }',
  '@keyframes sc-spin { to { transform: rotate(360deg); } }',
].join('\n')

window.__ModuleLoader__.load({
  id: PACKAGE_NAME,
  factory: function (require) {
    var React = require('react')
    var primitives = require('@deepseek-ai/dsh-client-ui-primitives')
    var GuideIcon = primitives.IconMicrophoneOutlineArtwork || primitives.PluginArtworkDefault
    var Button = primitives.Button

    var style = document.createElement('style')
    style.dataset.plugin = PACKAGE_NAME
    style.textContent = CSS
    document.head.append(style)

    function probe(url) {
      // no-cors：只关心服务是否可达；服务在线时得到不透明响应也算成功。
      return fetch(url, { mode: 'no-cors', cache: 'no-store' }).then(function () { return true }, function () { return false })
    }

    function appReady() {
      return Promise.all([probe(HEALTH_URL), probe(FRONTEND_URL)]).then(function (r) {
        return r[0] && r[1]
      })
    }

    function SeminarBody(props) {
      var t = props.t
      var statusState = React.useState('checking')
      var status = statusState[0]
      var setStatus = statusState[1]
      var retryState = React.useState(0)
      var retry = retryState[0]
      var setRetry = retryState[1]
      var copiedState = React.useState(false)
      var copied = copiedState[0]
      var setCopied = copiedState[1]

      React.useEffect(function () {
        var alive = true
        var pollTimer = null
        setStatus('checking')

        function poll(attempt) {
          if (!alive) return
          appReady().then(function (ready) {
            if (!alive) return
            if (ready) {
              setStatus('online')
              return
            }
            if (attempt >= POLL_MAX_ATTEMPTS) {
              setStatus('offline')
              return
            }
            pollTimer = setTimeout(function () { poll(attempt + 1) }, POLL_INTERVAL_MS)
          })
        }

        appReady().then(function (ready) {
          if (!alive) return
          if (ready) {
            setStatus('online')
            return
          }
          // 服务未运行：请求插件宿主侧的控制端点自动拉起 dev.sh。
          fetch(CONTROL_URL + '/start', { method: 'POST' }).then(function () {
            if (!alive) return
            setStatus('starting')
            pollTimer = setTimeout(function () { poll(1) }, POLL_INTERVAL_MS)
          }, function () {
            // 控制端点不可用（宿主侧未加载等）：回退为手动命令提示。
            if (alive) setStatus('offline')
          })
        })

        return function () {
          alive = false
          if (pollTimer) clearTimeout(pollTimer)
        }
      }, [retry])

      React.useEffect(function () {
        if (!copied) return undefined
        var timer = setTimeout(function () { setCopied(false) }, 2000)
        return function () { clearTimeout(timer) }
      }, [copied])

      if (status === 'online') {
        return React.createElement('div', { 'data-seminar-copilot': '' },
          React.createElement('iframe', { src: FRONTEND_URL, allow: 'microphone', title: t('tabTitle') }))
      }

      if (status === 'starting' || status === 'checking') {
        return React.createElement('div', { 'data-seminar-copilot': '' },
          React.createElement('div', { 'data-seminar-copilot-status': '' },
            React.createElement('h3', null,
              React.createElement('span', { className: 'sc-spinner' }),
              status === 'starting' ? t('startingTitle') : t('checking')),
            status === 'starting' ? React.createElement('p', null, t('startingBody')) : null))
      }

      var copyCommand = function () {
        var done = function () { setCopied(true) }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(START_COMMAND).then(done, done)
        } else {
          done()
        }
      }
      var retryNow = function () { setRetry(retry + 1) }
      return React.createElement('div', { 'data-seminar-copilot': '' },
        React.createElement('div', { 'data-seminar-copilot-status': '' },
          React.createElement('h3', null, t('offlineTitle')),
          React.createElement('p', null, t('offlineBody')),
          React.createElement('code', null, START_COMMAND),
          React.createElement('div', { className: 'sc-actions' },
            React.createElement(Button, { variant: 'ghost', onClick: copyCommand }, copied ? t('copied') : t('copy')),
            React.createElement(Button, { variant: 'ghost', onClick: retryNow }, t('retry'))),
          React.createElement('p', null, t('hintMic'))))
    }

    function SeminarTitle(props) {
      return React.createElement('span', null, props.t('tabTitle'))
    }

    return {
      inject: ['slots', 'locale', 'sidebarRightTabs'],
      apply: function (ctx) {
        ctx.effect(function () {
          return ctx.locale.register(LOCALE_NS, {
            zh: {
              tabTitle: '会议录音',
              guideTitle: '会议录音',
              guideDesc: '本地研讨会录音与实时转写（Seminar Copilot）',
              checking: '正在检测 Seminar Copilot 服务…',
              startingTitle: '正在启动 Seminar Copilot…',
              startingBody: '首次启动需要加载 Whisper 模型，一般一分钟内就绪，请稍候。',
              offlineTitle: 'Seminar Copilot 未能自动启动',
              offlineBody: '可在 /tmp/seminar-copilot-dev.log 查看启动日志，或在终端手动运行：',
              copy: '复制启动命令',
              copied: '已复制',
              retry: '重新检测',
              hintMic: '首次录音时浏览器会请求麦克风权限。',
            },
            en: {
              tabTitle: 'Meeting Recorder',
              guideTitle: 'Meeting Recorder',
              guideDesc: 'Local seminar recording with live transcription (Seminar Copilot)',
              checking: 'Checking the Seminar Copilot service…',
              startingTitle: 'Starting Seminar Copilot…',
              startingBody: 'The first launch loads the Whisper model and is usually ready within a minute.',
              offlineTitle: 'Seminar Copilot could not auto-start',
              offlineBody: 'Check /tmp/seminar-copilot-dev.log, or start it manually in a terminal:',
              copy: 'Copy start command',
              copied: 'Copied',
              retry: 'Check again',
              hintMic: 'The browser will ask for microphone access on first recording.',
            },
          })
        }, 'seminar-copilot.locale')

        var t = ctx.locale.bind(LOCALE_NS)

        ctx.effect(function () {
          return ctx.sidebarRightTabs.register({
            id: PACKAGE_NAME,
            kind: 'seminar',
            keepMounted: true,
            title: function () { return t('tabTitle') },
            guide: [{
              id: 'record',
              order: 30,
              title: function () { return t('guideTitle') },
              description: function () { return t('guideDesc') },
              icon: GuideIcon,
            }],
          })
        }, 'seminar-copilot.tabs')

        ctx.slots.inject('sidebar.right.pane.tab', function () {
          return ctx.slots.register({ name: 'sidebar.right.pane.tab', key: PACKAGE_NAME, locale: LOCALE_NS }, SeminarBody)
        })

        ctx.slots.inject('sidebar.right.pane.tab.title', function () {
          return ctx.slots.register({ name: 'sidebar.right.pane.tab.title', key: PACKAGE_NAME, locale: LOCALE_NS }, SeminarTitle)
        })
      },
    }
  },
})
