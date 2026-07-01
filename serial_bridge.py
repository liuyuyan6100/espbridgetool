#!/usr/bin/env python3
"""Serial Bridge — ESP32 串口代理服务主程序入口

启动:
    python serial_bridge.py --port COM6 --baud 115200
    python serial_bridge.py                        # 仅启动 Web 服务，手动连接串口
"""

import argparse
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from serial_manager import SerialManager
from log_buffer import LogBuffer
from idf_tool import IdfTool

# ---- 日志配置 ----

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("serial_bridge")

# ---- 全局状态 ----

SERIAL = SerialManager(on_data=None)
BUFFER = LogBuffer(max_lines=10000)
IDF: Optional[IdfTool] = None

# WebSocket 连接池
WS_CLIENTS: List[WebSocket] = []

# 状态常量
STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTED = "connected"
STATUS_FLASHING = "flashing"


def _on_serial_data(data: bytes):
    """串口数据回调：写入日志缓冲并广播到所有 WebSocket 客户端"""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = repr(data)

    BUFFER.append(text, raw=data)

    # 广播到所有 WebSocket 客户端
    dead_clients = []
    for ws in WS_CLIENTS:
        try:
            # 子进程（如 asyncio）需要异步发送，此处抛给事件循环
            # 实际在 ws_sender 协程中处理
            pass
        except Exception:
            dead_clients.append(ws)

    # (清理已断开连接由 WebSocket 端点处理)
    _broadcast_task(text)


def _broadcast_task(text: str):
    """将广播任务延迟到事件循环中执行"""
    # 由 FastAPI 事件循环中的 publish 协程处理
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
    # 启动时
    logger.info("Serial Bridge 服务启动")
    yield
    # 关闭时
    if SERIAL.is_open:
        SERIAL.close()
    logger.info("Serial Bridge 服务关闭")


app = FastAPI(
    title="Serial Bridge",
    description="ESP32 串口代理服务 — AI Agent 可通过 REST API 控制串口",
    version="1.0.0",
    lifespan=lifespan,
)


# ---- REST API ----

@app.get("/api/status")
async def api_status():
    """获取串口状态"""
    global IDF
    flashing = _get_flashing_status()
    return {
        "status": STATUS_FLASHING if flashing else (
            STATUS_CONNECTED if SERIAL.is_open else STATUS_DISCONNECTED
        ),
        "port": SERIAL.port,
        "baud": SERIAL.baud,
        "log_lines": BUFFER.count,
        "ws_clients": len(WS_CLIENTS),
        "available_ports": SerialManager.list_ports(),
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
    if not cmd:
        return JSONResponse({"ok": False, "error": "缺少 cmd 参数"}, status_code=400)
    try:
        n = SERIAL.send_line(cmd)
        return {"ok": True, "sent_bytes": n, "cmd": cmd.strip()}
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


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

    # 广播烧录结果
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
            # 接收客户端消息（可用于发送命令等）
            data = await websocket.receive_text()
            # 如果消息以 /send 开头，发到串口
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
    return HTMLResponse("<h1>Serial Bridge</h1><p>前端文件缺失，请确认 static/index.html 存在</p>")


# ---- 工具函数 ----

_flashing = False


def _get_flashing_status() -> bool:
    return _flashing


def _set_flashing(val: bool):
    global _flashing
    _flashing = val


# ---- 命令行入口 ----

def main():
    parser = argparse.ArgumentParser(description="Serial Bridge — ESP32 串口代理服务")
    parser.add_argument("--port", "-p", help="串口端口，如 COM6")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="波特率")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port-http", type=int, default=8080, help="HTTP 端口")
    parser.add_argument(
        "--project-dir",
        "-d",
        default=None,
        help="ESP-IDF 项目目录（启用 idf.py 集成）",
    )
    parser.add_argument(
        "--idf-export",
        default=r"C:\esp\v5.5.4\esp-idf\export.ps1",
        help="ESP-IDF export.ps1 路径",
    )
    args = parser.parse_args()

    global IDF

    # 初始化 IDF 工具
    if args.project_dir:
        if not os.path.isdir(args.project_dir):
            logger.error(f"项目目录不存在: {args.project_dir}")
            sys.exit(1)
        IDF = IdfTool(
            project_dir=args.project_dir,
            export_script=args.idf_export,
            on_output=_idf_output_callback,
        )
        logger.info(f"IDF 工具已初始化, 项目目录: {args.project_dir}")
    else:
        logger.info("未设置项目目录，编译/烧录功能不可用")

    # 自动打开串口
    if args.port:
        ok = SERIAL.open(args.port, args.baud)
        if not ok:
            logger.warning(f"串口 {args.port} 打开失败，仅启动 Web 服务")
        else:
            logger.info(f"串口已打开: {args.port} @ {args.baud}")

    # 启动 Web 服务
    logger.info(f"启动 Web 服务: http://{args.host}:{args.port_http}")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port_http,
        log_level="info",
    )


if __name__ == "__main__":
    main()