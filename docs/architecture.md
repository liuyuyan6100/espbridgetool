# ESP32 Serial Bridge — 系统架构文档

> **版本**：v2.2（阶段 1 远程接入已实现）
> **最后更新**：2026-07-04
> **维护说明**：本文档描述系统的进程模型、模块划分、数据流与关键设计决策。
> 任何影响进程边界、模块职责、状态归属或数据流方向的改动，都应同步更新本文档。

---

## 1. 系统全景

> 本节描述当前已实现架构。关于云端接入与多设备扩展的目标架构，
> 见 [远程接入与多设备架构设计](./remote-access-design.md) 及其配图。

Serial Bridge 是一个 ESP32 开发调试代理，架在「操作者」与「ESP32 设备」之间，
统一管理串口独占、日志收集、ESP-IDF 工具链调用。操作者有两类：

- **人** — 通过浏览器 Web UI（xterm.js 终端 + 日志面板 + 快捷按钮）
- **AI 智能体** — 通过标准 MCP（Model Context Protocol）协议

两类操作者共享同一份设备状态与日志流，可同时观察、必要时交替干预。

```
┌─────────────────────────────────────────────────────────────────┐
│                         操作者层                                  │
│                                                                  │
│   ┌─────────────┐                ┌──────────────────────────┐   │
│   │  Web 浏览器  │                │  AI Agent                │   │
│   │  xterm.js   │                │  (WorkBuddy/Cursor/      │   │
│   │  日志面板    │                │   Claude Desktop)        │   │
│   └──────┬──────┘                └────────────┬─────────────┘   │
│          │ HTTP + WebSocket              stdio │ (MCP/JSON-RPC)  │
└──────────┼────────────────────────────────────┼─────────────────┘
           │                                    │
           ▼                                    ▼
┌─────────────────────────────┐    ┌─────────────────────────────┐
│  serial_bridge.py (Web 服务) │◄───│  mcp_server.py (MCP 服务)    │
│  FastAPI + uvicorn :8080     │ HTTP│  FastMCP + httpx (stdio)    │
│  ┌───────────────────────┐   │     │  ┌───────────────────────┐ │
│  │ SerialManager (串口)   │   │     │  │ 19 个 tools            │ │
│  │ LogBuffer (日志+seq)   │   │     │  │ 3 个 resources         │ │
│  │ IdfTool (工具链)       │   │     │  │ _call() HTTP 封装       │ │
│  │ Web 前端 (static/)     │   │     │  └───────────────────────┘ │
│  └───────────────────────┘   │     └─────────────────────────────┘
└──────────┬──────────────────┘                    │
           │ pyserial (独占 COM 口)                  │ (无设备访问，纯代理)
           ▼                                        ▼
┌─────────────────────┐                  (mcp_server 不直接碰设备，
│      ESP32 设备       │                   所有操作转发给 Web 服务)
│      USB CDC COM6    │
└─────────────────────┘
```

---

## 2. 进程模型

系统由 **两个独立进程** 组成，职责严格分离：

| 进程 | 入口 | 传输 | 持有设备？ | 生命周期 |
|---|---|---|---|---|
| **Web 服务** `serial_bridge.py` | `python serial_bridge.py` / `start.bat` | HTTP + WebSocket (:8080) | **是** — 独占串口、调用 idf.py | 长驻，用户手动启动 |
| **MCP 服务** `mcp_server.py` | 由 agent spawn | stdio (JSON-RPC over stdin/stdout) | **否** — 纯 HTTP 客户端，转发请求 | 由 agent 管生命周期 |

### 2.1 为什么是两个进程而不是一个

1. **串口独占** — 同一个 COM 口只能被一个进程持有。若 MCP server 直接开串口，就会和 Web 服务抢端口。让 Web 服务做唯一的串口持有者，MCP server 通过 HTTP 转发，天然避免冲突。
2. **多 agent 共存** — 多个 agent（或同一 agent 的多次会话）可以各自 spawn MCP 进程，它们都连同一个 Web 服务，共享设备状态而互不干扰。
3. **Web UI 不中断** — agent 在编译/烧录时，用户仍能在浏览器看实时日志。
4. **崩溃隔离** — MCP server 崩了不会拖垮串口连接；Web 服务重启后 agent 自动重连即可。

### 2.2 启动顺序

```
1. 用户启动 serial_bridge.py（双击 start.bat）
   → 服务监听 127.0.0.1:8080，自动打开 .env 配置的串口
2. 用户在 agent 配置文件注册 mcp_server.py
   → agent 启动时 spawn mcp_server.py（stdio）
   → mcp_server 通过 HTTP 连接 Web 服务（连接失败时返回友好错误，不崩溃）
3. agent 调用 MCP 工具 → mcp_server 转发 HTTP → Web 服务执行 → 返回结果
```

---

## 3. 模块清单

### 3.1 Web 服务内部模块（`serial_bridge.py` + 同目录 .py 文件）

当前为扁平结构（单文件路由 + 三个独立类文件），便于后续拆包。

| 模块 | 文件 | 职责 | 持有状态 |
|---|---|---|---|
| **SerialManager** | `serial_manager.py` | 串口独占管理、后台读取线程、烧录时临时释放、多回调分发 | 串口句柄、回调列表、端口/波特率 |
| **LogBuffer** | `log_buffer.py` | 环形日志缓冲、历史查询、关键字过滤、**序列号增量获取** | 日志行 deque、`_seq` 序列号 |
| **IdfTool** | `idf_tool.py` | 封装 idf.py 命令（build/flash/clean/bmgr/menuconfig），PowerShell 子进程执行，输出回调 | 项目目录、板型、export 脚本路径 |
| **Web 路由层** | `serial_bridge.py` | FastAPI 路由、WebSocket 管理、配置持久化(.env)、快捷命令 | `WS_CLIENTS`、`STATS`、`QUICK_COMMANDS`、`IDF` 实例、`MAIN_LOOP` |
| **Web 前端** | `static/index.html` + `app.js` + `style.css` | xterm.js 终端、日志面板、快捷按钮、配置面板 | 浏览器端状态 |

### 3.2 MCP 服务模块（`mcp_server.py`）

| 组成 | 职责 |
|---|---|
| `_call()` HTTP 封装 | 统一请求/错误处理，bridge 离线时返回可读错误而非抛异常 |
| 19 个 `@mcp.tool()` | 串口/日志/命令/IDF 全套操作，参数与 REST 接口一一对应 |
| 3 个 `@mcp.resource()` | 只读快照（status / recent logs / config），供 agent 主动拉取 |
| 配置加载 | `.env` → 环境变量 → 命令行参数，三级优先 |

### 3.3 模块依赖关系

```
serial_bridge.py
  ├── imports SerialManager   (serial_manager.py)
  ├── imports LogBuffer       (log_buffer.py)
  ├── imports IdfTool         (idf_tool.py)
  └── serves static/          (前端资源)

mcp_server.py
  ├── imports FastMCP         (mcp SDK)
  ├── imports httpx           (HTTP 客户端)
  └── HTTP → serial_bridge.py (运行时依赖，非 import)

idf_tool.py
  └── subprocess → powershell.exe + idf.py  (运行时)

serial_manager.py
  └── pyserial → COM 端口  (运行时)
```

**关键边界**：`mcp_server.py` 与 `serial_manager.py`/`log_buffer.py`/`idf_tool.py` 之间**没有 import 关系**，只通过 HTTP 调用 Web 服务暴露的 REST 接口。这是模块化的核心约束——后续拆包时这条边界不能破。

---

## 4. 数据流

### 4.1 串口读取流（设备 → 操作者）

```
ESP32 USB CDC
    │ (串口数据 bytes)
    ▼
SerialManager.read_loop()  [后台线程]
    │ (bytes，按行分割)
    ▼
LogBuffer.append(line)     [写日志 + seq++]
    │
    ├─► SerialManager 的所有回调（多回调机制）
    │     ├─► 日志回调 → broadcast 给 /ws/log 的所有客户端
    │     └─► 终端回调 → send_bytes 给对应 /ws/terminal?mode=serial 的客户端
    │
    └─► (MCP agent 不走推送，通过 /api/log/since 轮询或 send_and_collect 一次性取)
```

**线程跨越点**：串口读取在后台线程，WebSocket 推送需在主事件循环。通过
`asyncio.run_coroutine_threadsafe(coro, MAIN_LOOP)` 调度，`MAIN_LOOP` 在
lifespan 启动时捕获。

### 4.2 命令发送流（操作者 → 设备）

```
┌─ Web UI ─────────────┐    ┌─ MCP Agent ──────────────┐
│ xterm.js 键盘输入     │    │ 调用 send_command /       │
│ 或命令框 /send        │    │      send_and_collect 工具 │
└───────┬──────────────┘    └──────────┬────────────────┘
        │ WebSocket bytes/text          │ HTTP POST
        ▼                               ▼
   SERIAL.send(bytes)            /api/send 或 /api/send-and-collect
        │                               │
        │                               ▼
        │                         SERIAL.send_line(cmd)
        │                               │
        ▼                               ▼
        └──────────► COM 端口 ◄──────────┘
                         │
                         ▼
                      ESP32
```

### 4.3 send_and_collect（agent 核心：发命令→看反馈）

```
agent 调用 send_and_collect(cmd, wait=2.0)
    │
    ▼
mcp_server._call POST /api/send-and-collect
    │
    ▼
serial_bridge: before_seq = BUFFER.last_seq
    │
    ├─► SERIAL.send_line(cmd)       [发命令]
    │
    ▼
await asyncio.sleep(wait_seconds)   [等待设备响应]
    │
    ▼
new_lines, after_seq = BUFFER.get_after_seq(before_seq)  [取期间新日志]
    │
    ▼
返回 {collected_lines, before_seq, after_seq, ...}
    │
    ▼
agent 拿到 collected_lines —— 这就是设备的"响应"
```

这是 MCP 接入的核心价值：agent 不再盲猜设备状态，而是发命令后实际看到设备回了什么。

### 4.4 烧录流（串口释放 → 烧录 → 重连）

```
POST /api/flash
    │
    ▼
with SERIAL.acquire_for_flash():   [上下文管理器]
    │  ├─ 关闭串口 COM6             [释放独占]
    │  │
    │  ├─ IDF.flash(port, board)   [PowerShell 子进程: idf.py -p COM6 flash]
    │  │     └─ 输出实时回调 → LogBuffer → broadcast
    │  │
    │  └─ __exit__: 重新打开 COM6   [自动重连]
    │
    ▼
推送 "[bridge] 烧录成功/失败" 到日志流
    │
    ▼
返回 {ok, output}
```

`acquire_for_flash()` 是串口独占模型的关键——保证烧录工具能拿到端口，烧完立即恢复监控。

### 4.5 配置变更流（运行时 + 持久化）

```
POST /api/config {IDF_PROJECT_DIR, IDF_BOARD, ...}
    │
    ▼
_save_env(updates)     [写回 .env 文件 + 同步 os.environ]
    │
    ▼
_reinit_idf(...)       [重建 IdfTool 实例，立即生效]
    │
    ▼
返回 {ok, message}
```

---

## 5. 状态归属

明确「谁持有哪个状态」，是模块化拆分的前提。当前状态分布：

| 状态 | 持有者 | 生命周期 | 跨进程？ |
|---|---|---|---|
| 串口句柄 | `SerialManager` | Web 服务进程内 | 否（独占） |
| 日志缓冲 + 序列号 | `LogBuffer` | Web 服务进程内 | 否（通过 REST 暴露） |
| IDF 实例/配置 | `serial_bridge.py` 全局 `IDF` | Web 服务进程内 | 否 |
| .env 配置 | 文件系统 | 持久化 | 是（两进程都读） |
| WebSocket 客户端列表 | `serial_bridge.py` 全局 `WS_CLIENTS` | Web 服务进程内 | 否 |
| 收发字节统计 | `serial_bridge.py` 全局 `STATS` | Web 服务进程内 | 否 |
| 快捷命令 | `serial_bridge.py` 全局 `QUICK_COMMANDS` | Web 服务进程内（未持久化） | 否 |
| MCP httpx 客户端 | `mcp_server.py` 全局 `_client` | MCP 进程内 | 否 |

**MCP server 是无状态的** —— 它只是 Web 服务的 HTTP 代理，不缓存任何设备状态。每次工具调用都直接转发。这意味着 Web 服务重启后，agent 无需做任何状态恢复。

---

## 6. 关键设计决策

### 6.1 串口独占模型

单一进程（Web 服务）持有串口句柄。所有写操作（send/flash）都经过它，避免多进程争抢。烧录时通过 `acquire_for_flash()` 上下文管理器临时释放，保证原子性。

### 6.2 日志序列号机制

`LogBuffer` 维护全局递增的 `_seq`，每 append 一行 +1。`get_after_seq(seq)` 返回序列号严格大于 `seq` 的所有行。

**为什么需要**：环形缓冲满了会丢早期行，但 agent 需要可靠的"增量获取"——发命令后只取新日志，不能重复读历史。序列号单调递增（即使旧行被丢弃），保证增量获取的正确性。

**两个用途**：
- `send_and_collect` — 记录 `before_seq`，等待后取 `(before_seq, now]` 的行
- `get_logs_since` — agent 轮询，记住上次 `after_seq`，下次只取增量

### 6.3 MCP-over-HTTP 解耦

MCP server 不直接操作设备，而是通过 HTTP 调用 Web 服务。这条边界带来三个好处：

1. **状态单一来源** — 串口/日志/IDF 配置只在 Web 服务里存一份，不存在双进程状态同步问题
2. **Web UI 与 agent 一致** — 两类操作者看到完全相同的设备状态和日志
3. **可替换性** — 未来若要支持别的 agent 协议（如直接 SSE、gRPC），只需新写一个代理进程，Web 服务不动

远程接入阶段的目标形态（HTTP transport + 多设备 + 两层鉴权）见 [remote-access-design.md](./remote-access-design.md)，配图文件为 [`images/remote-access-architecture.svg`](./images/remote-access-architecture.svg)。

### 6.4 stdio 通信约束

MCP server 用 stdio transport 与 agent 通信，stdout 走 JSON-RPC 协议帧。因此：
- **stdout 绝对不能写任何非协议内容**（包括 print 调试、中文提示）
- 所有诊断信息打 stderr，且用英文（Windows 控制台默认 GBK 编码，中文打 stderr 在 agent 端可能乱码）

### 6.5 错误处理策略

`mcp_server._call()` 捕获所有 HTTP 异常，返回 `{"ok": False, "error": "可读说明"}` 而非抛异常。这让 agent 收到的是结构化错误信息（"bridge 未启动，请先运行 start.bat"），而不是 MCP 层的 traceback。

---

## 7. 配置体系

三级优先（高 → 低）：**命令行参数 > 环境变量 > .env 文件 > 代码默认值**

### 7.1 `.env` 配置项

| 键 | 默认值 | 说明 | 谁读 |
|---|---|---|---|
| `SERIAL_PORT` | COM6 | 启动时自动打开的串口 | Web 服务 |
| `SERIAL_BAUD` | 115200 | 波特率 | Web 服务 |
| `HOST` | 127.0.0.1 | Web 服务监听地址 | Web 服务 |
| `HTTP_PORT` | 8080 | Web 服务监听端口 | Web 服务 |
| `LOG_MAX_LINES` | 10000 | 日志缓冲容量 | Web 服务 |
| `LOG_LEVEL` | INFO | 日志级别 | Web 服务 |
| `IDF_PROJECT_DIR` | (空) | ESP-IDF 项目根目录 | Web 服务 |
| `IDF_EXPORT_SCRIPT` | (空) | export.ps1 绝对路径 | Web 服务 |
| `IDF_BOARDS_DIR` | boards | boards 目录（相对/绝对） | Web 服务 |
| `IDF_BOARD` | lckfb_szpi_esp32s3 | 默认板型 | Web 服务 |
| `BRIDGE_HOST` | 127.0.0.1 | MCP 连接的 bridge 地址 | MCP 服务 |
| `BRIDGE_PORT` | 8080 | MCP 连接的 bridge 端口 | MCP 服务 |

### 7.2 agent 端 MCP 配置

在 agent 的 MCP 配置文件（WorkBuddy: `~/.workbuddy/mcp.json`）注册：

```json
{
  "mcpServers": {
    "esp32-bridge": {
      "command": "D:\\code\\espclaw\\espbridgetool\\.venv\\Scripts\\python.exe",
      "args": ["D:\\code\\espclaw\\espbridgetool\\mcp_server.py"]
    }
  }
}
```

`BRIDGE_HOST`/`BRIDGE_PORT` 不在配置里时，mcp_server 会读项目 `.env`。若 bridge 不在默认端口，可在 args 加 `--port <N>`。

---

## 8. 模块化拆分路线图

当前 `serial_bridge.py` 是单文件路由（~820 行），随功能增长应拆成包。建议路线：

### 阶段 1：路由分文件（低风险）

```
serial_bridge/
  __init__.py          # create_app()
  app.py               # FastAPI 实例 + lifespan + 静态挂载
  routes/
    serial.py          # /api/serial/*, /api/send, /api/send-and-collect
    log.py             # /api/log/*
    idf.py             # /api/config, /api/build, /api/flash, /api/boards/* ...
    quick_commands.py  # /api/quick-commands/*
    stats.py           # /api/stats/reset, /api/status
    ws.py              # /ws/log, /ws/terminal
  deps.py              # 共享依赖注入（SERIAL, BUFFER, IDF, WS_CLIENTS, STATS）
  config.py            # .env 读写、配置加载
```

拆分时保持「路由文件只做请求处理，业务逻辑留在 SerialManager/LogBuffer/IdfTool」的边界。

### 阶段 2：状态对象化（中风险）

把全局变量（`SERIAL`、`BUFFER`、`IDF`、`WS_CLIENTS`、`STATS`、`QUICK_COMMANDS`）收进一个 `AppState` 类，通过 FastAPI 依赖注入分发，消除全局可变状态。

### 阶段 3：多设备支持（高风险）

`SerialManager` 改为注册多个串口实例（按 device 名索引），路由加 `/api/serial/{port}/...` 路径参数。MCP 工具加 `port` 参数。这是最大改动，需同步更新本文档。

### 拆分原则（始终遵守）

1. **MCP server 永远是 HTTP 代理** — 不能 import SerialManager/LogBuffer/IdfTool
2. **串口句柄永远只在 Web 服务进程** — 不能让 MCP server 直接开串口
3. **新增 REST 接口时同步加 MCP 工具** — 保持两类操作者能力对等
4. **状态归属变更必须更新本文档第 5 节** — 这是模块化的契约

---

## 9. 相关文档

- [远程接入与多设备架构设计](./remote-access-design.md) — HTTP transport、两层鉴权、多设备路由、ACL 的完整设计稿（v1.0 设计阶段）
- [接口维护文档](./api-reference.md) — REST / WebSocket / MCP 工具的完整清单与字段定义
- [板型配置指南](./lckfb-szpi-esp32s3-configuration-guide.md) — lckfb_szpi_esp32s3 板型的 menuconfig 配置
- `serial-bridge-design.md` — 早期设计文档（v1.0，不含 MCP 层），保留作历史参考
