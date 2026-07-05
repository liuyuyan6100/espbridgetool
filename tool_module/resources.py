"""MCP Resources（只读上下文，agent 可主动拉取快照）。

Resources 与 tools 不同：tools 是 agent 主动调用的动作，resources 是 agent
可按 URI 拉取的只读上下文快照。新增/修改 resources 都在本文件内完成。
"""

from mcp.server.fastmcp import FastMCP

from . import bridge_client


def register(mcp: FastMCP) -> None:
    """将只读 resources 注册到给定的 FastMCP 实例。"""

    @mcp.resource("esp32://status")
    def resource_status() -> str:
        """Serial Bridge 当前状态快照（串口连接、统计、可用端口）"""
        return bridge_client.fmt(bridge_client.call("GET", "/api/status"))

    @mcp.resource("esp32://logs/recent")
    def resource_recent_logs() -> str:
        """最近 50 行日志，用于快速了解设备当前输出"""
        return bridge_client.fmt(
            bridge_client.call("GET", "/api/log/history", params={"lines": 50})
        )

    @mcp.resource("esp32://config")
    def resource_config() -> str:
        """当前 ESP-IDF 配置（项目目录、板型、export 脚本路径）"""
        return bridge_client.fmt(bridge_client.call("GET", "/api/config"))
