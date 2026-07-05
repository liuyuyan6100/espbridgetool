"""串口层 MCP 工具：列出/打开/关闭串口、发送命令、发命令并收集响应。

每个工具的 docstring 会成为 MCP 客户端看到的工具描述，请保持完整准确。
新增/修改串口相关工具都在本文件内完成。
"""

from mcp.server.fastmcp import FastMCP

from . import bridge_client


def register(mcp: FastMCP) -> None:
    """将串口层工具注册到给定的 FastMCP 实例。"""

    @mcp.tool()
    def list_serial_ports() -> str:
        """列出当前系统所有可用的串口设备。

        返回每个串口的 device（如 "COM6"）、description（设备描述）、hwid（硬件 ID）。
        在打开串口前调用此工具确认 ESP32 已通过 USB 连接并被系统识别。
        """
        data = bridge_client.call("GET", "/api/status")
        if not data.get("ok", True) and "available_ports" not in data:
            return bridge_client.fmt(data)
        return bridge_client.fmt(data.get("available_ports", []))

    @mcp.tool()
    def get_status() -> str:
        """获取 Serial Bridge 服务的完整状态快照。

        返回：串口是否已连接、当前端口/波特率、日志缓冲行数、WebSocket 客户端数、
        收发字节统计、可用串口列表。用于在执行任何操作前确认环境就绪。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/status"))

    @mcp.tool()
    def open_serial(port: str, baud: int = 115200) -> str:
        """打开指定串口，启动后台读取线程。

        参数:
            port: 串口设备名，如 "COM6"（Windows）或 "/dev/ttyUSB0"（Linux）。
                  先用 list_serial_ports 查可用端口。
            baud: 波特率，ESP32 默认 115200。

        打开后，设备输出的所有数据会进入日志缓冲，可用 get_logs / get_logs_since 读取。
        """
        return bridge_client.fmt(
            bridge_client.call("POST", "/api/serial/open", json={"port": port, "baud": baud})
        )

    @mcp.tool()
    def close_serial() -> str:
        """关闭当前已打开的串口。

        通常在烧录前不需要手动关闭——flash 工具会自动释放并重连串口。
        """
        return bridge_client.fmt(bridge_client.call("POST", "/api/serial/close"))

    @mcp.tool()
    def send_command(cmd: str, hex_mode: bool = False) -> str:
        """向串口发送一条命令，不等待设备响应（异步发出即返回）。

        参数:
            cmd: 要发送的文本。文本模式下会自动追加 \\r\\n。
            hex_mode: True 时把 cmd 当作十六进制字符串（如 "AT+RST" 不行，
                      "41542B525354" 可行），直接发原始字节。

        如果需要看到设备的反馈，请改用 send_and_collect（发命令 + 等待收集响应）。
        """
        return bridge_client.fmt(
            bridge_client.call("POST", "/api/send", json={"cmd": cmd, "hex": hex_mode})
        )

    @mcp.tool()
    def send_and_collect(cmd: str, wait_seconds: float = 2.0, hex_mode: bool = False) -> str:
        """【核心工具】发送一条命令到串口，并等待若干秒收集设备的响应输出。

        这是 agent 与 ESP32 交互最常用的工具——发命令后能看到设备实际回了什么，
        而不是盲猜。例如发送 AT 命令查看模块回复、发送 shell 命令查看输出、
        触发某个动作后观察日志。

        参数:
            cmd: 要发送的命令文本（自动追加 \\r\\n）。
            wait_seconds: 发送后等待收集设备输出的秒数，范围 0.1~10，默认 2.0。
                          日志量大可调小，设备响应慢可调大（最大 10 秒）。
            hex_mode: True 时按十六进制原始字节发送。

        返回:
            ok, sent_bytes, cmd, wait_seconds, before_seq, after_seq,
            collected_lines: 等待期间新增的日志行列表（这就是设备的"响应"）。

        注意: 需要串口已打开。如果 collected_lines 为空，可能是设备没响应、
              命令格式不对，或等待时间太短——可适当增大 wait_seconds 重试。
        """
        return bridge_client.fmt(
            bridge_client.call(
                "POST",
                "/api/send-and-collect",
                json={"cmd": cmd, "wait": wait_seconds, "hex": hex_mode},
                timeout=30.0,  # wait 最长 10s + 余量
            )
        )
