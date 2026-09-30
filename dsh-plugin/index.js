/**
 * Seminar Copilot DSH 插件的宿主侧。
 *
 * 在 DSH 主进程内维护一个 127.0.0.1:8766 的本地控制端点，并负责按需拉起
 * Seminar Copilot 开发栈（scripts/dev.sh：FastAPI 8765 + Vite 5173）。
 *
 *   GET  /status  → { backend, frontend, managed }   服务健康状态
 *   POST /start   → { result }                        幂等启动（已在运行则直接返回）
 *
 * 端点仅监听 loopback，并带 Access-Control-Allow-Origin: *，供客户端插件
 * （浏览器侧，源 127.0.0.1:3080）直接 fetch。dev.sh 以独立进程组 detached
 * 启动，日志写入 /tmp/seminar-copilot-dev.log；宿主退出时按进程组 SIGTERM
 * 回收自己拉起的子进程（外部已运行的实例则直接收养，不重复启动）。
 */

import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { openSync } from 'node:fs'

var REPO = '/Users/shiqi/Coding/github/wsqstar/seminar-copilot'
var HEALTH_URL = 'http://127.0.0.1:8765/api/health'
var FRONTEND_URL = 'http://127.0.0.1:5173/'
var CONTROL_HOST = '127.0.0.1'
var CONTROL_PORT = 8766
var LOG_FILE = '/tmp/seminar-copilot-dev.log'

var child = null

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

function ensureStarted() {
  return healthy(HEALTH_URL).then(function (up) {
    if (up) return 'already-running'
    if (child) return 'starting'
    var out = openSync(LOG_FILE, 'a')
    child = spawn('bash', ['scripts/dev.sh'], {
      cwd: REPO,
      detached: true,
      stdio: ['ignore', out, out],
      env: process.env,
    })
    child.on('exit', function () { child = null })
    child.unref()
    return 'spawned'
  })
}

function sendJson(res, status, payload) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(payload))
}

export function apply(ctx) {
  var server = createServer(function (req, res) {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    res.setHeader('Access-Control-Allow-Headers', 'content-type')
    if (req.method === 'OPTIONS') {
      res.writeHead(204)
      res.end()
      return
    }
    if (req.url === '/status' && req.method === 'GET') {
      Promise.all([healthy(HEALTH_URL), healthy(FRONTEND_URL)]).then(function (r) {
        sendJson(res, 200, { backend: r[0], frontend: r[1], managed: child !== null })
      })
      return
    }
    if (req.url === '/start' && req.method === 'POST') {
      ensureStarted().then(function (result) {
        sendJson(res, 200, { result: result })
      })
      return
    }
    sendJson(res, 404, { error: 'not-found' })
  })

  server.on('error', function (error) {
    // 端口被占用等情况下静默降级：客户端探测不到控制端点时会回退到手动提示。
    if (ctx && ctx.logger) ctx.logger('seminar-copilot').warn('control server failed: %s', error.message)
  })

  server.listen(CONTROL_PORT, CONTROL_HOST)

  ctx.effect(function () {
    return function () {
      server.close()
      if (child) {
        try {
          process.kill(-child.pid, 'SIGTERM')
        } catch (error) { /* 进程组可能已退出 */ }
        child = null
      }
    }
  }, 'seminar-copilot.host')
}
