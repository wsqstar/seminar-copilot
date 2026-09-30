/**
 * Seminar Copilot DSH 插件的宿主侧。
 *
 * 在 DSH 主进程内维护一个 127.0.0.1:8766 的本地控制端点，负责按需拉起
 * Seminar Copilot 开发栈（scripts/dev.sh：FastAPI 8765 + Vite 5173），
 * 并管理 ASR 后端配置（本地 Whisper / 阿里云百炼）。配置保存在
 * ~/.dsh/seminar-copilot/config.json（0600），启动时以进程级环境变量注入，
 * 不注册任何全局环境变量，也不写 shell 配置。
 *
 *   GET  /status  → { backend, frontend, managed, config }  服务健康状态
 *   POST /start   → { result }                              幂等启动
 *   POST /stop    → { result }                              停止插件拉起的实例
 *   POST /restart → { result }                              重启（配置变更后生效）
 *   GET  /config  → 当前 ASR 配置（API Key 只返回掩码，绝不回传明文）
 *   POST /config  → 更新配置；dashscopeApiKey 缺省/空串=保持不变，null=清除
 *
 * 端点仅监听 loopback，并带 Access-Control-Allow-Origin: *，供客户端插件
 * （浏览器侧，源 127.0.0.1:3080）直接 fetch。dev.sh 以独立进程组 detached
 * 启动，日志写入 /tmp/seminar-copilot-dev.log；宿主退出时按进程组 SIGTERM
 * 回收自己拉起的子进程（外部已运行的实例则直接收养，不重复启动）。
 */

import { spawn } from "node:child_process"
import { createServer } from "node:http"
import { chmodSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

var REPO = "/Users/shiqi/Coding/github/wsqstar/seminar-copilot"
var HEALTH_URL = "http://127.0.0.1:8765/api/health"
var FRONTEND_URL = "http://127.0.0.1:5173/"
var CONTROL_HOST = "127.0.0.1"
var CONTROL_PORT = 8766
var LOG_FILE = "/tmp/seminar-copilot-dev.log"
var CONFIG_DIR = join(homedir(), ".dsh", "seminar-copilot")
var CONFIG_FILE = join(CONFIG_DIR, "config.json")
var MAX_BODY_BYTES = 8192

var DEFAULT_CONFIG = {
  asrBackend: "mlx",
  bailianModel: "paraformer-v2",
  asrLanguage: "auto",
  dashscopeApiKey: "",
  // seminar-copilot 仓库（scripts/dev.sh 所在项目）的绝对路径，
  // 插件发布后由用户在设置面板中改成自己机器上的克隆位置。
  repoPath: REPO,
}
var ASR_BACKENDS = ["mlx", "bailian"]
var ASR_LANGUAGES = ["auto", "zh", "en"]

var child = null
var config = loadConfig()

function sanitizeConfig(input, base) {
  var out = Object.assign({}, base)
  if (!input || typeof input !== "object") return out
  if (ASR_BACKENDS.indexOf(input.asrBackend) >= 0) out.asrBackend = input.asrBackend
  if (typeof input.bailianModel === "string" && input.bailianModel.trim() && input.bailianModel.length <= 64) {
    out.bailianModel = input.bailianModel.trim()
  }
  if (ASR_LANGUAGES.indexOf(input.asrLanguage) >= 0) out.asrLanguage = input.asrLanguage
  if (typeof input.dashscopeApiKey === "string" && input.dashscopeApiKey.length <= 256) {
    out.dashscopeApiKey = input.dashscopeApiKey.trim()
  }
  if (typeof input.repoPath === "string") {
    var p = input.repoPath.trim()
    if (p.length > 1 && p.length <= 512 && p.charAt(0) === "/") out.repoPath = p
  }
  return out
}

function loadConfig() {
  try {
    return sanitizeConfig(JSON.parse(readFileSync(CONFIG_FILE, "utf8")), DEFAULT_CONFIG)
  } catch (error) {
    return Object.assign({}, DEFAULT_CONFIG)
  }
}

function saveConfig() {
  mkdirSync(CONFIG_DIR, { recursive: true })
  writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2) + "\n", { mode: 0o600 })
  try { chmodSync(CONFIG_FILE, 0o600) } catch (error) { /* 已尽力收紧权限 */ }
}

function maskKey(key) {
  if (!key) return ""
  if (key.length <= 8) return "****"
  return key.slice(0, 3) + "****" + key.slice(-4)
}

function publicConfig() {
  return {
    asrBackend: config.asrBackend,
    bailianModel: config.bailianModel,
    asrLanguage: config.asrLanguage,
    hasApiKey: Boolean(config.dashscopeApiKey),
    apiKeyPreview: maskKey(config.dashscopeApiKey),
    repoPath: config.repoPath,
    repoOk: existsSync(join(config.repoPath, "scripts", "dev.sh")),
  }
}

// 进程级注入：只覆盖 UI 管理的键，其余环境原样继承；不污染宿主进程。
function buildEnv() {
  var env = Object.assign({}, process.env)
  env.SEMINAR_ASR_BACKEND = config.asrBackend
  if (config.asrBackend === "bailian") {
    if (config.dashscopeApiKey) env.DASHSCOPE_API_KEY = config.dashscopeApiKey
    env.SEMINAR_BAILIAN_MODEL = config.bailianModel
    if (config.asrLanguage !== "auto") env.SEMINAR_ASR_LANGUAGE = config.asrLanguage
  }
  return env
}

function healthy(url) {
  var controller = new AbortController()
  var timer = setTimeout(function () { controller.abort() }, 1500)
  return fetch(url, { signal: controller.signal }).then(function () {
    clearTimeout(timer)
    return true
  }, function () {
    clearTimeout(timer)
    return false
  })
}

function spawnStack() {
  var out = openSync(LOG_FILE, "a")
  child = spawn("bash", ["scripts/dev.sh"], {
    cwd: config.repoPath,
    detached: true,
    stdio: ["ignore", out, out],
    env: buildEnv(),
  })
  child.on("exit", function () { child = null })
  child.unref()
}

function stopChild() {
  return new Promise(function (resolve) {
    if (!child) { resolve(false); return }
    var target = child
    var done = false
    function finish() { if (!done) { done = true; resolve(true) } }
    target.on("exit", finish)
    setTimeout(function () {
      try { process.kill(-target.pid, "SIGKILL") } catch (error) { /* 已退出 */ }
      finish()
    }, 5000)
    try { process.kill(-target.pid, "SIGTERM") } catch (error) { finish() }
  })
}

function ensureStarted() {
  return healthy(HEALTH_URL).then(function (up) {
    if (up) return "already-running"
    if (child) return "starting"
    spawnStack()
    return "spawned"
  })
}

function readBody(req) {
  return new Promise(function (resolve) {
    var chunks = []
    var size = 0
    req.on("data", function (chunk) {
      size += chunk.length
      if (size > MAX_BODY_BYTES) { req.destroy(); resolve(""); return }
      chunks.push(chunk)
    })
    req.on("end", function () { resolve(Buffer.concat(chunks).toString("utf8")) })
    req.on("error", function () { resolve("") })
  })
}

function sendJson(res, status, payload) {
  res.writeHead(status, { "content-type": "application/json" })
  res.end(JSON.stringify(payload))
}

function handleConfigPost(req, res) {
  readBody(req).then(function (body) {
    var patch = null
    try { patch = JSON.parse(body || "{}") } catch (error) { patch = null }
    if (!patch || typeof patch !== "object") {
      sendJson(res, 400, { error: "invalid-json" })
      return
    }
    // API Key：缺省或空串 = 保持不变；显式 null = 清除。
    if (patch.dashscopeApiKey === null) {
      patch.dashscopeApiKey = ""
    } else if (!patch.dashscopeApiKey) {
      patch.dashscopeApiKey = config.dashscopeApiKey
    }
    config = sanitizeConfig(patch, config)
    saveConfig()
    healthy(HEALTH_URL).then(function (running) {
      sendJson(res, 200, Object.assign(publicConfig(), { restartRequired: running }))
    })
  })
}

function handleStop(res) {
  if (!child) {
    healthy(HEALTH_URL).then(function (up) {
      if (up) sendJson(res, 409, { error: "external", message: "服务由外部进程启动，无法从插件停止" })
      else sendJson(res, 200, { result: "already-stopped" })
    })
    return
  }
  stopChild().then(function () { sendJson(res, 200, { result: "stopped" }) })
}

function handleRestart(res) {
  healthy(HEALTH_URL).then(function (up) {
    if (up && !child) {
      sendJson(res, 409, { error: "external", message: "服务由外部终端启动，请手动重启" })
      return
    }
    stopChild().then(function () {
      spawnStack()
      sendJson(res, 200, { result: "restarted" })
    })
  })
}

export function apply(ctx) {
  var server = createServer(function (req, res) {
    res.setHeader("Access-Control-Allow-Origin", "*")
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    res.setHeader("Access-Control-Allow-Headers", "content-type")
    if (req.method === "OPTIONS") {
      res.writeHead(204)
      res.end()
      return
    }
    if (req.url === "/status" && req.method === "GET") {
      Promise.all([healthy(HEALTH_URL), healthy(FRONTEND_URL)]).then(function (r) {
        sendJson(res, 200, { backend: r[0], frontend: r[1], managed: child !== null, config: publicConfig() })
      })
      return
    }
    if (req.url === "/start" && req.method === "POST") {
      ensureStarted().then(function (result) { sendJson(res, 200, { result: result }) })
      return
    }
    if (req.url === "/stop" && req.method === "POST") { handleStop(res); return }
    if (req.url === "/restart" && req.method === "POST") { handleRestart(res); return }
    if (req.url === "/config" && req.method === "GET") { sendJson(res, 200, publicConfig()); return }
    if (req.url === "/config" && req.method === "POST") { handleConfigPost(req, res); return }
    sendJson(res, 404, { error: "not-found" })
  })

  server.on("error", function (error) {
    // 端口被占用等情况下静默降级：客户端探测不到控制端点时会回退到手动提示。
    if (ctx && ctx.logger) ctx.logger("seminar-copilot").warn("control server failed: %s", error.message)
  })

  server.listen(CONTROL_PORT, CONTROL_HOST)

  ctx.effect(function () {
    return function () {
      server.close()
      if (child) {
        try {
          process.kill(-child.pid, "SIGTERM")
        } catch (error) { /* 进程组可能已退出 */ }
        child = null
      }
    }
  }, "seminar-copilot.host")
}
