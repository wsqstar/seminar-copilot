/**
 * Seminar Copilot 的 DSH Web 客户端插件（外部动态插件，纯 JS 无构建）。
 *
 * 在右侧栏「开始」引导页「新建终端」下方注册「会议录音」入口；
 * 点击后打开嵌入 Seminar Copilot 前端（Vite dev server，127.0.0.1:5173）的
 * 侧栏 tab。打开时先用 no-cors 探测服务（绕开 8765 仅允许 5173 源的 CORS
 * 限制）；未运行时自动请求插件宿主侧的 127.0.0.1:8766 控制端点拉起
 * scripts/dev.sh，并轮询直至就绪；控制端点不可用时回退为手动命令提示。
 * keepMounted 保证录音中的 iframe 在切换 tab / 会话时不被卸载。
 * 在线视图右上角提供「识别设置」面板：切换本地 Whisper / 阿里云百炼
 * 后端、填写 DashScope API Key、选择模型与语言、设置仓库路径；配置经
 * 控制端点保存到 ~/.dsh/seminar-copilot/config.json，由宿主在拉起 dev.sh
 * 时以进程级环境变量注入，不注册全局环境变量。
 */

var PACKAGE_NAME = "seminar-copilot-dsh-plugin"
var LOCALE_NS = "seminarCopilot"
var FRONTEND_URL = "http://127.0.0.1:5173/"
var HEALTH_URL = "http://127.0.0.1:8765/api/health"
var CONTROL_URL = "http://127.0.0.1:8766"
var START_COMMAND = "cd ~/Coding/github/wsqstar/seminar-copilot && ./scripts/dev.sh"
var POLL_INTERVAL_MS = 2000
var POLL_MAX_ATTEMPTS = 60

var CSS = [
  "[data-seminar-copilot] { position: relative; display: flex; flex-direction: column; width: 100%; height: 100%; min-height: 0; }",
  "[data-seminar-copilot] iframe { flex: 1; width: 100%; border: 0; display: block; background: #fff; }",
  "[data-seminar-copilot] .sc-gear { position: absolute; top: 6px; right: 8px; z-index: 5; padding: 3px 10px; font-size: 12px; line-height: 1.5; border: 1px solid rgba(127,127,127,0.45); border-radius: 999px; background: Canvas; color: CanvasText; cursor: pointer; opacity: 0.72; }",
  "[data-seminar-copilot] .sc-gear:hover { opacity: 1; }",
  "[data-seminar-copilot] .sc-settings { position: absolute; top: 38px; right: 8px; z-index: 6; width: 332px; max-width: calc(100% - 16px); padding: 14px 16px 16px; border: 1px solid rgba(127,127,127,0.45); border-radius: 12px; background: Canvas; color: CanvasText; box-shadow: 0 8px 28px rgba(0,0,0,0.28); font-size: 13px; line-height: 1.6; }",
  "[data-seminar-copilot] .sc-settings h4 { margin: 0 0 6px; font-size: 13px; }",
  "[data-seminar-copilot] .sc-settings label { display: block; margin: 10px 0 3px; font-size: 12px; opacity: 0.72; }",
  "[data-seminar-copilot] .sc-settings select, [data-seminar-copilot] .sc-settings input { width: 100%; box-sizing: border-box; padding: 6px 8px; font-size: 13px; border: 1px solid rgba(127,127,127,0.5); border-radius: 8px; background: Canvas; color: CanvasText; }",
  "[data-seminar-copilot] .sc-settings .sc-privacy { margin: 10px 0 0; padding: 8px 10px; font-size: 12px; border-radius: 8px; background: rgba(230,160,40,0.16); }",
  "[data-seminar-copilot] .sc-settings .sc-warn { margin: 10px 0 0; padding: 8px 10px; font-size: 12px; border-radius: 8px; background: rgba(220,80,80,0.14); }",
  "[data-seminar-copilot] .sc-settings .sc-settings-actions { display: flex; gap: 8px; margin-top: 14px; }",
  "[data-seminar-copilot] .sc-settings .sc-settings-msg { margin: 10px 0 0; font-size: 12px; opacity: 0.85; }",
  "[data-seminar-copilot-status] { margin: auto; max-width: 420px; padding: 32px 24px; text-align: center; line-height: 1.7; }",
  "[data-seminar-copilot-status] h3 { margin: 0 0 8px; font-size: 15px; }",
  "[data-seminar-copilot-status] p { margin: 0 0 12px; font-size: 13px; opacity: 0.72; }",
  "[data-seminar-copilot-status] code { display: block; margin: 0 0 16px; padding: 10px 12px; font-size: 12px; text-align: left; word-break: break-all; border-radius: 8px; background: rgba(127,127,127,0.14); }",
  "[data-seminar-copilot-status] .sc-actions { display: flex; gap: 8px; justify-content: center; margin-bottom: 12px; }",
  "[data-seminar-copilot-status] .sc-spinner { display: inline-block; width: 14px; height: 14px; margin-right: 8px; vertical-align: -2px; border: 2px solid rgba(127,127,127,0.35); border-top-color: currentColor; border-radius: 50%; animation: sc-spin 0.9s linear infinite; }",
  "@keyframes sc-spin { to { transform: rotate(360deg); } }",
].join("\n")

window.__ModuleLoader__.load({
  id: PACKAGE_NAME,
  factory: function (require) {
    var React = require("react")
    var primitives = require("@deepseek-ai/dsh-client-ui-primitives")
    var GuideIcon = primitives.IconMicrophoneOutlineArtwork || primitives.PluginArtworkDefault
    var Button = primitives.Button

    var style = document.createElement("style")
    style.dataset.plugin = PACKAGE_NAME
    style.textContent = CSS
    document.head.append(style)

    function probe(url) {
      // no-cors：只关心服务是否可达；服务在线时得到不透明响应也算成功。
      return fetch(url, { mode: "no-cors", cache: "no-store" }).then(function () { return true }, function () { return false })
    }

    function appReady() {
      return Promise.all([probe(HEALTH_URL), probe(FRONTEND_URL)]).then(function (r) {
        return r[0] && r[1]
      })
    }

    function el(tag, props) {
      var children = Array.prototype.slice.call(arguments, 2)
      return React.createElement.apply(null, [tag, props].concat(children))
    }

    function SettingsPanel(props) {
      var t = props.t
      var cfgState = React.useState(null)
      var cfg = cfgState[0]
      var setCfg = cfgState[1]
      var keyState = React.useState("")
      var apiKey = keyState[0]
      var setApiKey = keyState[1]
      var msgState = React.useState("")
      var message = msgState[0]
      var setMessage = msgState[1]
      var busyState = React.useState(false)
      var busy = busyState[0]
      var setBusy = busyState[1]

      React.useEffect(function () {
        var alive = true
        fetch(CONTROL_URL + "/config").then(function (r) { return r.json() }).then(function (data) {
          if (alive) setCfg(data)
        }, function () {
          if (alive) setMessage(t("settingsUnavailable"))
        })
        return function () { alive = false }
      }, [])

      function update(field) {
        return function (e) {
          var next = Object.assign({}, cfg)
          next[field] = e.target.value
          setCfg(next)
        }
      }

      function save(restart) {
        setBusy(true)
        setMessage("")
        var body = {
          asrBackend: cfg.asrBackend,
          bailianModel: cfg.bailianModel,
          asrLanguage: cfg.asrLanguage,
          repoPath: cfg.repoPath,
        }
        if (apiKey) body.dashscopeApiKey = apiKey
        fetch(CONTROL_URL + "/config", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        }).then(function (r) { return r.json() }).then(function (saved) {
          setCfg(saved)
          setApiKey("")
          if (!restart) {
            setMessage(saved.restartRequired ? t("savedNeedRestart") : t("saved"))
            setBusy(false)
            return
          }
          fetch(CONTROL_URL + "/restart", { method: "POST" }).then(function (r) {
            setBusy(false)
            if (r.status === 409) {
              setMessage(t("restartExternal"))
              return
            }
            setMessage(t("restarting"))
            if (props.onRestart) props.onRestart()
          }, function () {
            setBusy(false)
            setMessage(t("restartFailed"))
          })
        }, function () {
          setBusy(false)
          setMessage(t("settingsUnavailable"))
        })
      }

      if (!cfg) {
        return el("div", { className: "sc-settings" },
          el("h4", null, t("settings")),
          el("p", { className: "sc-settings-msg" }, message || t("settingsLoading")))
      }

      var children = [el("h4", { key: "h" }, t("settings"))]
      children.push(el("label", { key: "lb" }, t("backend")))
      children.push(el("select", { key: "sb", value: cfg.asrBackend, onChange: update("asrBackend"), disabled: busy },
        el("option", { value: "mlx" }, t("backendLocal")),
        el("option", { value: "bailian" }, t("backendBailian"))))

      if (cfg.asrBackend === "bailian") {
        children.push(el("label", { key: "lk" }, t("apiKey")))
        children.push(el("input", {
          key: "ik",
          type: "password",
          value: apiKey,
          autoComplete: "off",
          placeholder: cfg.hasApiKey ? t("apiKeySaved") + " " + cfg.apiKeyPreview : "sk-…",
          disabled: busy,
          onChange: function (e) { setApiKey(e.target.value) },
        }))
        children.push(el("label", { key: "lm" }, t("modelLabel")))
        children.push(el("input", { key: "im", type: "text", value: cfg.bailianModel, disabled: busy, onChange: update("bailianModel") }))
        children.push(el("label", { key: "ll" }, t("langLabel")))
        children.push(el("select", { key: "sl", value: cfg.asrLanguage, onChange: update("asrLanguage"), disabled: busy },
          el("option", { value: "auto" }, t("langAuto")),
          el("option", { value: "zh" }, "中文"),
          el("option", { value: "en" }, "English")))
        children.push(el("p", { key: "pv", className: "sc-privacy" }, t("bailianPrivacy")))
      }

      children.push(el("label", { key: "lr" }, t("repoPath")))
      children.push(el("input", { key: "ir", type: "text", value: cfg.repoPath, disabled: busy, onChange: update("repoPath") }))
      if (!cfg.repoOk) children.push(el("p", { key: "rw", className: "sc-warn" }, t("repoMissing")))

      children.push(el("div", { key: "ac", className: "sc-settings-actions" },
        el(Button, { variant: "ghost", disabled: busy, onClick: function () { save(false) } }, t("save")),
        el(Button, { variant: "ghost", disabled: busy, onClick: function () { save(true) } }, t("saveRestart")),
        el(Button, { variant: "ghost", disabled: busy, onClick: props.onClose }, t("close"))))
      if (message) children.push(el("p", { key: "ms", className: "sc-settings-msg" }, message))

      return el("div", { className: "sc-settings" }, children)
    }

    function SeminarBody(props) {
      var t = props.t
      var statusState = React.useState("checking")
      var status = statusState[0]
      var setStatus = statusState[1]
      var retryState = React.useState(0)
      var retry = retryState[0]
      var setRetry = retryState[1]
      var copiedState = React.useState(false)
      var copied = copiedState[0]
      var setCopied = copiedState[1]
      var settingsState = React.useState(false)
      var showSettings = settingsState[0]
      var setShowSettings = settingsState[1]

      React.useEffect(function () {
        var alive = true
        var pollTimer = null
        setStatus("checking")

        function poll(attempt) {
          if (!alive) return
          appReady().then(function (ready) {
            if (!alive) return
            if (ready) {
              setStatus("online")
              return
            }
            if (attempt >= POLL_MAX_ATTEMPTS) {
              setStatus("offline")
              return
            }
            pollTimer = setTimeout(function () { poll(attempt + 1) }, POLL_INTERVAL_MS)
          })
        }

        appReady().then(function (ready) {
          if (!alive) return
          if (ready) {
            setStatus("online")
            return
          }
          // 服务未运行：请求插件宿主侧的控制端点自动拉起 dev.sh。
          fetch(CONTROL_URL + "/start", { method: "POST" }).then(function () {
            if (!alive) return
            setStatus("starting")
            pollTimer = setTimeout(function () { poll(1) }, POLL_INTERVAL_MS)
          }, function () {
            // 控制端点不可用（宿主侧未加载等）：回退为手动命令提示。
            if (alive) setStatus("offline")
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

      if (status === "online") {
        return el("div", { "data-seminar-copilot": "" },
          el("button", { className: "sc-gear", type: "button", onClick: function () { setShowSettings(!showSettings) } }, t("settings")),
          showSettings ? el(SettingsPanel, {
            t: t,
            onClose: function () { setShowSettings(false) },
            onRestart: function () { setShowSettings(false); setRetry(retry + 1) },
          }) : null,
          el("iframe", { src: FRONTEND_URL, allow: "microphone", title: t("tabTitle") }))
      }

      if (status === "starting" || status === "checking") {
        return el("div", { "data-seminar-copilot": "" },
          el("div", { "data-seminar-copilot-status": "" },
            el("h3", null,
              el("span", { className: "sc-spinner" }),
              status === "starting" ? t("startingTitle") : t("checking")),
            status === "starting" ? el("p", null, t("startingBody")) : null))
      }

      return el("div", { "data-seminar-copilot": "" },
        el("div", { "data-seminar-copilot-status": "" },
          el("h3", null, t("offlineTitle")),
          el("p", null, t("offlineBody")),
          el("code", null, START_COMMAND),
          el("div", { className: "sc-actions" },
            el(Button, { variant: "ghost", onClick: function () {
              if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(START_COMMAND).then(function () { setCopied(true) })
              }
            } }, copied ? t("copied") : t("copy")),
            el(Button, { variant: "ghost", onClick: function () { setRetry(retry + 1) } }, t("retry"))),
          el("p", null, t("hintMic"))))
    }

    function SeminarTitle(props) {
      return el("span", null, props.t("tabTitle"))
    }

    return {
      apply: function (ctx) {
        ctx.effect(function () {
          return ctx.locale.register(LOCALE_NS, {
            zh: {
              tabTitle: "会议录音",
              guideTitle: "会议录音",
              guideDesc: "本地研讨会录音与实时转写（Seminar Copilot）",
              checking: "正在检测 Seminar Copilot 服务…",
              startingTitle: "正在启动 Seminar Copilot…",
              startingBody: "首次启动需要加载 Whisper 模型，一般一分钟内就绪，请稍候。",
              offlineTitle: "Seminar Copilot 未能自动启动",
              offlineBody: "可在 /tmp/seminar-copilot-dev.log 查看启动日志，或在终端手动运行：",
              copy: "复制启动命令",
              copied: "已复制",
              retry: "重新检测",
              hintMic: "首次录音时浏览器会请求麦克风权限。",
              settings: "识别设置",
              settingsLoading: "正在读取配置…",
              settingsUnavailable: "控制端点不可用，无法读写设置。",
              backend: "识别后端",
              backendLocal: "本地 Whisper（默认，音频不出本机）",
              backendBailian: "阿里云百炼 API（录音片段发送到云端）",
              apiKey: "DashScope API Key",
              apiKeySaved: "已保存",
              modelLabel: "识别模型",
              langLabel: "识别语言",
              langAuto: "自动",
              repoPath: "Seminar Copilot 仓库路径",
              repoMissing: "该路径下找不到 scripts/dev.sh，请改为 seminar-copilot 仓库的绝对路径。",
              bailianPrivacy: "注意：百炼模式下录音片段会上传到阿里云识别，请确认录音内容允许出本机。",
              save: "保存",
              saveRestart: "保存并重启",
              close: "关闭",
              saved: "已保存。",
              savedNeedRestart: "已保存，重启服务后生效。",
              restarting: "正在重启服务…",
              restartFailed: "重启请求失败，请查看 /tmp/seminar-copilot-dev.log。",
              restartExternal: "当前服务由外部终端启动，插件无法重启，请在该终端手动重启。",
            },
            en: {
              tabTitle: "Meeting Recorder",
              guideTitle: "Meeting Recorder",
              guideDesc: "Local seminar recording with live transcription (Seminar Copilot)",
              checking: "Checking the Seminar Copilot service…",
              startingTitle: "Starting Seminar Copilot…",
              startingBody: "The first launch loads the Whisper model and is usually ready within a minute.",
              offlineTitle: "Seminar Copilot could not auto-start",
              offlineBody: "Check /tmp/seminar-copilot-dev.log, or start it manually in a terminal:",
              copy: "Copy start command",
              copied: "Copied",
              retry: "Check again",
              hintMic: "The browser will ask for microphone access on first recording.",
              settings: "ASR settings",
              settingsLoading: "Loading settings…",
              settingsUnavailable: "Control endpoint unavailable; cannot read or write settings.",
              backend: "ASR backend",
              backendLocal: "Local Whisper (default, audio stays on this machine)",
              backendBailian: "Aliyun Bailian API (audio clips sent to the cloud)",
              apiKey: "DashScope API key",
              apiKeySaved: "Saved",
              modelLabel: "Recognition model",
              langLabel: "Recognition language",
              langAuto: "Auto",
              repoPath: "Seminar Copilot repository path",
              repoMissing: "scripts/dev.sh not found under this path; set it to the seminar-copilot clone.",
              bailianPrivacy: "Note: in Bailian mode audio clips are uploaded to Aliyun for recognition.",
              save: "Save",
              saveRestart: "Save and restart",
              close: "Close",
              saved: "Saved.",
              savedNeedRestart: "Saved. Restart the service to apply.",
              restarting: "Restarting service…",
              restartFailed: "Restart request failed; see /tmp/seminar-copilot-dev.log.",
              restartExternal: "The service was started from an external terminal; restart it there.",
            },
          })
        }, "seminar-copilot.locale")

        var t = ctx.locale.bind(LOCALE_NS)

        ctx.effect(function () {
          return ctx.sidebarRightTabs.register({
            id: PACKAGE_NAME,
            kind: "seminar",
            keepMounted: true,
            title: function () { return t("tabTitle") },
            guide: [{
              id: "record",
              order: 30,
              title: function () { return t("guideTitle") },
              description: function () { return t("guideDesc") },
              icon: GuideIcon,
            }],
          })
        }, "seminar-copilot.tabs")

        ctx.slots.inject("sidebar.right.pane.tab", function () {
          return ctx.slots.register({ name: "sidebar.right.pane.tab", key: PACKAGE_NAME, locale: LOCALE_NS }, SeminarBody)
        })

        ctx.slots.inject("sidebar.right.pane.tab.title", function () {
          return ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: PACKAGE_NAME, locale: LOCALE_NS }, SeminarTitle)
        })
      },
    }
  },
})
