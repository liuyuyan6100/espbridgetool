"""串口管理器 — 独占管理串口连接，支持烧录时临时释放

强化特性：
- USB 插拔自动恢复：检测到设备消失后轮询等待设备回来，自动重连
- 动态端口追踪：USB 重新枚举后端口号可能变化（COM6→COM5），
  通过 VID/PID 匹配原设备，自动切换到新端口
- 快速重试：首次重连 0.5s 间隔（不浪费时间），指数退避到最大 10s
- 句柄失效主动恢复：PermissionError 立即关闭失效句柄，走重连流程
- flash 后多级退避：1.5s 初始延迟 + 8 次重试，应对 USB 重新枚举
"""

import time
import threading
import logging
import os
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
        # 设备指纹：用于 USB 重新枚举后动态追踪端口变化
        self._device_hwid: Optional[str] = None  # USB 硬件 ID（含 VID/PID）
        self._device_vid: Optional[int] = None
        self._device_pid: Optional[int] = None

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
        """打开串口，启动后台读取线程

        即使打开失败也会启动后台读取线程，进入自动重连模式。
        这样启动时设备未插入，后续插入后能自动恢复连接。
        """
        with self._lock:
            if self.is_open:
                logger.warning("串口已打开，先关闭再重新打开")
                self._close_unlocked()

            success = False
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
                # 记录设备指纹，用于插拔后动态追踪
                self._record_device_fingerprint(port)
                logger.info(f"串口已打开: {port} @ {baud}")
                success = True
            except (serial.SerialException, OSError) as e:
                logger.error(f"打开串口失败 {port}: {e}")
                self._ser = None
                # 即使打开失败也记录目标端口和波特率，用于后台自动重连
                self._port = port
                self._baud = baud
                self._closed_manually = False  # 允许自动重连

        # 无论成功还是失败都启动读取线程：
        # 成功 → 正常读取数据
        # 失败 → 进入自动重连流程，等待设备插入
        self._start_reader()
        return success

    def _record_device_fingerprint(self, port: str) -> None:
        """记录设备的 USB 硬件指纹（VID/PID/hwid），用于插拔后追踪。

        USB 重新枚举后端口号可能从 COM6 变成 COM5，但 VID/PID 不变。
        凭这个指纹可以在重连时找到新端口。
        """
        try:
            info = serial.tools.list_ports.LoopbackProtocol  # 占位避免 unused
        except AttributeError:
            pass
        try:
            ports = serial.tools.list_ports.comports()
            for p in ports:
                if p.device == port:
                    self._device_hwid = p.hwid
                    # 从 hwid 解析 VID/PID，格式如 "USB VID:PID=303A:1001 ..."
                    if hasattr(p, 'vid') and p.vid is not None:
                        self._device_vid = p.vid
                        self._device_pid = p.pid
                    elif p.hwid and 'VID:PID=' in p.hwid:
                        import re
                        m = re.search(r'VID:PID=([0-9A-Fa-f]+):([0-9A-Fa-f]+)', p.hwid)
                        if m:
                            self._device_vid = int(m.group(1), 16)
                            self._device_pid = int(m.group(2), 16)
                    logger.info(
                        f"设备指纹: {port} hwid={self._device_hwid} "
                        f"VID={self._device_vid:#06x} PID={self._device_pid:#06x}"
                    )
                    return
        except Exception as e:
            logger.warning(f"记录设备指纹失败: {e}")

    def _find_device_port(self) -> Optional[str]:
        """通过设备指纹查找当前端口。

        USB 重新枚举后端口可能变化，凭 VID/PID 或 hwid 找到新端口。
        如果没有指纹信息（启动时设备未插入），尝试找任何 USB 串口设备。
        找不到返回 None。
        """
        try:
            ports = serial.tools.list_ports.comports()
            # 有指纹：优先按 VID/PID 或 hwid 匹配
            if self._device_hwid or self._device_vid is not None:
                for p in ports:
                    # 优先 VID/PID 匹配
                    if self._device_vid is not None and hasattr(p, 'vid') and p.vid is not None:
                        if p.vid == self._device_vid and p.pid == self._device_pid:
                            if p.device != self._port:
                                logger.info(
                                    f"设备端口变化: {self._port} → {p.device} "
                                    f"(VID/PID 匹配)"
                                )
                            return p.device
                    # 退回 hwid 匹配
                    if self._device_hwid and p.hwid == self._device_hwid:
                        if p.device != self._port:
                            logger.info(
                                f"设备端口变化: {self._port} → {p.device} "
                                f"(hwid 匹配)"
                            )
                        return p.device
                # 有指纹但没匹配到，设备可能还没插入
                return None

            # 无指纹（启动时设备未插入）：先看原端口是否存在
            for p in ports:
                if p.device == self._port:
                    return p.device

            # 原端口不存在，找任何 USB 串口设备（排除蓝牙等非 USB 设备）
            for p in ports:
                hwid = p.hwid or ""
                desc = p.description or ""
                if 'USB' in hwid or 'JTAG' in desc or 'CH340' in desc \
                        or 'CP210' in desc or 'CH343' in desc \
                        or 'USB' in desc:
                    logger.info(
                        f"无指纹模式：发现 USB 串口设备 {p.device} "
                        f"({desc})"
                    )
                    return p.device
        except Exception as e:
            logger.warning(f"查找设备端口失败: {e}")
        return None

    def _find_alternate_port(self, exclude_port: str) -> Optional[str]:
        """找一个备用的 USB 串口端口（排除指定端口和蓝牙端口）。

        用于 CH340 端口打不开时尝试 ESP32-S3 原生 USB 口等其他端口。
        """
        try:
            ports = serial.tools.list_ports.comports()
            for p in ports:
                if p.device == exclude_port:
                    continue
                hwid = p.hwid or ""
                desc = p.description or ""
                # 排除蓝牙虚拟串口
                if 'BTHENUM' in hwid or '蓝牙' in desc:
                    continue
                # 只考虑 USB 串口设备
                if 'USB' in hwid or 'CH340' in desc or 'CP210' in desc \
                        or 'JTAG' in desc or 'USB' in desc:
                    return p.device
        except Exception as e:
            logger.warning(f"查找备用端口失败: {e}")
        return None

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
        """后台线程：持续读取串口数据，支持 USB 插拔自动恢复"""
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
                        # 快速首次重试（0.5s），指数退避到最大 10s
                        delay = min(0.5 * (2 ** (retry_count - 1)), 10)
                        logger.warning(
                            f"串口断开，{delay:.1f}s 后重试 (第 {retry_count} 次)"
                        )
                        time.sleep(delay)
                        # 动态追踪设备端口（USB 重新枚举后端口可能变化）
                        new_port = self._find_device_port()
                        if new_port is None:
                            # 设备完全消失，等它插回来
                            logger.info("等待 USB 设备插入...")
                            time.sleep(2)
                            continue
                        # 连续失败超过 5 次，尝试其他可用 USB 串口
                        # （CH340 可能在 boot loop 时一直 OSError 22，
                        #  但 ESP32-S3 原生 USB 口可能可用）
                        if retry_count >= 5 and new_port == self._port:
                            alt_port = self._find_alternate_port(self._port)
                            if alt_port:
                                logger.info(
                                    f"连续失败 {retry_count} 次，"
                                    f"尝试备用端口: {alt_port}"
                                )
                                new_port = alt_port
                        self.open(new_port, self._baud)
                    else:
                        time.sleep(0.5)
                time.sleep(0.01)
            except serial.SerialException as e:
                # PermissionError(拒绝访问) 通常是句柄失效（flash 后 / USB 断开）
                # 继续读同一个失效句柄会无限报错，主动关闭触发重连
                if "PermissionError" in str(e) or "拒绝访问" in str(e):
                    logger.error(f"串口句柄失效，关闭后等待重连: {e}")
                    with self._lock:
                        self._close_unlocked()
                    # 短暂等待 USB 驱动释放，然后走重连流程
                    time.sleep(1)
                elif "FileNotFoundError" in str(e) or "系统找不到" in str(e):
                    # 设备已拔出，走重连流程等它回来
                    logger.warning(f"串口设备已断开: {e}")
                    with self._lock:
                        self._close_unlocked()
                    time.sleep(1)
                else:
                    logger.error(f"串口读取异常: {e}")
                    time.sleep(1)
            except OSError as e:
                # OSError(22, 函数不正确) 也是句柄失效的表现
                if e.errno in (22, 13) or "函数不正确" in str(e) or "拒绝访问" in str(e):
                    logger.error(f"串口句柄失效(errno={e.errno})，关闭后等待重连: {e}")
                    with self._lock:
                        self._close_unlocked()
                    time.sleep(1)
                else:
                    logger.error(f"串口 OSError: {e}")
                    time.sleep(1)
            except Exception as e:
                logger.error(f"读取线程异常: {e}", exc_info=True)
                time.sleep(1)

    # ---- 烧录上下文 ----

    @contextmanager
    def acquire_for_flash(self):
        """上下文管理器：释放串口 → 烧录 → 重新打开（带重试 + 端口追踪）

        idf.py flash 结束后，Windows COM 口句柄可能尚未完全释放，
        且 USB 可能重新枚举导致端口号变化。这里加延迟 + 多次重试 +
        动态端口追踪，确保烧录后能恢复连接。
        """
        saved_port = self._port
        saved_baud = self._baud
        self._stop_reader()
        with self._lock:
            self._close_unlocked()
        try:
            yield
        finally:
            if saved_port:
                # 给 Windows / USB 驱动时间完全释放句柄
                time.sleep(1.5)
                # 重试打开，最多 8 次，应对 USB 重新枚举
                for attempt in range(1, 9):
                    # 动态追踪设备端口（USB 重新枚举后端口可能变化）
                    target_port = self._find_device_port() or saved_port
                    if self.open(target_port, saved_baud):
                        break
                    delay = min(1.0 * attempt, 5.0)  # 退避递增，最大 5s
                    logger.warning(
                        f"flash 后串口重开失败 (第 {attempt}/8 次)，{delay}s 后重试"
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"flash 后串口 {saved_port} 重开 8 次均失败，需手动重连"
                    )

    # ---- 工具方法 ----

    @staticmethod
    def list_ports() -> list:
        """列出可用串口"""
        ports = serial.tools.list_ports.comports()
        return [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in sorted(ports)
        ]