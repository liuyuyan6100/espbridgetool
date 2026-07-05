"""ESP-IDF 工具链 MCP 工具：配置、板型管理、编译、烧录、清理。

新增/修改 ESP-IDF 相关工具都在本文件内完成。
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import bridge_client


def register(mcp: FastMCP) -> None:
    """将 ESP-IDF 工具链工具注册到给定的 FastMCP 实例。"""

    @mcp.tool()
    def get_idf_config() -> str:
        """获取当前 ESP-IDF 配置：项目目录、export.ps1 路径、boards 目录、当前板型。

        build/flash 等操作依赖这些配置正确。如果 idf_initialized 为 false，
        需要先调 set_idf_config 设置项目目录。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/config"))

    @mcp.tool()
    def set_idf_config(
        project_dir: Optional[str] = None,
        export_script: Optional[str] = None,
        boards_dir: Optional[str] = None,
        board: Optional[str] = None,
    ) -> str:
        """更新 ESP-IDF 配置（运行时生效 + 持久化到 .env）。

        所有参数都可选，只传需要改的字段即可。更新后会重新初始化 IDF 工具实例。

        参数:
            project_dir: ESP-IDF 项目根目录（含 CMakeLists.txt 和 boards/）
            export_script: ESP-IDF 的 export.ps1 绝对路径
            boards_dir: Board Manager 的 boards 目录（相对 project_dir 或绝对）
            board: 默认板型名称（如 lckfb_szpi_esp32s3）

        可用 list_idf_versions / list_idf_projects 辅助查找正确路径。
        """
        payload = {}
        if project_dir is not None:
            payload["IDF_PROJECT_DIR"] = project_dir
        if export_script is not None:
            payload["IDF_EXPORT_SCRIPT"] = export_script
        if boards_dir is not None:
            payload["IDF_BOARDS_DIR"] = boards_dir
        if board is not None:
            payload["IDF_BOARD"] = board
        if not payload:
            return bridge_client.fmt({"ok": False, "error": "未提供任何配置字段"})
        return bridge_client.fmt(bridge_client.call("POST", "/api/config", json=payload))

    @mcp.tool()
    def list_idf_versions() -> str:
        """扫描系统中已安装的 ESP-IDF 版本（默认扫描 C:\\esp\\）。

        返回每个版本的 version 名称和 export.ps1 绝对路径，用于 set_idf_config。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/idf-versions"))

    @mcp.tool()
    def list_idf_projects() -> str:
        """扫描常见目录下可用的 ESP-IDF 项目（含 CMakeLists.txt + boards/ 的目录）。

        返回每个项目的绝对路径和相对名称，用于 set_idf_config 的 project_dir 参数。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/idf-projects"))

    @mcp.tool()
    def list_boards() -> str:
        """列出当前项目支持的所有板型（通过 `idf.py bmgr -l` 扫描 boards 目录）。

        返回 boards 列表和当前选中的板型。需先配置好项目目录。
        """
        return bridge_client.fmt(bridge_client.call("GET", "/api/boards"))

    @mcp.tool()
    def select_board(board: str) -> str:
        """选择目标板型（通过 `idf.py bmgr -b <board>`）。

        切换板型后通常需要重新 build。参数 board 可用 list_boards 查询。
        """
        return bridge_client.fmt(
            bridge_client.call("POST", "/api/boards/select", json={"board": board})
        )

    @mcp.tool()
    def build(board: Optional[str] = None) -> str:
        """编译固件（`idf.py build`）。

        参数:
            board: 可选，指定板型会先 select_board 再 build。不传则用当前板型。

        这是个耗时操作（首次编译可能几分钟到十几分钟）。工具调用会阻塞等待完成。
        编译输出会同时进入日志缓冲，可用 get_logs 查看详情。
        """
        payload = {"board": board} if board else {}
        return bridge_client.fmt(bridge_client.call("POST", "/api/build", json=payload, timeout=600.0))

    @mcp.tool()
    def flash(port: Optional[str] = None, board: Optional[str] = None) -> str:
        """烧录固件到 ESP32（`idf.py -p <port> flash`）。

        参数:
            port: 串口号，如 "COM6"。不传则用当前已打开的串口或默认 COM6。
            board: 可选，指定板型会先 select_board 再 flash。

        烧录前会自动释放串口（SerialManager 的 acquire_for_flash），烧完自动重连。
        需先 build 成功。耗时通常 10~60 秒。
        """
        payload = {}
        if port:
            payload["port"] = port
        if board:
            payload["board"] = board
        return bridge_client.fmt(bridge_client.call("POST", "/api/flash", json=payload, timeout=300.0))

    @mcp.tool()
    def clean_build() -> str:
        """清理编译产物（`idf.py fullclean`）。

        会删除 build/ 目录。下次 build 会全量重新编译。用于切换板型/IDF 版本后
        或编译状态异常时。耗时几十秒。
        """
        return bridge_client.fmt(bridge_client.call("POST", "/api/clean", timeout=300.0))
