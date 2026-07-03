"""串口管理器 — 独占管理串口连接，支持烧录时临时释放"""

import time
import threading
import logging
from contextlib import contextmanager
from typing import Callable, Optional

import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)


class SerialManager:
    """独占管理串口连接，支持烧录时临时释放"""

    def __init__(self, on_data: Callable[[bytes], None] = None):
        self._port: Optional[str] = None
        self._baud: int = 115200
        self._ser: Optional[serial.Serial] = None
        self._reading = False
        self._reader_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._closed_manually = False
        # 支持多个数据回调（日志 + 终端同时收数据）
        self._on_data_callbacks: list[Callable[[bytes], None]] = []
        if on_data:
            self._on_data_callbacks.append(on_data)

    def add_callback(self, cb: Callable[[bytes], None]) -> None:
        """注册数据回调"""
        if cb not in self._on_data_callbacks:
            self._on_data_callbacks.append(cb)

    def remove_callback(self, cb: Callable[[bytes], None]) -> None:
        """移除数据回调"""
        if cb in self._on_data_callbacks:
            self._on_data_callbacks.remove(cb)

    # ---- 属性 ----

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def port(self) -> Optional[str]:
        return self._port

    @property
    def baud(self) -> int:
        return self._baud

    @property
    def status_text(self) -> str:
        if self.is_open:
            return f"已连接 {self._port}@{self._baud}"
        return "未连接"

    # ---- 打开 / 关闭 ----

    def open(self, port: str, baud: int = 115200) -> bool:
        """打开串口，启动后台读取线程"""
        with self._lock:
            if self.is_open:
                logger.warning("串口已打开，先关闭再重新打开")
                self._close_unlocked()

            try:
                self._ser = serial.Serial(
                    port=port,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.05,  # 短超时，读取线程能及时退出
                )
                self._port = port
                self._baud = baud
                self._closed_manually = False
                logger.info(f"串口已打开: {port} @ {baud}")
            except serial.SerialException as e:
                logger.error(f"打开串口失败 {port}: {e}")
                self._ser = None
                return False

        self._start_reader()
        return True

    def close(self) -> None:
        """关闭串口（人工关闭，不自动重连）"""
        self._closed_manually = True
        self._stop_reader()
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        """内部关闭串口（已持有锁）"""
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
                logger.info(f"串口已关闭: {self._port}")
            except serial.SerialException as e:
                logger.warning(f"关闭串口异常: {e}")
        self._ser = None

    # ---- 发送 ----

    def send(self, data: bytes) -> int:
        """发送原始字节，返回发送字节数"""
        with self._lock:
            if not self.is_open:
                raise RuntimeError("串口未打开")
            return self._ser.write(data)

    def send_line(self, line: str) -> int:
        """发送一行文本（自动追加 \\r\\n）"""
        data = (line.strip() + "\r\n").encode("utf-8")
        return self.send(data)

    # ---- 后台读取 ----

    def _start_reader(self) -> None:
        if self._reading:
            return
        self._reading = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _stop_reader(self) -> None:
        self._reading = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)

    def _read_loop(self) -> None:
        """后台线程：持续读取串口数据"""
        retry_count = 0
        while self._reading:
            try:
                if self.is_open:
                    data = self._ser.read(1024)
                    if data:
                        for cb in self._on_data_callbacks:
                            try:
                                cb(data)
                            except Exception as e:
                                logger.warning(f"串口数据回调异常: {e}")
                    retry_count = 0  # 成功读取，重置重试计数
                else:
                    # 串口断开，尝试自动重连
                    if not self._closed_manually and self._port:
                        retry_count += 1
                        delay = min(2 ** retry_count, 30)  # 指数退避，最大 30s
                        logger.warning(
                            f"串口断开，{delay}s 后重试 (第 {retry_count} 次)"
                        )
                        time.sleep(delay)
                        self.open(self._port, self._baud)
                    else:
                        time.sleep(0.5)
                time.sleep(0.01)
            except serial.SerialException as e:
                logger.error(f"串口读取异常: {e}")
                time.sleep(1)
            except Exception as e:
                logger.error(f"读取线程异常: {e}", exc_info=True)
                time.sleep(1)

    # ---- 烧录上下文 ----

    @contextmanager
    def acquire_for_flash(self):
        """上下文管理器：释放串口 → 烧录 → 重新打开"""
        saved_port = self._port
        saved_baud = self._baud
        self._stop_reader()
        with self._lock:
            self._close_unlocked()
        try:
            yield
        finally:
            if saved_port:
                self.open(saved_port, saved_baud)

    # ---- 工具方法 ----

    @staticmethod
    def list_ports() -> list:
        """列出可用串口"""
        ports = serial.tools.list_ports.comports()
        return [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in sorted(ports)
        ]