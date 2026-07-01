"""idf.py 命令封装 — 调用 ESP-IDF 编译/烧录命令，捕获输出"""

import os
import re
import subprocess
import logging
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# ESP-IDF 导出脚本路径（按实际安装路径修改）
DEFAULT_IDF_EXPORT_SCRIPT = r"C:\esp\v5.5.4\esp-idf\export.ps1"

# 常用的 ESP32-S3 开发板
DEFAULT_BOARD = "lckfb_szpi_esp32s3"


class IdfTool:
    """封装 idf.py 命令，捕获输出推送到日志流"""

    def __init__(
        self,
        project_dir: str,
        export_script: str = DEFAULT_IDF_EXPORT_SCRIPT,
        on_output: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            project_dir: ESP-IDF 项目目录（含 CMakeLists.txt）
            export_script: ESP-IDF export.ps1 路径
            on_output: 输出回调（每行文本），用于推送到 WebSocket
        """
        self.project_dir = os.path.abspath(project_dir)
        self.export_script = export_script
        self._on_output = on_output

    def _run_cmd(
        self, cmd: list, timeout: Optional[int] = None
    ) -> Tuple[bool, str]:
        """运行 idf.py 命令，捕获输出"""
        # 构造 PowerShell 命令：先 source export.ps1 再执行
        export_cmd = f". '{self.export_script}'"
        idf_cmd = " & ".join([export_cmd, "idf.py " + " ".join(cmd)])
        ps_command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            idf_cmd,
        ]

        logger.info(f"运行 idf.py 命令: {' '.join(cmd)} (cwd={self.project_dir})")

        try:
            process = subprocess.Popen(
                ps_command,
                cwd=self.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
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

    def build(self, board: Optional[str] = None) -> Tuple[bool, str]:
        """编译固件"""
        cmd = ["build"]
        if board:
            cmd.extend(["-D", f"CONFIG_BOARD={board}"])
        return self._run_cmd(cmd)

    def flash(self, port: str, board: Optional[str] = None) -> Tuple[bool, str]:
        """烧录固件（需先释放串口）"""
        cmd = ["-p", port, "flash"]
        if board:
            cmd.extend(["-D", f"CONFIG_BOARD={board}"])
        return self._run_cmd(cmd)

    def monitor(self, port: str) -> None:
        """monitor — 由 SerialManager 接管，此方法仅用于参考"""
        logger.info(f"monitor 功能由 SerialManager 接管，端口: {port}")

    def fullclean(self) -> Tuple[bool, str]:
        """清理编译产物"""
        return self._run_cmd(["fullclean"])

    def bmgr(self, board: Optional[str] = None) -> Tuple[bool, str]:
        """运行 bmgr（自定义烧录管理器）"""
        cmd = ["bmgr"]
        if board:
            cmd.extend(["-D", f"CONFIG_BOARD={board}"])
        return self._run_cmd(cmd)

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