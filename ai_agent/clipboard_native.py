"""
跨平台剪贴板监听 —— pyperclip 轮询，0.5s 间隔。

v4.2 跨平台改造：从 Windows API（AddClipboardFormatListener）替换为
pyperclip 轮询方案。pyperclip 底层自动适配：
    - Windows → ctypes 调 OpenClipboard/GetClipboardData
    - macOS   → subprocess 调 pbcopy/pbpaste（系统自带）
    - Linux   → subprocess 调 xclip 或 xsel

0.5s 延迟在剪贴板同步场景中可忽略（网络延迟通常 > 100ms）。
"""
import asyncio
import threading
import time

import pyperclip


class NativeClipboardListener:
    """跨平台剪贴板监听器：后台线程轮询 pyperclip，变化时异步回调。"""

    def __init__(self, broadcast_callback):
        self._callback = broadcast_callback
        self._last_text = ""
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self):
        """启动后台轮询线程。"""
        self._loop = asyncio.get_running_loop()
        self._running = True
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        """后台轮询循环：每 0.5s 读取剪贴板，变化时推送到事件循环。"""
        while self._running:
            try:
                current = pyperclip.paste() or ""
            except Exception:
                time.sleep(0.5)
                continue

            if current and current != self._last_text:
                self._last_text = current
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._callback({
                            "type": "clipboard_sync",
                            "content": current,
                            "timestamp": time.time(),
                        }),
                        self._loop,
                    )
            time.sleep(0.5)

    def set_clipboard(self, text: str):
        """写入剪贴板。先记录 last_text 防止回环触发。"""
        self._last_text = text
        try:
            pyperclip.copy(text)
        except Exception:
            pass

    def stop(self):
        """停止轮询。"""
        self._running = False
