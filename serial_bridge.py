#!/usr/bin/env python3
"""Serial Bridge — ESP32 串口代理服务主程序入口

启动:
    python serial_bridge.py          # 读取 .env 配置启动
    python serial_bridge.py --port COM6 --baud 115200   # 命令行覆盖
"""

import argparse
import asyncio
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from typing import List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from serial_manager import SerialManager
from log_buffer import LogBuffer
from log_sink import LogSink
from idf_tool import IdfTool

# ---- 加载 .env 配置 ----

load_dotenv()

# ---- 日志配置 ----

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("serial_bridge")

# ---- 全局状态 ----

SERIAL = SerialManager(on_data=None)
BUFFER = LogBuffer(max_lines=int(os.getenv("LOG_MAX_LINES", "10000")))
IDF: Optional[IdfTool] = None

# 日志落盘（会话级文件）。LOG_SINK_ENABLED=false 可关闭。
# 默认目录 logs/（相对本文件所在目录），可经 LOG_SINK_DIR 用绝对路径覆盖。
_SINK_DIR = os.getenv("LOG_SINK_DIR", os.path.join(os.path.dirname(__file__), "logs"))
SINK = LogSink(
    sink_dir=_SINK_DIR,
    enabled=os.getenv("LOG_SINK_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
)

# WebSocket 连接池
WS_CLIENTS: List[WebSocket] = []

# 收发统计
STATS = {"tx_bytes": 0, "rx_bytes": 0}

# 快捷命令列表（可持久化到 .env 或本地文件）
QUICK_COMMANDS: List[dict] = []

# 状态常量
STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTED = "connected"
STATUS_FLASHING = "flashing"

# 主事件循环引用（在 lifespan 中捕获，供跨线程调度使用）
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _on_serial_data(data: bytes):
    """串口数据回调：写入日志缓冲并广播到所有 WebSocket 客户端"""
    STATS["rx_bytes"] += len(data)
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = repr(data)

    BUFFER.append(text, raw=data)
    SINK.write(text)
    _broadcast_task(text)


def _broadcast_task(text: str):
    """将广播任务安全地调度到主事件循环（线程安全版）"""
    global MAIN_LOOP
    if MAIN_LOOP and MAIN_LOOP.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_broadcast(text), MAIN_LOOP)
        except Exception:
            pass


async def _broadcast(text: str):
    """向所有连接的 WebSocket 广播日志行"""
    dead = []
    for ws in WS_CLIENTS:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in WS_CLIENTS:
            WS_CLIENTS.remove(ws)


def _idf_output_callback(line: str):
    """idf.py 输出回调：推送到日志缓冲 + WebSocket"""
    BUFFER.append(line)
    SINK.write(f"[idf.py] {line}")
    _broadcast_task(f"[idf.py] {line}")


# 注册串口数据回调（在 SERIAL 创建后立即注册）
SERIAL.add_callback(_on_serial_data)


# ---- FastAPI App ----

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    logger.info("Serial Bridge 服务启动")
    # 开启日志落盘会话（写环境元数据到文件头）
    if SINK.enabled:
        auto_port = os.getenv("SERIAL_PORT", "")
        auto_baud = int(os.getenv("SERIAL_BAUD", "115200"))
        sink_path = SINK.open_session(meta={
            "串口": auto_port or "(未配置)",
            "波特率": auto_baud,
            "IDF 项目目录": os.getenv("IDF_PROJECT_DIR", "(未配置)"),
            "IDF 板型": os.getenv("IDF_BOARD", "(未配置)"),
            "日志缓冲行数": os.getenv("LOG_MAX_LINES", "10000"),
        })
        if sink_path:
            logger.info(f"日志落盘已启用: {sink_path}")
    # 自动打开串口（如果配置了）
    auto_port = os.getenv("SERIAL_PORT", "")
    auto_baud = int(os.getenv("SERIAL_BAUD", "115200"))
    if auto_port:
        ok = SERIAL.open(auto_port, auto_baud)
        if ok:
            logger.info(f"串口已自动打开: {auto_port} @ {auto_baud}")
        else:
            logger.warning(f"串口 {auto_port} 打开失败，仅启动 Web 服务")
    yield
    if SERIAL.is_open:
        SERIAL.close()
    SINK.close()
    logger.info("Serial Bridge 服务关闭")


app = FastAPI(
    title="Serial Bridge",
    description="ESP32 串口代理服务 — AI Agent 可通过 REST API 控制串口",
    version="1.1.0",
    lifespan=lifespan,
)

# 挂载静态文件目录（CSS/JS 等）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---- 内层 token 鉴权中间件（可选）----
# 配置 BRIDGE_AUTH_TOKEN 后，除 / 和 /static 外的 /api/* 和 /ws/* 请求
# 必须带 X-Bridge-Token 头。防止 bridge 被未授权直连。
# 留空（默认）则不校验，保持向后兼容。
BRIDGE_AUTH_TOKEN = os.getenv("BRIDGE_AUTH_TOKEN", "")


@app.middleware("http")
async def bridge_auth_middleware(request, call_next):
    """校验 X-Bridge-Token 头（仅当 BRIDGE_AUTH_TOKEN 已配置时生效）"""
    if BRIDGE_AUTH_TOKEN:
        path = request.url.path
        # 放行：根路径（Web UI 首页）、静态资源、健康检查
        if path in ("/", "/health") or path.startswith("/static"):
            return await call_next(request)
        token = request.headers.get("X-Bridge-Token", "")
        if token != BRIDGE_AUTH_TOKEN:
            return JSONResponse(
                {"ok": False, "error": "无效或缺失的 X-Bridge-Token"},
                status_code=401,
            )
    return await call_next(request)


# ---- REST API ----

@app.get("/api/status")
async def api_status():
    """获取串口状态"""
    return {
        "status": STATUS_CONNECTED if SERIAL.is_open else STATUS_DISCONNECTED,
        "port": SERIAL.port,
        "baud": SERIAL.baud,
        "log_lines": BUFFER.count,
        "ws_clients": len(WS_CLIENTS),
        "available_ports": SerialManager.list_ports(),
        "stats": STATS,
    }


@app.post("/api/serial/open")
async def api_serial_open(data: dict):
    """打开串口"""
    port = data.get("port", "")
    baud = data.get("baud", 115200)
    if not port:
        return JSONResponse({"ok": False, "error": "缺少 port 参数"}, status_code=400)
    ok = SERIAL.open(port, baud)
    return {"ok": ok, "port": port, "baud": baud}


@app.post("/api/serial/close")
async def api_serial_close():
    """关闭串口"""
    SERIAL.close()
    return {"ok": True}


@app.post("/api/send")
async def api_send(data: dict):
    """发送数据到串口"""
    cmd = data.get("cmd", "")
    hex_mode = data.get("hex", False)
    if not cmd:
        return JSONResponse({"ok": False, "error": "缺少 cmd 参数"}, status_code=400)
    try:
        if hex_mode:
            # HEX 模式：将十六进制字符串转为字节
            hex_str = cmd.replace(" ", "").replace("\n", "")
            data_bytes = bytes.fromhex(hex_str)
            n = SERIAL.send(data_bytes)
        else:
            n = SERIAL.send_line(cmd)
        STATS["tx_bytes"] += n
        return {"ok": True, "sent_bytes": n, "cmd": cmd.strip()}
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": f"HEX 格式错误: {e}"}, status_code=400)


@app.get("/api/log/history")
async def api_log_history(
    lines: int = Query(100, description="返回行数"),
    filter: Optional[str] = Query(None, description="关键字过滤"),
):
    """获取历史日志"""
    if filter:
        return {"lines": BUFFER.get_filtered(filter, last_n=lines)}
    return {"lines": BUFFER.get_history(last_n=lines)}


@app.get("/api/log/since")
async def api_log_since(seq: int = Query(0, description="返回此序列号之后的日志")):
    """增量获取日志（基于序列号）

    供 MCP agent 轮询新日志使用。返回序列号严格大于 seq 的所有行。
    """
    new_lines, latest = BUFFER.get_after_seq(seq)
    return {"ok": True, "lines": new_lines, "before_seq": seq, "after_seq": latest}


@app.get("/api/log/last-seq")
async def api_log_last_seq():
    """获取当前最新序列号"""
    return {"ok": True, "last_seq": BUFFER.last_seq, "count": BUFFER.count}


@app.post("/api/send-and-collect")
async def api_send_and_collect(data: dict):
    """发送串口命令并等待收集设备响应

    典型场景：AI agent 发送 AT 命令或 shell 指令后，需要看到设备
    在一段时间内的输出作为"反馈"，而不是盲猜。

    参数:
        cmd: 要发送的命令文本
        hex: 是否以 HEX 模式发送（默认 False）
        wait: 发送后等待收集的秒数（0.1~10，默认 2.0）

    返回:
        ok, sent_bytes, cmd, wait_seconds, before_seq, after_seq,
        collected_lines: 等待期间新增的日志行列表
    """
    cmd = data.get("cmd", "")
    hex_mode = data.get("hex", False)
    wait_seconds = float(data.get("wait", 2.0))
    # 限制最大等待时间，避免 agent 把服务卡死
    wait_seconds = min(max(wait_seconds, 0.1), 10.0)

    if not cmd:
        return JSONResponse({"ok": False, "error": "缺少 cmd 参数"}, status_code=400)

    if not SERIAL.is_open:
        return JSONResponse(
            {"ok": False, "error": "串口未打开，请先调用 open_serial"}, status_code=400
        )

    before_seq = BUFFER.last_seq

    try:
        if hex_mode:
            hex_str = cmd.replace(" ", "").replace("\n", "")
            data_bytes = bytes.fromhex(hex_str)
            n = SERIAL.send(data_bytes)
        else:
            n = SERIAL.send_line(cmd)
        STATS["tx_bytes"] += n
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": f"HEX 格式错误: {e}"}, status_code=400)

    await asyncio.sleep(wait_seconds)

    new_lines, after_seq = BUFFER.get_after_seq(before_seq)
    return {
        "ok": True,
        "sent_bytes": n,
        "cmd": cmd.strip(),
        "wait_seconds": wait_seconds,
        "before_seq": before_seq,
        "after_seq": after_seq,
        "collected_lines": new_lines,
    }


@app.post("/api/log/clear")
async def api_log_clear():
    """清空日志缓冲"""
    BUFFER.clear()
    return {"ok": True}


# ---- 日志落盘（会话级文件）----

@app.get("/api/log/sessions")
async def api_log_sessions():
    """列出所有落盘会话文件（按修改时间倒序）。

    返回每个会话的 path、size、mtime。当前活跃会话标 current=true。
    """
    sessions = SINK.list_sessions()
    import os.path as _p
    from datetime import datetime as _dt
    result = []
    for path in sessions:
        try:
            st = os.stat(path)
            result.append({
                "path": path,
                "name": _p.basename(path),
                "size": st.st_size,
                "mtime": _dt.fromtimestamp(st.st_mtime).isoformat(),
                "current": (path == SINK.current_path),
            })
        except OSError:
            continue
    return {"ok": True, "enabled": SINK.enabled, "sessions": result,
            "current": SINK.current_path}


@app.post("/api/log/dump")
async def api_log_dump(data: dict = None):
    """把当前内存缓冲导出为一个文件（可选指定路径）。

    参数:
        path: 可选，目标文件绝对路径。不传则导出到落盘目录下，
              文件名 dump_<时间戳>.log。
        lines: 可选，导出最近 N 行，默认全部。

    与会话文件的区别：会话文件是持续追加的实时流；dump 是某一时刻的
    内存缓冲快照，适合"现在这一段日志我要单独存下来分析"。
    """
    if data is None:
        data = {}
    target = data.get("path")
    lines_n = data.get("lines")
    if lines_n:
        try:
            lines_n = int(lines_n)
        except (TypeError, ValueError):
            lines_n = None
    content_lines = BUFFER.get_history(last_n=lines_n) if lines_n else BUFFER.get_history(last_n=BUFFER.max_lines)
    content = "\n".join(content_lines)
    if not content_lines:
        content = "(空缓冲)"

    if not target:
        os.makedirs(_SINK_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_") + f"{datetime.now().microsecond // 1000:03d}"
        import secrets as _sec
        target = os.path.join(_SINK_DIR, f"dump_{ts}_{_sec.token_hex(2)}.log")
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(f"=== Serial Bridge 日志导出 ===\n")
            f.write(f"导出时间: {datetime.now().isoformat()}\n")
            f.write(f"行数: {len(content_lines)}\n")
            f.write("=" * 40 + "\n")
            f.write(content)
            f.write("\n")
        return {"ok": True, "path": target, "lines": len(content_lines)}
    except OSError as e:
        return JSONResponse({"ok": False, "error": f"写入失败: {e}"}, status_code=500)


@app.post("/api/log/rotate")
async def api_log_rotate():
    """轮转落盘会话：关闭当前文件，开启新会话文件。

    用于人为切分调试阶段——比如"开始测 WiFi"前 rotate 一下，
    新阶段日志进新文件，便于事后按阶段回看。
    """
    if not SINK.enabled:
        return {"ok": False, "error": "日志落盘未启用"}
    path = SINK.rotate(meta={
        "轮转时间": datetime.now().isoformat(),
        "串口": SERIAL.port or "(未连接)",
        "波特率": SERIAL.baud,
    })
    if path:
        return {"ok": True, "path": path}
    return {"ok": False, "error": "会话文件创建失败"}


@app.post("/api/stats/reset")
async def api_stats_reset():
    """重置收发统计"""
    STATS["tx_bytes"] = 0
    STATS["rx_bytes"] = 0
    return {"ok": True, "stats": STATS}


# ---- 快捷命令 ----

@app.get("/api/quick-commands")
async def api_get_quick_commands():
    """获取快捷命令列表"""
    return {"commands": QUICK_COMMANDS}


@app.post("/api/quick-commands")
async def api_add_quick_command(data: dict):
    """添加快捷命令"""
    name = data.get("name", "")
    cmd = data.get("cmd", "")
    hex_mode = data.get("hex", False)
    if not name or not cmd:
        return JSONResponse({"ok": False, "error": "缺少 name 或 cmd"}, status_code=400)
    QUICK_COMMANDS.append({"name": name, "cmd": cmd, "hex": hex_mode})
    return {"ok": True, "commands": QUICK_COMMANDS}


@app.delete("/api/quick-commands/{index}")
async def api_del_quick_command(index: int):
    """删除快捷命令"""
    if 0 <= index < len(QUICK_COMMANDS):
        QUICK_COMMANDS.pop(index)
        return {"ok": True, "commands": QUICK_COMMANDS}
    return JSONResponse({"ok": False, "error": "索引越界"}, status_code=400)


# ---- idf.py 集成 ----

def _reinit_idf(project_dir: str, export_script: str, boards_dir: str, board: str):
    """重新初始化 IDF 工具实例"""
    global IDF
    if not os.path.isdir(project_dir):
        return False, f"项目目录不存在: {project_dir}"
    IDF = IdfTool(
        project_dir=project_dir,
        export_script=export_script,
        boards_dir=boards_dir,
        board=board,
        on_output=_idf_output_callback,
    )
    logger.info(
        f"IDF 工具已重新初始化, 项目目录: {project_dir}, "
        f"boards_dir: {boards_dir}, 板型: {board}"
    )
    return True, "OK"


def _save_env(updates: dict):
    """将配置更新持久化到 .env 文件"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0]
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    # 添加 .env 中不存在的新键
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # 同步更新 os.environ
    for key, val in updates.items():
        os.environ[key] = val


@app.get("/api/config")
async def api_get_config():
    """获取当前 IDF 配置"""
    return {
        "ok": True,
        "config": {
            "project_dir": os.getenv("IDF_PROJECT_DIR", ""),
            "export_script": os.getenv("IDF_EXPORT_SCRIPT", ""),
            "boards_dir": os.getenv("IDF_BOARDS_DIR", "boards"),
            "board": os.getenv("IDF_BOARD", "lckfb_szpi_esp32s3"),
            "idf_initialized": IDF is not None,
        },
    }


@app.post("/api/config")
async def api_set_config(data: dict):
    """更新 IDF 配置（运行时生效 + 持久化到 .env）"""
    updates = {}
    for key in ["IDF_PROJECT_DIR", "IDF_EXPORT_SCRIPT", "IDF_BOARDS_DIR", "IDF_BOARD"]:
        if key in data:
            updates[key] = data[key]

    if not updates:
        return JSONResponse({"ok": False, "error": "无更新字段"}, status_code=400)

    # 持久化
    _save_env(updates)

    # 重新初始化 IDF
    project_dir = os.getenv("IDF_PROJECT_DIR", "")
    export_script = os.getenv("IDF_EXPORT_SCRIPT", "")
    boards_dir = os.getenv("IDF_BOARDS_DIR", "boards")
    board = os.getenv("IDF_BOARD", "lckfb_szpi_esp32s3")

    if project_dir:
        ok, msg = _reinit_idf(project_dir, export_script, boards_dir, board)
        return {"ok": ok, "message": msg if not ok else "配置已更新并生效"}
    else:
        return {"ok": True, "message": "配置已保存（项目目录为空，IDF 未初始化）"}


@app.get("/api/idf-versions")
async def api_idf_versions():
    """扫描可用的 ESP-IDF 版本"""
    versions = []
    # 扫描 C:\esp\ 下的版本目录
    esp_root = os.getenv("IDF_SCAN_ROOT", r"C:\esp")
    if os.path.isdir(esp_root):
        for name in os.listdir(esp_root):
            export_path = os.path.join(esp_root, name, "esp-idf", "export.ps1")
            if os.path.isfile(export_path):
                versions.append({
                    "version": name,
                    "export_script": export_path,
                })

    # 如果当前 export_script 不在扫描结果中，添加它
    current = os.getenv("IDF_EXPORT_SCRIPT", "")
    if current and not any(v["export_script"] == current for v in versions):
        versions.insert(0, {
            "version": "当前配置",
            "export_script": current,
        })

    return {"ok": True, "versions": versions}


@app.get("/api/idf-projects")
async def api_idf_projects():
    """扫描可用的 ESP-IDF 项目目录"""
    projects = []
    # 扫描常见目录
    scan_dirs = [
        r"D:\code\espclaw",
        r"D:\code",
    ]
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        for root, dirs, files in os.walk(scan_dir):
            # 只扫描 3 层深度
            depth = root[len(scan_dir):].count(os.sep)
            if depth >= 3:
                dirs[:] = []
                continue
            if "CMakeLists.txt" in files and "boards" in dirs:
                rel = os.path.relpath(root, scan_dir)
                projects.append({
                    "path": root,
                    "name": rel,
                })
            # 跳过 .git, build, __pycache__
            dirs[:] = [d for d in dirs if d not in (".git", "build", "__pycache__", "node_modules")]

    # 去重
    seen = set()
    unique = []
    for p in projects:
        if p["path"] not in seen:
            seen.add(p["path"])
            unique.append(p)

    return {"ok": True, "projects": unique}


@app.post("/api/build")
async def api_build(data: dict = {}):
    """触发编译"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化，请在配置中设置项目目录"},
            status_code=400,
        )
    board = data.get("board")
    ok, output = IDF.build(board=board)
    return {"ok": ok, "output": output[:500] if not ok else "编译完成"}


@app.post("/api/flash")
async def api_flash(data: dict):
    """触发烧录（自动管理串口释放与重连）"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化，未设置项目目录"},
            status_code=400,
        )
    port = data.get("port", SERIAL.port or "COM6")
    board = data.get("board")

    with SERIAL.acquire_for_flash():
        ok, output = IDF.flash(port=port, board=board)

    result_msg = f"[bridge] 烧录{'成功' if ok else '失败'}"
    BUFFER.append(result_msg)
    await _broadcast(result_msg)

    return {"ok": ok, "output": output[:500] if not ok else "烧录完成"}


@app.post("/api/clean")
async def api_clean():
    """触发 fullclean"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化"}, status_code=400
        )
    ok, output = IDF.fullclean()
    return {"ok": ok, "output": output[:300]}


@app.post("/api/bmgr")
async def api_bmgr(data: dict = {}):
    """触发 bmgr 选择板型"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化"}, status_code=400
        )
    board = data.get("board")
    ok, output = IDF.bmgr(board=board)
    return {"ok": ok, "output": output[:500] if not ok else "bmgr 完成"}


@app.get("/api/boards")
async def api_list_boards():
    """列出可用板型（IDF 未初始化时尝试自动初始化）"""
    global IDF
    # 如果 IDF 未初始化但 .env 中有项目目录，尝试自动初始化
    if IDF is None:
        project_dir = os.getenv("IDF_PROJECT_DIR", "")
        if project_dir and os.path.isdir(project_dir):
            export_script = os.getenv("IDF_EXPORT_SCRIPT", r"C:\esp\v5.5.4\esp-idf\export.ps1")
            boards_dir = os.getenv("IDF_BOARDS_DIR", "boards")
            board = os.getenv("IDF_BOARD", "lckfb_szpi_esp32s3")
            _reinit_idf(project_dir, export_script, boards_dir, board)

    if IDF is None:
        return {"ok": True, "boards": [], "current": "", "error": "未配置项目目录"}

    ok, boards = IDF.list_boards()
    return {"ok": True, "boards": boards, "current": IDF.board}


@app.post("/api/boards/select")
async def api_select_board(data: dict):
    """选择板型"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化"}, status_code=400
        )
    board = data.get("board")
    if not board:
        return JSONResponse({"ok": False, "error": "缺少 board 参数"}, status_code=400)
    ok, output = IDF.select_board(board)
    return {"ok": ok, "board": board, "output": output[:500] if not ok else "选择成功"}


@app.post("/api/menuconfig")
async def api_menuconfig():
    """触发 menuconfig（需终端环境，Web 下可能不工作）"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化"}, status_code=400
        )
    ok, output = IDF.menuconfig()
    return {"ok": ok, "output": output[:500] if not ok else "menuconfig 退出"}


# ---- WebSocket ----

@app.websocket("/ws/log")
async def ws_log(websocket: WebSocket):
    """WebSocket 实时日志流"""
    await websocket.accept()
    WS_CLIENTS.append(websocket)
    logger.info(f"WebSocket 客户端连接, 当前在线: {len(WS_CLIENTS)}")

    # 推送历史日志
    history = BUFFER.get_history(last_n=200)
    await websocket.send_text("--- 历史日志 (最近 200 行) ---")
    for line in history:
        await websocket.send_text(line)
    await websocket.send_text("--- 实时日志 ---")

    try:
        while True:
            data = await websocket.receive()
            msg_type = data.get("type", "")
            if msg_type == "websocket.disconnect":
                break
            if "text" in data and data["text"] is not None:
                text = data["text"]
                if text.startswith("/send "):
                    cmd = text[6:]
                    try:
                        SERIAL.send_line(cmd)
                    except RuntimeError as e:
                        await websocket.send_text(f"[bridge] 发送失败: {e}")
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in WS_CLIENTS:
            WS_CLIENTS.remove(websocket)
        logger.info(f"WebSocket 客户端断开, 当前在线: {len(WS_CLIENTS)}")


# ---- 终端 WebSocket（方案 3：xterm.js 全终端）----

def _make_terminal_callback(ws: WebSocket, loop: asyncio.AbstractEventLoop):
    """创建一个串口数据回调，把原始 bytes 推给指定终端 WS（线程安全版）"""
    async def _push(data: bytes):
        try:
            await ws.send_bytes(data)
        except Exception:
            pass

    def callback(data: bytes):
        if loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(_push(data), loop)
            except Exception:
                pass
    return callback


@app.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket, mode: str = "serial"):
    """终端 WebSocket — 支持 serial / shell 双模式

    GET /ws/terminal?mode=serial  —— 串口终端（连 ESP32 设备）
    GET /ws/terminal?mode=shell   —— Shell 终端（执行 idf.py 等命令）
    """
    await websocket.accept()
    loop = asyncio.get_running_loop()
    logger.info(f"终端连接: mode={mode}")

    if mode == "serial":
        # ---- 串口模式 ----
        if not SERIAL.is_open:
            await websocket.send_text("[bridge] 串口未打开，请先连接串口\r\n")

        cb = _make_terminal_callback(websocket, loop)
        SERIAL.add_callback(cb)

        try:
            while True:
                msg = await websocket.receive()
                msg_type = msg.get("type", "")
                if msg_type == "websocket.disconnect":
                    break
                if "bytes" in msg and msg["bytes"] is not None:
                    SERIAL.send(msg["bytes"])
                elif "text" in msg and msg["text"] is not None:
                    SERIAL.send(msg["text"].encode("utf-8"))
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"终端异常 (serial): {e}")
        finally:
            SERIAL.remove_callback(cb)
            logger.info("终端断开 (serial)")

    elif mode == "shell":
        # ---- Shell 模式（winpty + PowerShell 直启动）----
        try:
            from winpty import PTY
        except ImportError:
            await websocket.send_text(
                "[bridge] winpty 未安装，无法启动 Shell 终端\r\n"
                "请运行: pip install pywinpty\r\n"
            )
            return

        # 构造干净环境：移除 MSYS/Mingw 变量，避免 ESP-IDF 报
        # "MSys/Mingw is not supported"
        spawn_env = os.environ.copy()
        for k in ("MSYSTEM", "MSYSTEM_CHOST", "MSYSTEM_PREFIX", "MINGW_CHOST",
                   "MINGW_PREFIX", "MINGW_PACKAGE_PREFIX"):
            spawn_env.pop(k, None)

        # 用 PowerShell 直接启动（不经过 cmd.exe 嵌套，避免输出交错）
        pty = PTY(120, 30)
        powershell_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        project_dir = os.getenv("IDF_PROJECT_DIR", "")
        export_script = os.getenv("IDF_EXPORT_SCRIPT", "")

        try:
            pty.spawn(
                powershell_path,
                cmdline=f'"{powershell_path}" -NoProfile -NoLogo -NoExit',
                cwd=project_dir if project_dir and os.path.isdir(project_dir) else None,
            )
        except Exception as e:
            await websocket.send_text(f"[bridge] PTY 启动失败: {e}\r\n")
            return

        # 等待 PowerShell 完成启动
        import time
        time.sleep(1.5)

        # 初始化命令（PowerShell 语法），每条之间留间隔确保执行
        init_cmds = [
            # 设置 UTF-8 编码，解决中文乱码
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "$OutputEncoding = [System.Text.Encoding]::UTF8",
            # 清除 MSYS/Mingw 环境变量（ESP-IDF export.ps1 会检测）
            "Remove-Item Env:MSYSTEM -ErrorAction SilentlyContinue",
            "Remove-Item Env:MSYSTEM_CHOST -ErrorAction SilentlyContinue",
            "Remove-Item Env:MSYSTEM_PREFIX -ErrorAction SilentlyContinue",
        ]

        if export_script and os.path.isfile(export_script):
            init_cmds.append(f". '{export_script}'")

        init_cmds.append(
            "Write-Host '[bridge] Shell 终端就绪，IDF 环境已加载' -ForegroundColor Green"
        )

        for i, cmd in enumerate(init_cmds):
            pty.write(cmd + "\r\n")
            # 每条命令之间间隔 0.5s，确保 PowerShell 有时间执行
            time.sleep(0.5)

        # 后台线程：持续读取 PTY 输出 → 推到 WS
        pty_running = True

        def _pty_reader():
            while pty_running:
                try:
                    data = pty.read()
                    if data:
                        if loop.is_running():
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    _send_pty_data(websocket, data), loop
                                )
                            except Exception:
                                break
                except Exception:
                    break
                import time
                time.sleep(0.02)

        async def _send_pty_data(ws, data):
            try:
                if isinstance(data, str):
                    await ws.send_bytes(data.encode("utf-8", errors="replace"))
                else:
                    await ws.send_bytes(data)
            except Exception:
                pass

        reader_thread = threading.Thread(target=_pty_reader, daemon=True)
        reader_thread.start()

        try:
            while True:
                msg = await websocket.receive()
                msg_type = msg.get("type", "")
                if msg_type == "websocket.disconnect":
                    break
                if "bytes" in msg and msg["bytes"] is not None:
                    pty.write(msg["bytes"].decode("utf-8", errors="replace"))
                elif "text" in msg and msg["text"] is not None:
                    pty.write(msg["text"])
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"终端异常 (shell): {e}")
        finally:
            pty_running = False
            try:
                pty.write("exit\r\n")
            except Exception:
                pass
            logger.info("终端断开 (shell)")

    else:
        await websocket.send_text(f"[bridge] 未知模式: {mode}\r\n")


# ---- Web 前端页面 ----

@app.get("/")
async def index():
    """返回 Web 前端页面"""
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Serial Bridge</h1><p>前端文件缺失</p>")


# ---- 命令行入口 ----

def main():
    parser = argparse.ArgumentParser(description="Serial Bridge — ESP32 串口代理服务")
    parser.add_argument("--port", "-p", default=None, help="串口端口 (覆盖 .env)")
    parser.add_argument("--baud", "-b", type=int, default=None, help="波特率 (覆盖 .env)")
    parser.add_argument("--host", default=None, help="监听地址 (覆盖 .env)")
    parser.add_argument("--http-port", type=int, default=None, help="HTTP 端口 (覆盖 .env)")
    parser.add_argument("--project-dir", "-d", default=None, help="ESP-IDF 项目目录")
    parser.add_argument("--idf-export", default=None, help="ESP-IDF export.ps1 路径")
    args = parser.parse_args()

    global IDF

    # 配置优先级: 命令行 > .env > 默认值
    host = args.host or os.getenv("HOST", "127.0.0.1")
    http_port = args.http_port or int(os.getenv("HTTP_PORT", "8080"))
    project_dir = args.project_dir or os.getenv("IDF_PROJECT_DIR", "")
    idf_export = args.idf_export or os.getenv(
        "IDF_EXPORT_SCRIPT", r"C:\esp\v5.5.4\esp-idf\export.ps1"
    )
    boards_dir = os.getenv("IDF_BOARDS_DIR", "boards")
    default_board = os.getenv("IDF_BOARD", "lckfb_szpi_esp32s3")

    # 命令行端口覆盖 .env（在 lifespan 中读取 .env 自动连接）
    if args.port:
        os.environ["SERIAL_PORT"] = args.port
    if args.baud:
        os.environ["SERIAL_BAUD"] = str(args.baud)

    # 初始化 IDF 工具
    if project_dir:
        if not os.path.isdir(project_dir):
            logger.error(f"项目目录不存在: {project_dir}")
            sys.exit(1)
        IDF = IdfTool(
            project_dir=project_dir,
            export_script=idf_export,
            boards_dir=boards_dir,
            board=default_board,
            on_output=_idf_output_callback,
        )
        logger.info(
            f"IDF 工具已初始化, 项目目录: {project_dir}, "
            f"boards_dir: {boards_dir}, 板型: {default_board}"
        )
    else:
        logger.info("未设置项目目录，编译/烧录功能不可用")

    # 启动 Web 服务
    logger.info(f"启动 Web 服务: http://{host}:{http_port}")

    # 关闭 uvicorn 访问日志（GET /api/status 轮询等噪音），
    # 保留启动/关闭/错误等关键日志
    uvicorn.run(
        app,
        host=host,
        port=http_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()