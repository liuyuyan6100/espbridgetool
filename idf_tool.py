"""idf.py 命令封装 — 调用 ESP-IDF 编译/烧录命令，捕获输出

基于立创·实战派 ESP32-S3 编译指导文档实现，支持 Board Manager 工作流。
参考文档: docs/lckfb-szpi-esp32s3-configuration-guide.md
"""

import os
import re
import subprocess
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ESP-IDF 导出脚本路径（按实际安装路径修改）
DEFAULT_IDF_EXPORT_SCRIPT = r"C:\esp\v5.5.4\esp-idf\export.ps1"

# 默认开发板（立创·实战派 ESP32-S3）
DEFAULT_BOARD = "lckfb_szpi_esp32s3"


@dataclass
class FlashProgress:
    """烧录进度状态（线程安全）"""
    active: bool = False
    phase: str = ""          # "building" / "flashing" / "done" / "error" / ""
    percent: int = 0         # 0-100
    address: str = ""        # 当前烧录地址（如 0x00008000）
    message: str = ""        # 人类可读状态消息
    total_partitions: int = 0  # 总分区数
    written_partitions: int = 0  # 已完成分区数
    started_at: Optional[float] = None  # 开始时间戳
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reset(self, phase: str = "flashing") -> None:
        with self._lock:
            self.active = True
            self.phase = phase
            self.percent = 0
            self.address = ""
            self.message = "开始烧录"
            self.total_partitions = 0
            self.written_partitions = 0
            import time
            self.started_at = time.time()

    def update(self, percent: int = -1, address: str = "", message: str = "",
               phase: str = "", inc_partition: bool = False) -> None:
        with self._lock:
            if percent >= 0:
                self.percent = percent
            if address:
                self.address = address
            if message:
                self.message = message
            if phase:
                self.phase = phase
            if inc_partition:
                self.written_partitions += 1

    def finish(self, success: bool) -> None:
        with self._lock:
            self.active = False
            self.phase = "done" if success else "error"
            self.percent = 100 if success else self.percent
            self.message = "烧录完成" if success else "烧录失败"

    def to_dict(self) -> dict:
        with self._lock:
            import time
            elapsed = 0
            if self.started_at:
                elapsed = round(time.time() - self.started_at, 1)
            return {
                "active": self.active,
                "phase": self.phase,
                "percent": self.percent,
                "address": self.address,
                "message": self.message,
                "total_partitions": self.total_partitions,
                "written_partitions": self.written_partitions,
                "elapsed": elapsed,
            }


# esptool 进度行正则：Writing at 0x00008000... (12 %)
_PROGRESS_RE = re.compile(r'Writing at 0x([0-9A-Fa-f]+)\.\.\.\s*\((\d+)\s*%\)')
# 分区验证行：Hash of data verified.
_VERIFY_RE = re.compile(r'Hash of data verified')
# 连接行：Connecting.....
_CONNECT_RE = re.compile(r'Connecting')
# 硬复位行：Hard resetting via RTS pin...
_RESET_RE = re.compile(r'Hard resetting')


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
        # 烧录进度状态（外部可读，通过 to_dict() 序列化）
        self.flash_progress = FlashProgress()

    def _resolve_boards_dir(self) -> str:
        """解析 boards_dir 为绝对路径"""
        if os.path.isabs(self.boards_dir):
            return self.boards_dir
        return os.path.join(self.project_dir, self.boards_dir)

    def _detect_python_env(self) -> Optional[str]:
        """自动检测 ESP-IDF 的 Python 虚拟环境路径。

        export.ps1 会按系统 Python 版本去找 C:\\Espressif\\python_env\\idf5.5_py3.X_env，
        但系统 Python 版本可能和 IDF 安装时的版本不一致（比如 IDF 装时是 3.13，
        后来系统降级或装了多个 Python）。这里直接扫目录，按 export_script 路径里
        的 IDF 版本号匹配对应环境，绕过 export.ps1 的版本检测。

        Returns:
            Python 环境目录绝对路径，找不到返回 None。
        """
        # 从 export_script 路径提取 IDF 版本号（如 v5.5.4 → 5.5）
        idf_ver = None
        m = re.search(r'[v/](\d+\.\d+)', self.export_script)
        if m:
            idf_ver = m.group(1)  # "5.5"

        candidates = [
            r"C:\Espressif\python_env",
            os.path.join(os.path.dirname(os.path.dirname(self.export_script)), "python_env"),
        ]
        for base in candidates:
            if not os.path.isdir(base):
                continue
            # 找 idf*_py*_env 目录
            dirs = [d for d in os.listdir(base)
                    if d.startswith("idf") and d.endswith("_env") and "_py" in d]
            # 优先匹配 IDF 版本号（如 idf5.5_py3.13_env）
            if idf_ver:
                prefix = f"idf{idf_ver}_"
                matched = [d for d in dirs if d.startswith(prefix)]
                if matched:
                    for name in matched:
                        path = os.path.join(base, name)
                        if os.path.exists(os.path.join(path, "Scripts", "python.exe")):
                            return path
            # 版本号没匹配上，取最后一个（版本号最大的，sorted 后最后一个）
            for name in sorted(dirs, reverse=True):
                path = os.path.join(base, name)
                if os.path.exists(os.path.join(path, "Scripts", "python.exe")):
                    return path
        return None

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

        # 自动检测 IDF Python 环境路径，绕过 export.ps1 的版本检测
        # （系统 Python 版本可能和 IDF 安装时的版本不一致）
        py_env = self._detect_python_env()
        py_env_clause = ""
        if py_env:
            py_env_escaped = py_env.replace("'", "''")
            py_env_clause = f"$env:IDF_PYTHON_ENV_PATH='{py_env_escaped}'; "

        # 用脚本块包裹，确保环境变量在同一个作用域内生效
        ps_script = (
            f"$env:PYTHONUTF8='1'; "
            f"$env:PYTHONIOENCODING='utf-8'; "
            f"$env:ESP_BMGR_LOCK_TIMEOUT='120'; "
            f"{py_env_clause}"
            f". '{export_escaped}'; "
            f"idf.py {idf_args}"
        )

        # 合并环境变量（Python 层面也设一份，防 subprocess 继承问题）
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["ESP_BMGR_LOCK_TIMEOUT"] = "120"
        if py_env:
            env["IDF_PYTHON_ENV_PATH"] = py_env
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
                ps_command,
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
                # 解析烧录进度
                self._parse_flash_progress(line)
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

        self.flash_progress.reset(phase="flashing")
        cmd = ["-p", port, "flash"]
        success, output = self._run_cmd(cmd)
        self.flash_progress.finish(success)
        return success, output

    def _parse_flash_progress(self, line: str) -> None:
        """从 idf.py / esptool 输出行解析烧录进度。

        esptool 输出格式：
            Connecting....
            Chip is ESP32-S3 (Firmware rev: v0.2)
            Writing at 0x00008000... (12 %)
            Hash of data verified.
            Hard resetting via RTS pin...
        """
        # 写入进度：Writing at 0x00008000... (12 %)
        m = _PROGRESS_RE.search(line)
        if m:
            addr = "0x" + m.group(1).upper()
            pct = int(m.group(2))
            self.flash_progress.update(
                percent=pct, address=addr,
                message=f"写入 {addr} ({pct}%)",
            )
            return

        # 分区验证完成
        if _VERIFY_RE.search(line):
            self.flash_progress.update(
                inc_partition=True,
                message=f"分区校验完成 (已写 {self.flash_progress.written_partitions + 1} 个分区)",
            )
            return

        # 连接阶段
        if _CONNECT_RE.search(line):
            self.flash_progress.update(
                phase="connecting", percent=0,
                message="连接 ESP32 芯片中...",
            )
            return

        # 硬复位（烧录完成的标志）
        if _RESET_RE.search(line):
            self.flash_progress.update(
                phase="resetting", percent=100,
                message="烧录完成，重启设备中...",
            )
            return

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
