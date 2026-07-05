"""日志层 MCP 工具：历史日志、增量日志、最新序列号、清空日志。

新增/修改日志相关工具都在本文件内完成。
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import bridge_client


def register(mcp: FastMCP) -> None:
    """将日志层工具注册到给定的 FastMCP 实例。"""

    @mcp.tool()
    def get_logs(lines: int = 100, keyword: Optional[str] = None) -> str:
        """获取历史日志（最近 N 行，可按关键字过滤）。

        参数:
            lines: 返回的行数，默认 100。设为较大值（如 500）可看更多上下文。
            keyword: 可选的关键字过滤（大小写不敏感）。例如 "error" 只看错误行。

        日志是环形缓冲，最多保留 10000 行（可在 .env 的 LOG_MAX_LINES 调整）。
        """
        params = {"lines": lines}
        if keyword:
            params["filter"] = keyword
        return bridge_client.fmt(bridge_client.call("GET", "/api/log/history", params=params))

    @mcp.tool()
    def get_logs_since(seq: int = 0) -> str:
        """增量获取日志：返回序列号严格大于 seq 的所有新日志行。

        用于轮询场景——agent 记住上次拿到的 after_seq，下次传进来即可只取增量，
        避免重复读取历史。

        参数:
            seq: 上次获取返回的 after_seq。首次调用传 0（取全部）或先调
                  get_last_seq 拿当前序列号，之后只取新行。

        返回:
            ok, before_seq, after_seq, lines: 新行列表。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/log/since", params={"seq": seq}))

    @mcp.tool()
    def get_last_seq() -> str:
        """获取当前日志缓冲的最新序列号和总行数。

        通常在开始监控前调用一次拿到 last_seq，之后用 get_logs_since(seq=last_seq)
        轮询新日志。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/log/last-seq"))

    @mcp.tool()
    def clear_logs() -> str:
        """清空日志缓冲。

        谨慎使用：会删除所有历史日志。通常在开始新一轮调试前清理，避免旧日志干扰。
        """
        return bridge_client.fmt(bridge_client.call("POST", "/api/log/clear"))

    @mcp.tool()
    def dump_logs(lines: Optional[int] = None, path: Optional[str] = None) -> str:
        """把当前内存日志缓冲导出为文件（落盘快照）。

        与会话文件的区别：会话文件是持续追加的实时流；dump 是某一时刻的
        内存快照，适合"现在这一段日志我要单独存下来分析"。

        参数:
            lines: 可选，导出最近 N 行。不传则导出全部缓冲。
            path: 可选，目标文件绝对路径。不传则存到落盘目录下，
                  文件名 dump_<时间戳>.log。

        返回: ok, path（导出文件路径）, lines（导出行数）。
        """
        payload = {}
        if lines is not None:
            payload["lines"] = lines
        if path:
            payload["path"] = path
        return bridge_client.fmt(bridge_client.call("POST", "/api/log/dump", json=payload))

    @mcp.tool()
    def list_log_sessions() -> str:
        """列出所有落盘会话文件（按修改时间倒序）。

        返回每个会话的 path、name、size、mtime，当前活跃会话标 current=true。
        用于回看历史调试会话，或确认日志落盘是否在工作。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/log/sessions"))

    @mcp.tool()
    def rotate_log_session() -> str:
        """轮转落盘会话：关闭当前文件，开启新会话文件。

        用于人为切分调试阶段——比如"开始测 WiFi"前调用一次，
        新阶段日志进新文件，便于事后按阶段回看。
        """
        return bridge_client.fmt(bridge_client.call("POST", "/api/log/rotate"))
