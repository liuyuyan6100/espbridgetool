# ESP32 Serial Bridge — 接口维护文档

> **版本**：v2.0
> **最后更新**：2026-07-04
> **维护说明**：本文档是所有对外接口的权威清单。新增/修改/废弃接口时，
> 必须同步更新本文档对应小节，并在文末「变更记录」追加一行。
> 接口分三类：REST API（Web 服务）、WebSocket（Web 服务）、MCP 工具（MCP 服务）。

---

## 0. 通用约定

### 0.1 Base URL

| 接口类型 | Base URL |
|---|---|
| REST / WebSocket | `http://127.0.0.1:8080`（可由 `.env` 的 `HOST`/`HTTP_PORT` 配置） |
| MCP | stdio（由 agent spawn，无 URL） |

### 0.2 请求与响应格式

- REST 请求体：`application/json`（除 WebSocket 外不接受 form-data）
- REST 响应体：`application/json`
- 字符编码：UTF-8
- 成功响应：各接口自定义，通常含 `ok: true` 或业务字段
- 错误响应：HTTP 4xx/5xx，body 为 `{"ok": false, "error": "<可读说明>"}`

### 0.3 状态码约定

| 状态码 | 含义 |
|---|---|
| 200 | 成功（即使业务逻辑失败也返回 200，看 body 的 `ok` 字段） |
| 400 | 请求参数错误 / 业务前置条件不满足（如串口未打开） |
| 404 | 路由不存在 |
| 500 | 服务内部异常 |

### 0.4 命名规范（新增接口时遵守）

- REST 路径：`/api/<模块>/<动作>`，如 `/api/serial/open`、`/api/log/history`
- MCP 工具名：`snake_case` 动词短语，如 `send_and_collect`、`get_logs_since`
- MCP 工具与 REST 接口尽量一一对应，便于维护和排查

---

## 1. REST API — 串口管理

### 1.1 `GET /api/status`

获取服务整体状态快照。

**响应**：
```json
{
  "status": "connected",
  "port": "COM6",
  "baud": 115200,
  "log_lines": 29,
  "ws_clients": 1,
  "available_ports": [
    {"device": "COM6", "description": "...", "hwid": "..."}
  ],
  "stats": {"tx_bytes": 128, "rx_bytes": 4096}
}
```

| 字段 | 说明 |
|---|---|
| `status` | `"connected"` / `"disconnected"` |
| `available_ports` | 系统所有可用串口（`SerialManager.list_ports()` 返回） |

---

### 1.2 `POST /api/serial/open`

打开指定串口。

**请求**：
```json
{"port": "COM6", "baud": 115200}
```

**响应**：`{"ok": true, "port": "COM6", "baud": 115200}`
**错误**：缺 `port` → 400 `{"ok": false, "error": "缺少 port 参数"}`

---

### 1.3 `POST /api/serial/close`

关闭当前串口。

**响应**：`{"ok": true}`

---

## 2. REST API — 命令发送

### 2.1 `POST /api/send`

发送数据到串口，**不等待响应**（异步发出即返回）。

**请求**：
```json
{"cmd": "help", "hex": false}
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `cmd` | string | (必填) | 命令文本。文本模式自动追加 `\r\n` |
| `hex` | bool | false | true 时 `cmd` 视为十六进制字符串（如 `"4154"`），发原始字节 |

**响应**：`{"ok": true, "sent_bytes": 6, "cmd": "help"}`
**错误**：缺 `cmd` / 串口未开 / HEX 格式错误 → 400

---

### 2.2 `POST /api/send-and-collect` ⭐ 核心

发送命令并等待若干秒收集设备响应。**MCP agent 最常用的接口**。

**请求**：
```json
{"cmd": "AT+RST", "wait": 2.0, "hex": false}
```

| 参数 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `cmd` | string | (必填) | — | 命令文本（自动追加 `\r\n`） |
| `wait` | float | 2.0 | 0.1 ~ 10.0 | 发送后等待收集的秒数，超出范围会被 clamp |
| `hex` | bool | false | — | HEX 模式发送 |

**响应**：
```json
{
  "ok": true,
  "sent_bytes": 8,
  "cmd": "AT+RST",
  "wait_seconds": 2.0,
  "before_seq": 100,
  "after_seq": 108,
  "collected_lines": ["OK", "", "ready"]
}
```

| 字段 | 说明 |
|---|---|
| `before_seq` | 发命令前的日志序列号 |
| `after_seq` | 等待结束后的序列号 |
| `collected_lines` | 等待期间新增的日志行 —— **这就是设备的"响应"** |

**错误**：缺 `cmd` / 串口未开 → 400

**实现要点**：依赖 `LogBuffer.get_after_seq(before_seq)`，即使环形缓冲满了丢早期行，序列号仍单调递增，不会取错。

---

## 3. REST API — 日志

### 3.1 `GET /api/log/history`

获取历史日志（最近 N 行，可过滤）。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `lines` | int | 100 | 返回行数 |
| `filter` | string | (无) | 关键字过滤（大小写不敏感） |

**响应**：`{"lines": ["...", "..."]}`

---

### 3.2 `GET /api/log/since`

增量获取日志：返回序列号严格大于 `seq` 的所有行。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `seq` | int | 0 | 上次获取的 `after_seq`，首次传 0 取全部 |

**响应**：
```json
{"ok": true, "lines": ["..."], "before_seq": 100, "after_seq": 108}
```

---

### 3.3 `GET /api/log/last-seq`

获取当前最新序列号和总行数。用于开始监控前拿到基线。

**响应**：`{"ok": true, "last_seq": 108, "count": 29}`

---

### 3.4 `POST /api/log/clear`

清空日志缓冲。**谨慎**：不可恢复。

**响应**：`{"ok": true}`

---

## 4. REST API — 统计

### 4.1 `POST /api/stats/reset`

重置收发字节统计（`tx_bytes`/`rx_bytes` 归零）。

**响应**：`{"ok": true, "stats": {"tx_bytes": 0, "rx_bytes": 0}}`

---

## 5. REST API — 快捷命令

快捷命令存在内存中（`QUICK_COMMANDS` 全局列表），**未持久化**，服务重启后丢失。

### 5.1 `GET /api/quick-commands`

获取快捷命令列表。

**响应**：
```json
{"commands": [{"name": "复位", "cmd": "AT+RST", "hex": false}]}
```

---

### 5.2 `POST /api/quick-commands`

添加快捷命令。

**请求**：`{"name": "复位", "cmd": "AT+RST", "hex": false}`
**响应**：`{"ok": true, "commands": [...]}`

---

### 5.3 `DELETE /api/quick-commands/{index}`

按索引删除快捷命令。

**响应**：`{"ok": true, "commands": [...]}` / 索引越界 → 400

---

## 6. REST API — ESP-IDF 配置

### 6.1 `GET /api/config`

获取当前 IDF 配置。

**响应**：
```json
{
  "ok": true,
  "config": {
    "project_dir": "D:\\code\\espclaw\\esp-claw\\application\\edge_agent",
    "export_script": "C:\\esp\\v5.5.4\\esp-idf\\export.ps1",
    "boards_dir": "boards",
    "board": "lckfb_szpi_esp32s3",
    "idf_initialized": true
  }
}
```

---

### 6.2 `POST /api/config`

更新 IDF 配置（运行时生效 + 持久化到 `.env`）。所有字段可选，只传需改的。

**请求**：
```json
{
  "IDF_PROJECT_DIR": "D:\\...",
  "IDF_EXPORT_SCRIPT": "C:\\esp\\v5.5.4\\esp-idf\\export.ps1",
  "IDF_BOARDS_DIR": "boards",
  "IDF_BOARD": "lckfb_szpi_esp32s3"
}
```

**响应**：`{"ok": true, "message": "配置已更新并生效"}`

---

## 7. REST API — ESP-IDF 扫描

### 7.1 `GET /api/idf-versions`

扫描系统已安装的 ESP-IDF 版本（默认扫 `C:\esp\`，可由 `IDF_SCAN_ROOT` 环境变量覆盖）。

**响应**：
```json
{
  "ok": true,
  "versions": [
    {"version": "v5.5.4", "export_script": "C:\\esp\\v5.5.4\\esp-idf\\export.ps1"}
  ]
}
```

---

### 7.2 `GET /api/idf-projects`

扫描常见目录（`D:\code\espclaw`、`D:\code`）下含 `CMakeLists.txt` + `boards/` 的项目，最多 3 层深度。

**响应**：
```json
{
  "ok": true,
  "projects": [
    {"path": "D:\\code\\espclaw\\esp-claw\\application\\edge_agent", "name": "esp-claw\\application\\edge_agent"}
  ]
}
```

---

## 8. REST API — ESP-IDF 构建操作

> 这组接口都是阻塞调用——HTTP 请求会等到 idf.py 命令执行完才返回。
> 输出同时实时推送到日志流（`/ws/log`），Web UI 可见进度。MCP 端对应工具设了 600s 超时。

### 8.1 `GET /api/boards`

列出当前项目支持的所有板型（`idf.py bmgr -l`）。IDF 未初始化但有项目目录时自动初始化。

**响应**：
```json
{"ok": true, "boards": ["lckfb_szpi_esp32s3", "..."], "current": "lckfb_szpi_esp32s3"}
```

---

### 8.2 `POST /api/boards/select`

选择目标板型（`idf.py bmgr -b <board>`）。

**请求**：`{"board": "lckfb_szpi_esp32s3"}`
**响应**：`{"ok": true, "board": "...", "output": "选择成功"}`

---

### 8.3 `POST /api/build`

编译固件（`idf.py build`）。

**请求**：`{"board": "lckfb_szpi_esp32s3"}` （`board` 可选，传了会先 select）

**响应**：`{"ok": true, "output": "编译完成"}` / 失败时 `output` 为错误输出前 500 字符

---

### 8.4 `POST /api/flash`

烧录固件（`idf.py -p <port> flash`）。**自动管理串口释放与重连**。

**请求**：`{"port": "COM6", "board": "lckfb_szpi_esp32s3"}` （均可选，默认用当前串口/板型）

**响应**：`{"ok": true, "output": "烧录完成"}`

**实现**：用 `SERIAL.acquire_for_flash()` 上下文管理器，烧录前关串口、烧录后重连。

---

### 8.5 `POST /api/clean`

清理编译产物（`idf.py fullclean`）。

**响应**：`{"ok": true, "output": "..."}`

---

### 8.6 `POST /api/bmgr`

触发 bmgr（`idf.py bmgr -c <boards_dir> -b <board>`）。

**请求**：`{"board": "lckfb_szpi_esp32s3"}`
**响应**：`{"ok": true, "output": "bmgr 完成"}`

---

### 8.7 `POST /api/menuconfig`

触发 menuconfig。⚠️ 需终端环境，Web 下可能不工作。

**响应**：`{"ok": true, "output": "menuconfig 退出"}`

---

## 9. WebSocket 接口

### 9.1 `/ws/log` — 实时日志流

连接后立即推送最近 200 行历史日志，之后实时推送所有新日志。

**客户端→服务端消息**（可选）：
- `/send <cmd>` — 发送命令到串口（等价于 `POST /api/send`）

**服务端→客户端消息**：纯文本日志行

**断开处理**：客户端断开时从 `WS_CLIENTS` 列表移除，不影响其他客户端。

---

### 9.2 `/ws/terminal?mode=serial|shell` — 终端

双模式终端，由 `mode` 查询参数切换：

| mode | 用途 | 实现 |
|---|---|---|
| `serial`（默认） | 串口终端，直连 ESP32 | 注册 SerialManager 回调，bytes 双向透传 |
| `shell` | Shell 终端，执行 idf.py 等 | pywinpty PTY + powershell.exe，自动 source export.ps1 |

**serial 模式消息**：
- 客户端→服务端：`bytes` 或 `text`，直接发到串口
- 服务端→客户端：`bytes`，串口原始数据

**shell 模式**：基于 pywinpty PTY，初始化时设置 UTF-8 编码、清除 `MSYSTEM`、dot-source `export.ps1`。命令间需 0.5s 延迟确保 PowerShell 逐条执行。

---

## 10. MCP 工具清单

MCP server（`mcp_server.py`）暴露 19 个工具，按模块分组。每个工具内部调用对应的 REST 接口。

### 10.1 串口管理（6 个）

| 工具 | 参数 | 对应 REST | 用途 |
|---|---|---|---|
| `list_serial_ports` | (无) | `GET /api/status` 取 `available_ports` | 列出可用串口 |
| `get_status` | (无) | `GET /api/status` | 完整状态快照 |
| `open_serial` | `port: str`, `baud: int=115200` | `POST /api/serial/open` | 打开串口 |
| `close_serial` | (无) | `POST /api/serial/close` | 关闭串口 |
| `send_command` | `cmd: str`, `hex_mode: bool=False` | `POST /api/send` | 发命令，不等响应 |
| `send_and_collect` ⭐ | `cmd: str`, `wait_seconds: float=2.0`, `hex_mode: bool=False` | `POST /api/send-and-collect` | **发命令+收集设备响应** |

### 10.2 日志（4 个）

| 工具 | 参数 | 对应 REST | 用途 |
|---|---|---|---|
| `get_logs` | `lines: int=100`, `keyword: str=None` | `GET /api/log/history` | 历史日志，可过滤 |
| `get_logs_since` | `seq: int=0` | `GET /api/log/since` | 增量取日志 |
| `get_last_seq` | (无) | `GET /api/log/last-seq` | 取当前序列号 |
| `clear_logs` | (无) | `POST /api/log/clear` | 清空日志 |

### 10.3 ESP-IDF 配置与扫描（5 个）

| 工具 | 参数 | 对应 REST | 用途 |
|---|---|---|---|
| `get_idf_config` | (无) | `GET /api/config` | 获取 IDF 配置 |
| `set_idf_config` | `project_dir/export_script/boards_dir/board` (均可选) | `POST /api/config` | 更新配置（持久化） |
| `list_idf_versions` | (无) | `GET /api/idf-versions` | 扫描已装 IDF 版本 |
| `list_idf_projects` | (无) | `GET /api/idf-projects` | 扫描可用项目 |
| `list_boards` | (无) | `GET /api/boards` | 列出板型 |

### 10.4 ESP-IDF 构建操作（4 个）

| 工具 | 参数 | 对应 REST | 用途 |
|---|---|---|---|
| `select_board` | `board: str` | `POST /api/boards/select` | 选择板型 |
| `build` | `board: str=None` | `POST /api/build` | 编译（600s 超时） |
| `flash` | `port: str=None`, `board: str=None` | `POST /api/flash` | 烧录（300s 超时） |
| `clean_build` | (无) | `POST /api/clean` | 清理（300s 超时） |

---

## 11. MCP 资源清单

3 个只读资源，agent 可主动拉取快照：

| 资源 URI | 对应 REST | 用途 |
|---|---|---|
| `esp32://status` | `GET /api/status` | 当前状态快照 |
| `esp32://logs/recent` | `GET /api/log/history?lines=50` | 最近 50 行日志 |
| `esp32://config` | `GET /api/config` | 当前 IDF 配置 |

---

## 12. 错误处理约定

### 12.1 Web 服务（REST）

业务错误返回 HTTP 400 + `{"ok": false, "error": "..."}`，不抛未捕获异常。典型错误：

| 场景 | 错误信息 |
|---|---|
| 缺必填参数 | `"缺少 cmd 参数"` / `"缺少 port 参数"` |
| 串口未打开就发命令 | `"串口未打开"`（send_and_collect 专属提示） |
| IDF 未初始化就构建 | `"IDF 工具未初始化，请在配置中设置项目目录"` |
| HEX 格式错误 | `"HEX 格式错误: <细节>"` |

### 12.2 MCP 服务

`_call()` 捕获所有 HTTP 异常，转成 `{"ok": false, "error": "..."}` 返回给 agent，**不抛异常**。特殊场景：

| 场景 | 错误信息 |
|---|---|
| bridge 未启动 | `"无法连接 Serial Bridge (http://...)。请先运行 serial_bridge.py（双击 start.bat）启动桥接服务。"` |
| 请求超时 | `"请求超时（30s）。长耗时操作（build/flash）请确认是否正常执行。"` |
| 非 JSON 响应 | `"非 JSON 响应 (HTTP 502): <正文前 300 字>"` |

---

## 13. 接口维护检查清单

新增或修改接口时，按此清单逐项确认：

### 新增 REST 接口
- [ ] 在本文档对应小节加表格（路径、方法、请求参数、响应示例、错误情况）
- [ ] 路径符合 `/api/<模块>/<动作>` 规范
- [ ] 业务错误返回 400 + `{"ok": false, "error": ...}`，不抛异常
- [ ] 若是 agent 会用到的能力 → 同步加 MCP 工具（见下）

### 新增 MCP 工具
- [ ] 在第 10 节表格登记（工具名、参数、对应 REST、用途）
- [ ] 工具名用 `snake_case`，写清楚 docstring（agent 靠它判断何时调用）
- [ ] 通过 `_call()` 调用 REST，不要直接操作设备
- [ ] 长耗时操作（build/flash/clean）在 `_call` 里设足够 timeout
- [ ] 若是只读快照 → 考虑同时加 `@mcp.resource`

### 修改现有接口
- [ ] 向后兼容（新增字段OK，删除/改名字段需评估 agent 影响）
- [ ] 不兼容改动 → 走新路径/新工具名，旧的下线标注废弃日期
- [ ] 更新本文档 + 文末变更记录

### 废弃接口
- [ ] 在文档对应行标注 `⚠️ 已废弃（计划 YYYY-MM-DD 移除）`
- [ ] 保留至少一个版本周期再删

---

## 14. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-07-01 | v1.0 | 初版 REST API（串口/日志/构建）+ WebSocket |
| 2026-07-04 | v2.0 | 新增 MCP 智能体接入层：19 工具 + 3 资源；REST 新增 `/api/log/since`、`/api/log/last-seq`、`/api/send-and-collect`；LogBuffer 加序列号机制 |
| 2026-07-04 | v2.1 | 新增远程接入设计稿（[remote-access-design.md](./remote-access-design.md)），规划 HTTP transport + 两层鉴权 + 多设备路由 + ACL，尚未实现 |
| 2026-07-04 | v2.2 | **阶段 1 已实现**：mcp_server 支持 `--transport http`（streamable-http + Bearer token 中间件）；serial_bridge 加 `BRIDGE_AUTH_TOKEN` 可选校验中间件（`X-Bridge-Token` 头）；.env 新增 `MCP_TRANSPORT`/`MCP_HTTP_HOST`/`MCP_HTTP_PORT`/`MCP_AUTH_TOKENS`/`BRIDGE_AUTH_TOKEN` |

---

## 15. 相关文档

- [架构文档](./architecture.md) — 进程模型、模块划分、数据流、状态归属、拆分路线图
- [远程接入设计](./remote-access-design.md) — HTTP transport、鉴权、多设备路由、ACL（设计稿）
- [板型配置指南](./lckfb-szpi-esp32s3-configuration-guide.md) — lckfb_szpi_esp32s3 的 menuconfig 配置
