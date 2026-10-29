"""Live2D 視窗、表情、嘴型與主執行緒繪製迴圈。"""
import json
from pathlib import Path
import queue
import threading
import traceback


class AvatarMixin:
    """Live2D 視窗、表情、嘴型與主執行緒繪製迴圈。"""

    def _init_view(self, model_path, size=(800, 900), scale=1.0, mouth_gain=6.0, background=(0.08, 0.08, 0.1, 1.0), emotion_map=None):
        self.path = Path(model_path).resolve()
        self.size, self.scale = (size, scale)
        self.background, self.mouth_gain = (background, mouth_gain)
        self.emotions = dict(emotion_map or {})
        self.commands = queue.Queue()
        self.stop = threading.Event()
        self.mouth = 0.0
        self.worker_error = None

    def _worker(self, function):
        try:
            function()
        except BaseException as exc:
            self.worker_error = exc
            traceback.print_exc()
        finally:
            self.stop.set()

    def _run_view(self, ai_function):
        """必須由主執行緒呼叫，ai_function 會在背景執行。"""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('Live2DView.run 必須在主執行緒呼叫')
        if not self.path.is_file():
            raise FileNotFoundError(f'找不到 Live2D 模型：{self.path}')
        settings = json.loads(self.path.read_text(encoding='utf-8-sig'))
        names = {item['Name'] for item in settings.get('FileReferences', {}).get('Expressions', [])}
        lips = next((g['Ids'] for g in settings.get('Groups', []) if g.get('Name') == 'LipSync' and g.get('Ids')), ['ParamMouthOpenY'])
        print('Live2D 表情：', ', '.join(sorted(names)) or '無')
        import pygame
        import live2d
        pygame.display.init()
        model, set_param = (None, None)
        initialized = False
        try:
            pygame.display.set_mode(self.size, pygame.DOUBLEBUF | pygame.OPENGL)
            pygame.display.set_caption('白璃 HAKURI')
            live2d.init()
            initialized = True
            init_gl = getattr(live2d, 'glInit', None) or getattr(live2d, 'glewInit')
            init_gl()
            model = live2d.Model()
            model.LoadModelJson(str(self.path))
            model.Resize(*self.size)
            model.SetScale(self.scale)
            model.ResetExpression()
            set_param = getattr(model, 'SetParameterValue', None) or getattr(model, 'SetParamById')
            tracking = self.live2d_settings.get('camera_tracking', {})
            eye_params = (tracking.get('eye_x_parameter', 'ParamEyeBallX'), tracking.get('eye_y_parameter', 'ParamEyeBallY'))
            param_ids = set(model.GetParamIds())
            if tracking.get('enabled', False):
                if not set(eye_params).issubset(param_ids):
                    self.post_control_event('log', '模型缺少設定中的眼球參數')
                else:
                    self.camera_thread = threading.Thread(target=self.track_camera, daemon=True)
                    self.camera_thread.start()
            threading.Thread(target=self._worker, args=(ai_function,), daemon=True).start()
            clock = pygame.time.Clock()
            smooth = 0.0
            gaze_x = gaze_y = 0.0
            expression_layers = {'emotion': set(), 'gesture': set(), 'manual': set()}
            applied_expressions = set()
            while not self.stop.is_set():
                if self.control_window is not None:
                    self.update_control_window()
                    if self.stop.is_set():
                        break
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.stop.set()
                    elif event.type == pygame.MOUSEMOTION and not tracking.get('enabled', False):
                        model.Drag(*event.pos)
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        self.stop.set()
                while True:
                    try:
                        kind, value = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if kind in expression_layers:
                        keys = [value] if isinstance(value, str) else (value or [])
                        expression_layers[kind] = {self.emotions[key] for key in keys if key in self.emotions}
                desired = set().union(*expression_layers.values()) & names
                for name in sorted(applied_expressions - desired):
                    model.RemoveExpression(name)
                for name in sorted(desired - applied_expressions):
                    model.AddExpression(name)
                applied_expressions = desired
                live2d.clearBuffer(*self.background)
                model.Update()
                if tracking.get('enabled', False):
                    alpha = min(1.0, max(0.01, float(tracking.get('smoothing', 0.15))))
                    target_x, target_y = self.gaze_target
                    gaze_x += (target_x - gaze_x) * alpha
                    gaze_y += (target_y - gaze_y) * alpha
                    for param, value in zip(eye_params, (gaze_x, gaze_y)):
                        if param in param_ids:
                            set_param(param, value)
                    self.apply_head_tracking(model, set_param, param_ids, gaze_x, gaze_y)
                smooth += (self.mouth - smooth) * 0.5
                for param in lips:
                    set_param(param, smooth)
                model.Draw()
                pygame.display.flip()
                clock.tick(60)
        except KeyboardInterrupt:
            self.stop.set()
        finally:
            self.stop.set()
            set_param = None
            if model is not None:
                del model
            if initialized:
                live2d.dispose()
            pygame.quit()
            tts = self.tts
            if tts is not None:
                tts.close()
            self.camera_stop.set()
            if self.camera_thread is not None:
                self.camera_thread.join(timeout=2)
            if self.control_window is not None:
                self.control_window.destroy()
                self.control_window = None
        if self.worker_error is not None:
            raise RuntimeError('AI 背景工作失敗，請查看上方錯誤') from self.worker_error
