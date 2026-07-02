"""idf.py 命令封装 — 调用 ESP-IDF 编译/烧录命令，捕获输出

基于立创·实战派 ESP32-S3 编译指导文档实现，支持 Board Manager 工作流。
参考文档: docs/lckfb-szpi-esp32s3-configuration-guide.md
"""

import os
import re
import subprocess
import logging
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ESP-IDF 导出脚本路径（按实际安装路径修改）
DEFAULT_IDF_EXPORT_SCRIPT = r"C:\esp\v5.5.4\esp-idf\export.ps1"

# 默认开发板（立创·实战派 ESP32-S3）
DEFAULT_BOARD = "lckfb_szpi_esp32s3"


class IdfTool:
    """封装 idf.py 命令，捕获输出推送到日志流

    支持 Board Manager 工作流：
      - bmgr -c <boards_dir> -l         列出可用板型
      - bmgr -c <boards_dir> -b <board> 选择板型
      - build                            编译
      - -p <port> flash                  烧录
      - menuconfig                       配置
      - fullclean                        清理
    """

    def __init__(
        self,
        project_dir: str,
        export_script: str = DEFAULT_IDF_EXPORT_SCRIPT,
        boards_dir: Optional[str] = None,
        board: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            project_dir: ESP-IDF 项目目录（含 CMakeLists.txt）
            export_script: ESP-IDF export.ps1 路径
            boards_dir: Board Manager 的 boards 目录路径（相对 project_dir 或绝对）
            board: 默认板型名称（如 lckfb_szpi_esp32s3）
            on_output: 输出回调（每行文本），用于推送到 WebSocket
        """
        self.project_dir = os.path.abspath(project_dir)
        self.export_script = export_script
        self.boards_dir = boards_dir or "boards"
        self.board = board or DEFAULT_BOARD
        self._on_output = on_output

    def _resolve_boards_dir(self) -> str:
        """解析 boards_dir 为绝对路径"""
        if os.path.isabs(self.boards_dir):
            return self.boards_dir
        return os.path.join(self.project_dir, self.boards_dir)

    def _run_cmd(
        self,
        cmd: list,
        timeout: Optional[int] = None,
        env_extra: Optional[dict] = None,
    ) -> Tuple[bool, str]:
        """运行 idf.py 命令，捕获输出

        Args:
            cmd: idf.py 的参数列表（如 ["bmgr", "-c", "boards", "-l"]）
            timeout: 超时秒数
            env_extra: 额外环境变量
        """
        # 构造 PowerShell 命令：用分号连接，避免 & 被 shell 误解析
        export_escaped = self.export_script.replace("'", "''")
        idf_args = " ".join(cmd)

        # 用脚本块包裹，确保环境变量在同一个作用域内生效
        ps_script = (
            f"$env:PYTHONUTF8='1'; "
            f"$env:PYTHONIOENCODING='utf-8'; "
            f"$env:ESP_BMGR_LOCK_TIMEOUT='120'; "
            f". '{export_escaped}'; "
            f"idf.py {idf_args}"
        )

        # 合并环境变量（Python 层面也设一份，防 subprocess 继承问题）
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["ESP_BMGR_LOCK_TIMEOUT"] = "120"
        if env_extra:
            env.update(env_extra)

        logger.info(f"运行 idf.py 命令: {' '.join(cmd)} (cwd={self.project_dir})")

        ps_command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ]

        try:
            process = subprocess.Popen(
                cwd=self.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

            full_output = []
            for line in iter(process.stdout.readline, ""):
                line = line.rstrip("\n")
                full_output.append(line)
                if self._on_output:
                    self._on_output(line)
                logger.debug(f"[idf.py] {line}")

            process.stdout.close()
            retcode = process.wait(timeout=timeout)
            output = "\n".join(full_output)
            success = retcode == 0
            logger.info(f"idf.py 命令完成: success={success}, retcode={retcode}")
            return success, output

        except subprocess.TimeoutExpired:
            process.kill()
            logger.error("idf.py 命令超时")
            return False, "命令执行超时"
        except FileNotFoundError as e:
            logger.error(f"找不到 PowerShell: {e}")
            return False, f"执行环境错误: {e}"
        except Exception as e:
            logger.error(f"idf.py 执行异常: {e}", exc_info=True)
            return False, str(e)

    def list_boards(self) -> Tuple[bool, List[str]]:
        """列出可用板型 (bmgr -c <boards_dir> -l)

        Returns:
            (success, boards_list)
        """
        boards_dir = self._resolve_boards_dir()
        cmd = ["bmgr", "-c", boards_dir, "-l"]
        ok, output = self._run_cmd(cmd)
        if not ok:
            return False, []

        # 从输出中解析板型名称
        boards = []
        for line in output.split("\n"):
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            # 匹配板型名称行（通常是缩进的板名）
            # 格式可能是 "- lckfb_szpi_esp32s3" 或直接 "lckfb_szpi_esp32s3"
            cleaned = re.sub(r"^[\-\*\s]+", "", line)
            if cleaned and re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", cleaned):
                boards.append(cleaned)

        return ok, boards

    def select_board(self, board: Optional[str] = None) -> Tuple[bool, str]:
        """选择板型 (bmgr -c <boards_dir> -b <board>)

        Args:
            board: 板型名称，为空则使用 self.board
        """
        target_board = board or self.board
        boards_dir = self._resolve_boards_dir()
        cmd = ["bmgr", "-c", boards_dir, "-b", target_board]
        ok, output = self._run_cmd(cmd)
        if ok:
            self.board = target_board
            logger.info(f"板型已选择: {target_board}")
        return ok, output

    def build(self, board: Optional[str] = None) -> Tuple[bool, str]:
        """编译固件

        Args:
            board: 如果指定，会先 select_board 再 build
        """
        if board and board != self.board:
            ok, msg = self.select_board(board)
            if not ok:
                return False, f"选择板型失败: {msg}"

        return self._run_cmd(["build"])

    def flash(self, port: str, board: Optional[str] = None) -> Tuple[bool, str]:
        """烧录固件（需先释放串口）

        Args:
            port: 串口号（如 COM6）
            board: 如果指定，会先 select_board 再 flash
        """
        if board and board != self.board:
            ok, msg = self.select_board(board)
            if not ok:
                return False, f"选择板型失败: {msg}"

        cmd = ["-p", port, "flash"]
        return self._run_cmd(cmd)

    def flash_monitor(self, port: str, board: Optional[str] = None) -> Tuple[bool, str]:
        """烧录并打开监视器

        注意：monitor 会占用串口，退出后 SerialManager 才能重新连接。
        退出监视器的快捷键是 Ctrl+]
        """
        if board and board != self.board:
            ok, msg = self.select_board(board)
            if not ok:
                return False, f"选择板型失败: {msg}"

        cmd = ["-p", port, "flash", "monitor"]
        return self._run_cmd(cmd)

    def menuconfig(self) -> Tuple[bool, str]:
        """打开 menuconfig 配置菜单

        注意：这是一个交互式终端程序，需要 TTY 支持。
        在 Web 环境下可能无法正常工作，建议在终端中直接运行。
        """
        return self._run_cmd(["menuconfig"])

    def monitor(self, port: str) -> None:
        """monitor — 由 SerialManager 接管，此方法仅用于参考"""
        logger.info(f"monitor 功能由 SerialManager 接管，端口: {port}")

    def fullclean(self) -> Tuple[bool, str]:
        """清理编译产物"""
        return self._run_cmd(["fullclean"])

    def bmgr(self, board: Optional[str] = None) -> Tuple[bool, str]:
        """运行 bmgr 选择板型（兼容旧接口）

        Args:
            board: 板型名称，为空则使用 self.board
        """
        return self.select_board(board)

    def fullclean_and_rebuild(self, board: Optional[str] = None) -> Tuple[bool, str]:
        """清理后重新选择板型并编译

        用于切换过开发板、ESP-IDF 版本或依赖后的重建。
        """
        ok, output = self.fullclean()
        if not ok:
            return False, f"清理失败: {output}"

        ok, output = self.select_board(board)
        if not ok:
            return False, f"选择板型失败: {output}"

        return self.build()

    @staticmethod
    def parse_flash_progress(line: str) -> Optional[dict]:
        """解析烧录进度（esptool 输出）"""
        m = re.search(r"Writing at 0x([0-9a-f]+)\.\.\. \((\d+)%\)", line)
        if m:
            return {"address": m.group(1), "percent": int(m.group(2))}
        m = re.search(r"Writing at 0x([0-9a-f]+)", line)
        if m:
            return {"address": m.group(1), "percent": None}
        return None

    @staticmethod
    def parse_bmgr_lock_error(output: str) -> Optional[str]:
        """检测 Board Manager 锁等待错误

        Returns:
            锁文件路径（如果存在），否则 None
        """
        if "Waiting for board manager bootstrap lock" in output:
            m = re.search(r"([\w\\\/:.]+\.lock)", output)
            if m:
                return m.group(1)
            return "lock_file_not_found_in_message"
        return None
