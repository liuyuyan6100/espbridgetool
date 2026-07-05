"""日志落盘模块 — 把串口/IDF 日志按会话写入文件。

设计要点
--------
- 会话级文件：每次服务启动（或手动 rotate）开一个新文件，文件名含时间戳，
  避免单文件无限增长，也便于按调试会话回溯。
- 线程安全：串口读取线程、idf.py 子进程回调线程都会并发写入，用 threading.Lock 保护。
- 元数据头：每个会话文件开头写一段结构化头（启动时间、串口、波特率、IDF 配置），
  方便事后回看当时的环境。
- 失败不阻断：写文件异常只记日志，绝不抛回串口读取线程（日志落盘是辅助，不能影响主链路）。
- 可开关：通过 enabled 属性控制，配置 false 时 write() 直接 return。

文件布局
--------
    <LOG_SINK_DIR>/
      session_20260704_082510.log      # 每次启动一个
      session_20260704_153022.log
      ...

每行格式：[HH:MM:SS.mmm] <原始日志内容>
（保留 ANSI 颜色码，便于事后用支持 ANSI 的工具还原颜色查看）
"""

import os
import secrets
import threading
from datetime import datetime
from typing import Optional


class LogSink:
    """把日志行写入会话文件，线程安全。"""

    def __init__(
        self,
        sink_dir: str,
        enabled: bool = True,
        prefix: str = "session",
    ):
        """
        Args:
            sink_dir: 落盘目录（绝对路径）。不存在会自动创建。
            enabled: 是否启用。False 时 write() 直接返回。
            prefix: 文件名前缀，默认 "session"。
        """
        self._enabled = enabled
        self._sink_dir = sink_dir
        self._prefix = prefix
        self._lock = threading.Lock()
        self._fp = None
        self._current_path: Optional[str] = None
        self._start_time: Optional[datetime] = None

        if enabled:
            try:
                os.makedirs(sink_dir, exist_ok=True)
            except OSError as e:
                # 目录创建失败不抛，写入时会再次尝试/记日志
                import logging
                logging.getLogger(__name__).warning(
                    f"LogSink 目录创建失败 {sink_dir}: {e}；落盘将禁用"
                )
                self._enabled = False

    # ---- 属性 ----

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        with self._lock:
            if value and not self._enabled:
                self._enabled = True
                # 下次 open_session 时重建
            elif not value and self._enabled:
                self._close_unlocked()
                self._enabled = False

    @property
    def current_path(self) -> Optional[str]:
        return self._current_path

    @property
    def start_time(self) -> Optional[datetime]:
        return self._start_time

    # ---- 会话管理 ----

    def open_session(self, meta: Optional[dict] = None) -> Optional[str]:
        """开启一个新的会话文件，写入元数据头。

        Args:
            meta: 写入文件头的环境信息（如 port/baud/idf 配置）。

        Returns:
            会话文件绝对路径；未启用时返回 None。
        """
        if not self._enabled:
            return None
        with self._lock:
            self._close_unlocked()
            self._start_time = datetime.now()
            ts = self._start_time.strftime("%Y%m%d_%H%M%S_") + f"{self._start_time.microsecond // 1000:03d}"
            suffix = secrets.token_hex(2)  # 4 位十六进制，避免同毫秒轮转撞名
            fname = f"{self._prefix}_{ts}_{suffix}.log"
            path = os.path.join(self._sink_dir, fname)
            try:
                self._fp = open(path, "w", encoding="utf-8", buffering=1)  # 行缓冲
                self._current_path = path
                # 写元数据头
                self._fp.write(f"=== Serial Bridge 会话日志 ===\n")
                self._fp.write(f"启动时间: {self._start_time.isoformat()}\n")
                if meta:
                    for k, v in meta.items():
                        self._fp.write(f"{k}: {v}\n")
                self._fp.write(f"=" * 40 + "\n")
                return path
            except OSError as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"LogSink 会话文件创建失败 {path}: {e}；落盘禁用"
                )
                self._enabled = False
                self._fp = None
                self._current_path = None
                return None

    def rotate(self, meta: Optional[dict] = None) -> Optional[str]:
        """轮转：关闭当前文件，开新会话文件。等同 open_session。"""
        return self.open_session(meta=meta)

    # ---- 写入 ----

    def write(self, line: str) -> None:
        """写入一行日志（自动加时间戳前缀）。

        异常绝不抛出——落盘是辅助功能，不能影响串口读取主链路。
        """
        if not self._enabled or self._fp is None:
            return
        ts = datetime.now().strftime("%H:%M:%S.") + f"{datetime.now().microsecond // 1000:03d}"
        try:
            with self._lock:
                if self._fp is None:
                    return
                self._fp.write(f"[{ts}] {line}\n")
        except Exception:
            # 任何写入异常都吞掉，最多记一条日志
            pass

    def write_raw(self, text: str) -> None:
        """写入原始文本（不加时间戳前缀，用于多行块）。

        注意：text 末尾应自带换行；本方法不追加。
        """
        if not self._enabled or self._fp is None:
            return
        try:
            with self._lock:
                if self._fp is None:
                    return
                self._fp.write(text)
        except Exception:
            pass

    # ---- 关闭 ----

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._fp:
            try:
                self._fp.flush()
                self._fp.close()
            except Exception:
                pass
            self._fp = None

    # ---- 查询 ----

    def list_sessions(self) -> list:
        """列出落盘目录下所有会话文件（按修改时间倒序）。"""
        if not os.path.isdir(self._sink_dir):
            return []
        try:
            files = [
                os.path.join(self._sink_dir, f)
                for f in os.listdir(self._sink_dir)
                if f.startswith(self._prefix) and f.endswith(".log")
            ]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return files
        except OSError:
            return []
