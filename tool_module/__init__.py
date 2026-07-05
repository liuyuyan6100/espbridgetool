"""tool_module —— ESP32 Serial Bridge 的 MCP 工具实现集合。

本包把所有 MCP 工具（tools）与资源（resources）的实现按职责拆分到子模块：
  - serial_tools:  串口层（列出/打开/关闭/发送/收响应）
  - log_tools:     日志层（历史/增量/序列号/清空）
  - idf_tools:     ESP-IDF 工具链（配置/板型/编译/烧录/清理）
  - resources:     只读上下文快照（status/logs/config）
  - bridge_client: 对 serial_bridge.py Web 服务的 HTTP 客户端封装（共享）

主文件 mcp_server.py 只负责：解析参数 → 重建客户端 → 创建 FastMCP →
调用 register_all(mcp) 注册全部工具 → mcp.run()。

新增工具时：在对应子模块的 register() 内用 @mcp.tool() 定义即可，
无需改动 mcp_server.py。
"""

from . import serial_tools, log_tools, idf_tools, resources


def register_all(mcp) -> None:
    """把所有子模块的工具/resources 一次性注册到 FastMCP 实例。"""
    serial_tools.register(mcp)
    log_tools.register(mcp)
    idf_tools.register(mcp)
    resources.register(mcp)


__all__ = ["register_all"]
