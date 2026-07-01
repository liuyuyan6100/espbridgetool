# Serial Bridge — ESP32 串口代理服务设计方案

> **版本**：v1.0
> **日期**：2026-07-01
> **状态**：规划中

## 1. 背景与目标

### 1.1 问题痛点

在 ESP-Claw 固件开发过程中遇到以下串口调试痛点：

| 痛点 | 描述 |
|---|---|
| **串口冲突** | `idf.py monitor` 和 `idf.py flash` 争抢同一个 COM 端口，Ctrl+] 退出后端口仍可能被占用 |
| **设备挂死** | 固件异常导致 USB CDC 串口卡死，需要拔插 USB 或进入下载模式才能恢复 |
| **人工干预难** | Agent 在终端操作串口时，用户无法同时观察日志或手动发送命令 |
| **调试效率低** | 烧录 → 观察 → 改代码 → 重新烧录的循环需要频繁切换终端窗口 |

### 1.2 设计目标

- **串口独占管理**：单一服务持有串口，消除多进程争抢
- **Agent 可控**：AI agent 通过 REST API 发送命令、读取日志、触发烧录
- **Web 可观察**：用户通过浏览器实时查看日志，必要时手动干预
- **完整开发工具**：集成 idf.py build/flash/monitor，日志分析与错误高亮

## 2. 总体架构

```
┌─────────────────┐     HTTP REST      ┌──────────────────────┐      Serial       ┌───────────┐
│   AI Agent      │ ◄────────────────► │   Serial Bridge      │ ◄───────────────► │   ESP32   │
│  (Trae IDE)     │   /api/send        │   (Python 后端)      │    pyserial       │   COM6    │
│                 │   /api/flash       │                      │                    └───────────┘
│                 │   /api/log/history │  ┌────────────────┐  │
└─────────────────┘   /api/status      │  │  串口管理器    │  │
                                      │  │  - 独占串口    │  │
┌─────────────────┐   WebSocket       │  │  - 烧录释放    │  │
│   Web 浏览器    │ ◄────────────────► │  │  - 自动重连    │  │
│                 │   /ws/log          │  └────────────────┘  │
│  ┌───────────┐  │   实时日志流        │  ┌────────────────┐  │
│  │ xterm.js  │  │                    │  │  日志缓冲器    │  │
│  │ 终端模式  │  │                    │  │  - 环形缓冲    │  │
│  └───────────┘  │                    │  │  - 历史回看    │  │
│  ┌───────────┐  │                    │  │  - 关键字过滤  │  │
│  │ 日志面板  │  │                    │  └────────────────┘  │
│  │ + 命令框  │  │                    │  ┌────────────────┐  │
│  └───────────┘  │                    │  │  idf.py 集成   │  │
│                 │                    │  │  - build       │  │
└─────────────────┘                    │  │  - flash       │  │
                                      │  │  - monitor     │  │
                                      │  └────────────────┘  │
                                      └──────────────────────┘
```

## 3. 技术栈

| 层级 | 技术选型 | 说明 |
|---|---|---|
| **后端框架** | Python + FastAPI | 异步支持好，自动生成 OpenAPI 文档 |
| **串口库** | pyserial | 成熟稳定，跨平台 |
| **WebSocket** | FastAPI 内置 | 无需额外依赖 |
| **前端终端** | xterm.js (CDN) | 浏览器端终端模拟器，支持 ANSI 颜色码 |
| **前端 UI** | 原生 HTML/CSS/JS | 无构建工具，单文件部署 |
| **进程管理** | subprocess | 调用 idf.py 命令 |
| **运行环境** | Python 3.8+ | 可复用 ESP-IDF venv 或独立 venv |

## 4. 功能模块

### 4.1 串口管理器（Serial Manager）

```python
class SerialManager:
    """独占管理串口连接，支持烧录时临时释放"""

    def open(self, port: str, baud: int) -> bool
    def close(self) -> None
    def send(self, data: bytes) -> int
    def send_line(self, line: str) -> int
    def read_loop(self) -> None  # 后台线程持续读取
    def acquire_for_flash(self) -> ContextManager  # 上下文管理器：释放串口 → 烧录 → 重新打开
```

**关键行为**：
- 启动时自动打开串口，后台线程持续读取数据
- 烧录时通过上下文管理器临时关闭串口，烧录完成后自动重连
- 串口异常断开时自动重试连接（指数退避）

### 4.2 日志缓冲器（Log Buffer）

```python
class LogBuffer:
    """环形缓冲区，保存最近 N 行日志"""

    def __init__(self, max_lines: int = 10000)
    def append(self, line: str) -> None
    def get_history(self, last_n: int = 100) -> List[str]
    def get_filtered(self, keyword: str) -> List[str]
    def clear(self) -> None
```

**关键行为**：
- 环形缓冲，默认保存最近 10000 行
- 新 WebSocket 客户端连接时先推送历史日志
- 支持关键字过滤（如只看 `app_emote` 或 `error`）

### 4.3 idf.py 集成（Build Tool）

```python
class IdfTool:
    """封装 idf.py 命令，捕获输出推送到日志流"""

    def build(self, board: str = None) -> Tuple[bool, str]
    def flash(self, port: str, board: str = None) -> Tuple[bool, str]
    def monitor(self, port: str) -> None  # 实际上由 SerialManager 接管
    def fullclean(self) -> Tuple[bool, str]
    def bmgr(self, board: str) -> Tuple[bool, str]
```

**关键行为**：
- 调用 idf.py 前先 `source export.ps1`（Windows: `. 'C:\esp\v5.5.4\esp-idf\export.ps1'`）
- 通过 `subprocess.Popen` 实时捕获 stdout/stderr
- 烧录时自动通知 SerialManager 释放串口
- 命令输出实时推送到 WebSocket（Web 端可见烧录进度）

### 4.4 REST API

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/status` | GET | 获取串口状态（已连接/断开/烧录中）、端口名、波特率 |
| `/api/serial/open` | POST | 打开串口 `{port, baud}` |
| `/api/serial/close` | POST | 关闭串口 |
| `/api/send` | POST | 发送数据 `{cmd: "help\n"}` |
| `/api/log/history` | GET | 获取历史日志 `?lines=100&filter=app_emote` |
| `/api/log/clear` | POST | 清空日志缓冲 |
| `/api/build` | POST | 触发编译 `{board: "lckfb_szpi_esp32s3"}` |
| `/api/flash` | POST | 触发烧录 `{port: "COM6", board: "lckfb_szpi_esp32s3"}` |
| `/api/clean` | POST | 触发 fullclean |
| `/api/bmgr` | POST | 触发 bmgr `{board: "lckfb_szpi_esp32s3"}` |
| `/ws/log` | WS | WebSocket 实时日志流 |

### 4.5 Web 前端

**双模式切换**：

#### 终端模式（xterm.js）
- 全屏终端，和 `idf.py monitor` 体验一致
- 支持 ANSI 颜色码（ESP-IDF 日志的彩色标签正常显示）
- 键盘直接输入命令
- 适合：交互式调试、REPL 操作

#### 日志面板模式
- 上方：滚动日志区，支持暂停滚动、关键字过滤、错误高亮
- 下方：命令输入框（支持历史命令，上下箭头切换）
- 右侧：快捷操作按钮（Build / Flash / Clean / BMGR / Clear Log）
- 底部状态栏：串口状态、波特率、日志行数、连接状态
- 适合：长时间观察、错误排查、构建监控

**错误高亮规则**：
- `E (xxx)` 红色背景
- `W (xxx)` 黄色背景
- `I (xxx)` 默认色
- 烧录进度条（解析 esptool 输出）

## 5. 目录结构

```
D:\tools\serial_bridge\
├── serial_bridge.py          # 主程序入口
├── serial_manager.py         # 串口管理器
├── log_buffer.py             # 日志环形缓冲
├── idf_tool.py               # idf.py 命令封装
├── requirements.txt          # Python 依赖
├── start.bat                 # Windows 启动脚本
├── README.md                 # 使用说明
└── static/
    ├── index.html            # Web 前端单页
    ├── style.css             # 样式
    └── app.js                # 前端逻辑
```

## 6. 启动与使用

### 6.1 首次安装

```powershell
# 创建独立 venv
python -m venv D:\tools\serial_bridge\venv

# 激活 venv
D:\tools\serial_bridge\venv\Scripts\Activate.ps1

# 安装依赖
pip install fastapi uvicorn pyserial
```

### 6.2 日常启动

```powershell
# 方式 1：直接运行
D:\tools\serial_bridge\venv\Scripts\python.exe D:\tools\serial_bridge\serial_bridge.py --port COM6 --baud 115200

# 方式 2：使用启动脚本
D:\tools\serial_bridge\start.bat
```

### 6.3 使用流程

1. **启动服务**：运行 `start.bat`，服务监听 `http://127.0.0.1:8080`
2. **打开 Web**：浏览器访问 `http://localhost:8080`
3. **Agent 操作**：
   - 发命令：`POST http://127.0.0.1:8080/api/send {"cmd": "help\n"}`
   - 触发烧录：`POST http://127.0.0.1:8080/api/flash {"port": "COM6"}`
   - 读日志：`GET http://127.0.0.1:8080/api/log/history?lines=50&filter=app_emote`
4. **人工干预**：在 Web 终端直接输入命令，或点击快捷按钮

## 7. 关键设计决策

### 7.1 烧录时串口释放

```
用户/Agent 调用 /api/flash
        │
        ▼
SerialManager 关闭串口 COM6
        │
        ▼
IdfTool 调用 idf.py -p COM6 flash
        │
        ▼
烧录完成（成功/失败）
        │
        ▼
SerialManager 重新打开 COM6
        │
        ▼
WebSocket 推送 "串口已重新连接"
```

### 7.2 多客户端观察

- 多个 WebSocket 连接同时订阅日志流
- 每个客户端独立维护过滤条件
- 命令发送通过 REST API 串行化（避免并发冲突）

### 7.3 日志分析增强

| 功能 | 实现 |
|---|---|
| 错误自动高亮 | 正则匹配 `^E \(\d+\)` |
| 烧录进度 | 解析 esptool 的 `Writing at 0x...` 输出 |
| 内存信息 | 匹配 `heap` 关键字并高亮 |
| WiFi 状态 | 匹配 `wifi:` 标签 |
| 自定义过滤器 | Web 端输入关键字，实时过滤显示 |

## 8. 安全性

- 服务仅监听 `127.0.0.1`，不暴露到局域网
- 无需认证（本地开发工具）
- 烧录命令参数校验（防止命令注入）

## 9. 后续扩展

| 扩展方向 | 说明 |
|---|---|
| 多设备支持 | 同时管理多个串口（如 ESP32-S3 + ESP32-C6） |
| 日志持久化 | 保存到文件，支持回放 |
| 自动化测试 | Agent 可编写测试脚本，通过 API 驱动设备 |
| 固件性能分析 | 解析日志中的时间戳，生成性能报告 |
| OTA 支持 | 集成 OTA 烧录接口 |
