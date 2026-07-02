#!/usr/bin/env python3
"""Serial Bridge — ESP32 串口代理服务主程序入口

启动:
    python serial_bridge.py          # 读取 .env 配置启动
    python serial_bridge.py --port COM6 --baud 115200   # 命令行覆盖
"""

import argparse
import logging
import os
import sys
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


def _on_serial_data(data: bytes):
    """串口数据回调：写入日志缓冲并广播到所有 WebSocket 客户端"""
    STATS["rx_bytes"] += len(data)
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = repr(data)

    BUFFER.append(text, raw=data)
    _broadcast_task(text)


def _broadcast_task(text: str):
    """将广播任务延迟到事件循环中执行"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_broadcast(text))
    except RuntimeError:
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


SERIAL = SerialManager(on_data=_on_serial_data)


def _idf_output_callback(line: str):
    """idf.py 输出回调：推送到日志缓冲 + WebSocket"""
    BUFFER.append(line)
    _broadcast_task(f"[idf.py] {line}")


# ---- FastAPI App ----

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Serial Bridge 服务启动")
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
    logger.info("Serial Bridge 服务关闭")


app = FastAPI(
    title="Serial Bridge",
    description="ESP32 串口代理服务 — AI Agent 可通过 REST API 控制串口",
    version="1.1.0",
    lifespan=lifespan,
)

# 挂载静态文件目录（CSS/JS 等）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


@app.post("/api/log/clear")
async def api_log_clear():
    """清空日志缓冲"""
    BUFFER.clear()
    return {"ok": True}


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

@app.post("/api/build")
async def api_build(data: dict = {}):
    """触发编译"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化，未设置项目目录"},
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
    """触发 bmgr"""
    global IDF
    if IDF is None:
        return JSONResponse(
            {"ok": False, "error": "IDF 工具未初始化"}, status_code=400
        )
    board = data.get("board")
    ok, output = IDF.bmgr(board=board)
    return {"ok": ok, "output": output[:500] if not ok else "bmgr 完成"}


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
            data = await websocket.receive_text()
            if data.startswith("/send "):
                cmd = data[6:]
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
            on_output=_idf_output_callback,
        )
        logger.info(f"IDF 工具已初始化, 项目目录: {project_dir}")
    else:
        logger.info("未设置项目目录，编译/烧录功能不可用")

    # 启动 Web 服务
    logger.info(f"启动 Web 服务: http://{host}:{http_port}")
    uvicorn.run(app, host=host, port=http_port, log_level="info")


if __name__ == "__main__":
    main()