# seminar-copilot-dsh-plugin

把 Seminar Copilot 接入 DeepSeek Harness Web GUI 的外部动态插件（宿主侧 + 客户端）。

## 功能

- 在右侧栏「开始」引导页的「新建终端」下方新增「会议录音」入口（guide order 30）。
- 点击后打开嵌入 `http://127.0.0.1:5173`（Vite dev server）的侧栏 tab。
- **自动启动**：服务未运行时，客户端请求插件宿主侧的 `127.0.0.1:8766` 控制端点，
  由 DSH 主进程拉起 `scripts/dev.sh`（FastAPI 8765 + Vite 5173），页面显示启动
  进度并轮询就绪后自动加载 iframe；控制端点不可用时回退为手动命令提示。
- `keepMounted: true`：录音中的 iframe 在切换 tab / 会话 / 折叠侧栏时保持挂载。

## 宿主侧控制端点（127.0.0.1:8766，仅 loopback）

- `GET /status` → `{ backend, frontend, managed }`：前后端健康状态。
- `POST /start` → `{ result }`：幂等启动；已在运行的外部实例直接收养，不重复拉起。
- dev.sh 以独立进程组 detached 启动，日志写入 `/tmp/seminar-copilot-dev.log`；
  DSH 宿主退出时按进程组 SIGTERM 回收自己拉起的进程。

## 安装

在 DSH checkout 下执行：

    pnpm dsh plugin --profile web add file:/Users/shiqi/Coding/github/wsqstar/seminar-copilot/dsh-plugin

注意：`file:` 依赖是**拷贝**而非符号链接。修改本目录后需重新同步：

    cp dsh-plugin/{index.js,client.js,package.json} ~/.dsh/profiles/web/node_modules/seminar-copilot-dsh-plugin/

新增/移除插件包或宿主侧（index.js）变更需重启 Web GUI（`dsh-web.sh restart`）生效。

## 结构

- `package.json`：声明 `main`（宿主侧）与 `exports["./client"]`（浏览器侧），
  以及 `dsh.bundle.patch` / `dsh.client.platform`。
- `cordis.patch.yml`：向 cordis 插件树插入一行（id `seminar-copilot`）。
- `index.js`：宿主侧——控制端点 + 按需拉起 dev.sh + 退出回收。
- `client.js`：浏览器侧模块，经 `window.__ModuleLoader__.load` 注册，
  React 与 `@deepseek-ai/dsh-client-ui-primitives` 由宿主外部化提供。

## 约束

- 后端 CORS 仅允许 5173 源，因此服务健康探测使用 `mode: "no-cors"`（不透明响应）；
  8766 控制端点是插件自己的服务，主动设置 `Access-Control-Allow-Origin: *`。
- iframe 需要 `allow="microphone"`；5173 localhost 属于安全上下文，麦克风可用。
- 8766 仅监听 loopback、无任何鉴权：本机任何进程都能触发启动。
  这与本地优先的定位一致，但不要把 DSH 暴露到局域网（`--host 0.0.0.0`）。
