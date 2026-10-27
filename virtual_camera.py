"""透過 FFmpeg DirectShow 依名稱讀取 Windows 虛擬攝影機。"""
import queue
import subprocess
import threading
from collections import deque
import time

import numpy as np


class VirtualCamera:
    def __init__(self, ffmpeg, name, width=640, height=480, fps=15, startup_timeout=15):
        self.width, self.height = width, height
        self.frames = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.errors = deque(maxlen=20)
        self.startup_timeout = startup_timeout
        self.received_frame = False
        self.process = subprocess.Popen(
            [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-f', 'dshow',
             '-rtbufsize', '64M', '-pixel_format', 'yuyv422', '-video_size', '640x360',
             '-framerate', '30', '-i', 'video=' + name, '-an', '-threads', '1',
             '-filter_threads', '1', '-vf', f'scale={width}:{height}',
             '-r', str(fps), '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.reader = threading.Thread(target=self._read_frames, daemon=True)
        self.reader.start()
        self.error_reader = threading.Thread(target=self._read_errors, daemon=True)
        self.error_reader.start()

    def _read_errors(self):
        for line in iter(self.process.stderr.readline, b''):
            self.errors.append(line.decode('utf-8', errors='replace').strip())

    @property
    def error(self):
        return '\n'.join(self.errors) or f'FFmpeg exit={self.process.poll()}；攝影機未送出影格'

    def _read_frames(self):
        size = self.width * self.height * 3
        try:
            while not self.closed.is_set():
                data = bytearray()
                while len(data) < size:
                    chunk = self.process.stdout.read(size - len(data))
                    if not chunk:
                        return
                    data.extend(chunk)
                frame = np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                self.frames.put_nowait(frame)
        finally:
            self.closed.set()

    def read(self):
        deadline = time.monotonic() + (3 if self.received_frame else self.startup_timeout)
        while time.monotonic() < deadline:
            try:
                frame = self.frames.get(timeout=0.1)
                self.received_frame = True
                return True, frame
            except queue.Empty:
                if self.closed.is_set() or self.process.poll() is not None:
                    break
        return False, None

    def release(self):
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.reader.join(timeout=1)
        self.error_reader.join(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()
