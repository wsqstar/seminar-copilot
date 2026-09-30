# seminar-copilot-dsh-plugin

把 Seminar Copilot 接入 DeepSeek Harness Web GUI 的外部动态客户端插件。

## 功能

- 在右侧栏「开始」引导页的「新建终端」下方新增「会议录音」入口（guide order 30）。
- 点击后打开嵌入 `http://127.0.0.1:5173`（Vite dev server）的侧栏 tab。
- tab 正文先以 no-cors 探测 `http://127.0.0.1:8765/api/health`；服务未运行时
  展示启动命令（可复制）、「重新检测」按钮与麦克风权限提示。
- `keepMounted: true`：录音中的 iframe 在切换 tab / 会话 / 折叠侧栏时保持挂载。

## 安装

在 DSH checkout 下执行：

    pnpm dsh plugin --profile web add file:/Users/shiqi/Coding/github/wsqstar/seminar-copilot/dsh-plugin

安装后在 Web GUI「设置 → 内置插件」页面可看到并可卸载。
新增/移除插件包需要重启 Web GUI（`dsh-web.sh restart`）后生效。

## 结构

- `package.json`：声明 `main`（宿主侧）与 `exports["./client"]`（浏览器侧），
  以及 `dsh.bundle.patch` / `dsh.client.platform`。
- `cordis.patch.yml`：向 cordis 插件树插入一行（id `seminar-copilot`）。
- `index.js`：宿主侧占位 `apply()`。
- `client.js`：浏览器侧模块，经 `window.__ModuleLoader__.load` 注册，
  React 与 `@deepseek-ai/dsh-client-ui-primitives` 由宿主外部化提供。

## 约束

- 后端（FastAPI 8765）与前端（Vite 5173）仍需通过 `scripts/dev.sh` 手动启动；
  插件只探测与提示，不负责拉起进程。
- iframe 需要 `allow="microphone"`；5173 localhost 属于安全上下文，麦克风可用。
- 后端 CORS 仅允许 5173 源，因此健康探测使用 `mode: "no-cors"`（不透明响应）。
