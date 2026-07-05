#!/usr/bin/env python3
"""ESP32 Serial Bridge — MCP Server (stdio)

让 AI 智能体通过标准 MCP (Model Context Protocol) 协议直接操作 ESP32：
  - 列出/打开/关闭串口
  - 发送命令到串口
  - 读取历史日志 / 增量获取日志
  - **发命令并收集设备响应**（send_and_collect — agent 最常用）
  - 编译 / 烧录 / 清理 / 板型管理
  - 配置 ESP-IDF

架构
----
本 MCP server 是一个 stdio 进程，内部通过 HTTP 调用本地已运行的
serial_bridge.py（FastAPI Web 服务）。串口/IDF 状态集中在 Web 服务
统一管理，避免双进程抢占同一个 COM 口。Web UI 和 MCP agent 可同时
观察设备状态。

         ┌─────────┐  stdio   ┌──────────────┐  HTTP   ┌──────────────┐
         │  Agent  │◄────────►│ mcp_server.py│◄───────►│serial_bridge │
         │(WorkBuddy│  (MCP)   │  (本文件)     │ (REST)  │  .py (8080)  │
         │/Cursor) │          └──────────────┘         │      │       │
         └─────────┘                                    └──────┼───────┘
                                                               │ COM6
                                                               ▼
                                                          ┌─────────┐
                                                          │  ESP32  │
                                                          └─────────┘

本文件的职责（仅工具调度）
--------------------------
本文件不再实现具体工具，只负责：
  1. 解析命令行参数（--host / --port）
  2. 按参数重建 HTTP 客户端（tool_module.bridge_client.rebuild_client）
  3. 创建 FastMCP 实例
  4. 调用 tool_module.register_all(mcp) 注册全部工具/resources
  5. mcp.run() 启动 stdio 服务

具体工具实现全部在 tool_module/ 包内，按职责拆分：
  - tool_module/serial_tools.py  串口层工具
  - tool_module/log_tools.py     日志层工具
  - tool_module/idf_tools.py     ESP-IDF 工具链工具
  - tool_module/resources.py     只读 resources
  - tool_module/bridge_client.py HTTP 客户端封装（call/fmt/配置）

新增/修改工具时只需编辑 tool_module/ 下对应文件，无需改动本文件。

启动前置
--------
1. 先运行 serial_bridge.py（双击 start.bat 或 `python serial_bridge.py`）
   —— 它是真正的串口/IDF 控制者，监听 http://127.0.0.1:8080
2. 再由 agent spawn 本 MCP server

配置
----
BRIDGE_HOST / BRIDGE_PORT 从 .env、环境变量读取，默认 127.0.0.1:8080。
也可用命令行参数覆盖：python mcp_server.py --host 127.0.0.1 --port 8080

在 WorkBuddy / Claude Desktop / Cursor / TRAE 中接入
----------------------------------------------------
    {
      "mcpServers": {
        "esp32-bridge": {
          "command": "D:\\code\\espclaw\\espbridgetool\\.venv\\Scripts\\python.exe",
          "args": ["D:\\code\\espclaw\\espbridgetool\\mcp_server.py"]
        }
      }
    }

本地调试
--------
    python mcp_server.py            # 以 stdio 模式启动（等 agent 连）
    python mcp_server.py --help     # 查看参数
"""

import argparse
import sys

from tool_module import bridge_client, register_all

# FastMCP 必须在 import 时就知道 server 名称
try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    print(
        f"[mcp_server] 未安装 mcp SDK: {e}\n"
        "请运行: pip install mcp httpx",
        file=sys.stderr,
    )
    sys.exit(1)

mcp = FastMCP("esp32-serial-bridge")

# 一次性注册全部工具与 resources（实现都在 tool_module 包内）
register_all(mcp)


# ---- 入口 ----

def main():
    parser = argparse.ArgumentParser(
        description="ESP32 Serial Bridge — MCP Server (stdio)",
    )
    parser.add_argument(
        "--host", default=bridge_client.BRIDGE_HOST,
        help=f"Serial Bridge 主机 (默认 {bridge_client.BRIDGE_HOST}，来自 .env/环境变量)",
    )
    parser.add_argument(
        "--port", type=int, default=bridge_client.BRIDGE_PORT,
        help=f"Serial Bridge 端口 (默认 {bridge_client.BRIDGE_PORT}，来自 .env/环境变量)",
    )
    args = parser.parse_args()

    # 按命令行/最终配置重建 httpx 客户端
    bridge_client.rebuild_client(args.host, args.port)

    # 提示信息打到 stderr（stdio transport 用 stdout 通信，不能污染）
    # 用英文避免 Windows 控制台 GBK 编码导致 agent 端读 stderr 乱码
    print(
        f"[mcp_server] ESP32 Serial Bridge MCP Server started\n"
        f"[mcp_server] Bridge endpoint: {bridge_client.BRIDGE_URL}\n"
        f"[mcp_server] Make sure serial_bridge.py is running at that address",
        file=sys.stderr,
    )

    mcp.run()


if __name__ == "__main__":
    main()
