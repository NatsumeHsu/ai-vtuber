"""Shared avatar state and the loopback web presentation lifecycle."""
from pathlib import Path
import queue
import threading
import traceback

class AvatarMixin:
    def _init_view(self, model_path, size=(800, 900), scale=1.0, mouth_gain=6.0,
                   background=(0.08, 0.08, 0.1, 1.0), emotion_map=None):
        self.path = Path(model_path).resolve()
        self.size, self.scale = size, scale
        self.background, self.mouth_gain = background, mouth_gain
        self.emotions = dict(emotion_map or {})
        self.commands = queue.Queue()
        self.stop = threading.Event()
        self.mouth = 0.0
        self.worker_error = None
        self.web_view = None

    def _worker(self, function):
        try:
            function()
        except BaseException as exc:
            self.worker_error = exc
            traceback.print_exc()
        finally:
            self.stop.set()

    def _run_view(self, ai_function):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('控制介面必須由主執行緒更新')
        if not self.path.is_file():
            raise FileNotFoundError(f'找不到 Live2D 模型：{self.path}')
        from vtuber.GUI.web_avatar import WebAvatar
        self.web_view = WebAvatar(self)
        self.web_view.start()
        view = self.web_view
        self.post_control_event('log', f'角色網頁：{self.web_view.url}；OBS：{self.web_view.url}?obs=1')
        self.post_control_event('log', f'點歌台：{self.web_view.url}songs；點歌 OBS：{self.web_view.url}songs?obs=1')
        threading.Thread(target=self._worker, args=(ai_function,), daemon=True).start()
        try:
            while not self.stop.wait(0.02):
                if self.control_window is not None:
                    self.update_control_window()
                while True:
                    try:
                        kind, value = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if kind == 'emotion':
                        values = [value] if isinstance(value, str) else (value or [])
                        with view.lock:
                            view.expressions = [self.emotions[key] for key in values if key in self.emotions]
        except KeyboardInterrupt:
            self.stop.set()
        finally:
            self.stop.set()
            self.close()
        if self.worker_error is not None:
            raise RuntimeError('AI 背景工作失敗，請查看本機記錄') from self.worker_error
