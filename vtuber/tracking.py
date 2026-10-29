"""攝影機、人臉視線、頭部與手勢追蹤。"""
import time


class TrackingMixin:
    """攝影機、人臉視線、頭部與手勢追蹤。"""

    def face_to_gaze(self, face, width, height):
        """將人臉中心轉成 Live2D 眼球座標，垂直方向上方為正。"""
        x, y, w, h = face
        config = self.live2d_settings.get('camera_tracking', {})
        gx = 2 * (x + w / 2) / width - 1
        gy = 1 - 2 * (y + h / 2) / height
        if config.get('mirror', True):
            gx = -gx
        gain = float(config.get('gain', 1.5))
        return (max(-1.0, min(1.0, gx * gain)), max(-1.0, min(1.0, gy * gain)))

    def apply_head_tracking(self, model, set_param, param_ids, x, y):
        """以平滑後的人臉位置驅動轉頭，並限制在模型參數範圍內。"""
        config = self.live2d_settings.get('camera_tracking', {})
        if not config.get('head_enabled', False):
            return
        targets = (
            (config.get('head_x_parameter', 'ParamAngleX'), x * float(config.get('head_yaw', 25))),
            (config.get('head_y_parameter', 'ParamAngleY'), y * float(config.get('head_pitch', 15))),
        )
        for param, value in targets:
            if param in param_ids:
                value = max(model.GetParamMinById(param), min(model.GetParamMaxById(param), value))
                set_param(param, value)

    def track_camera(self):
        """影像僅在本機記憶體做人臉定位，不保存或傳送影像。"""
        capture = None
        gestures = None
        gesture_active = False
        gesture_status = None
        try:
            import cv2
            config = self.live2d_settings.get('camera_tracking', {})
            gesture_config = config.get('gestures', {})
            if gesture_config.get('enabled', False):
                try:
                    from .hand_gestures import HandGestures
                    gestures = HandGestures(self.project_path(gesture_config['model_path']), gesture_config)
                    self.post_control_event('gesture_status', '手勢：已啟動，請讓整隻手進入鏡頭')
                except Exception as exc:
                    self.post_control_event('log', f'手勢辨識無法啟動：{exc}')
                    self.post_control_event('gesture_status', f'手勢：無法啟動，{exc}')
            detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            if detector.empty():
                raise RuntimeError('無法載入人臉偵測模型')
            device = config.get('device', 0)
            from .virtual_camera import VirtualCamera
            def open_camera():
                return VirtualCamera(device, width=int(config.get('width', 640)),
                                     height=int(config.get('height', 360)),
                                     fps=max(1, float(config.get('fps', 30))),
                                     pixel_format=config.get('pixel_format', 'YUY2'))
            capture = open_camera()
            self.post_control_event('camera', '眼睛追蹤：等待人臉')
            last_seen = time.monotonic()
            found_before = False
            interval = 1 / max(1, float(config.get('fps', 15)))
            reconnects = 0
            while not self.stop.is_set() and not self.camera_stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    if self.stop.is_set() or self.camera_stop.is_set():
                        break
                    self.gaze_target = (0.0, 0.0)
                    error = capture.error
                    capture.release()
                    if reconnects < int(config.get('reconnect_attempts', 3)):
                        reconnects += 1
                        self.post_control_event('camera', f'眼睛追蹤：重新連線 {reconnects}，{error}')
                        if self.camera_stop.wait(1) or self.stop.is_set():
                            break
                        capture = open_camera()
                        continue
                    raise RuntimeError(f'攝影機重連失敗：{error}')
                height, width = frame.shape[:2]
                if gestures is not None:
                    try:
                        active = gestures.process(frame)
                        status = (gestures.hand_count, gestures.detected, active)
                        if status != gesture_status:
                            gesture_status = status
                            if active:
                                message = '手勢：已觸發中指'
                            elif gestures.detected:
                                message = '手勢：偵測到中指，保持一下'
                            elif gestures.hand_count:
                                message = f'手勢：看到 {gestures.hand_count} 隻手，尚未符合中指手勢'
                            else:
                                message = '手勢：沒有偵測到手，請讓整隻手進入鏡頭'
                            self.post_control_event('gesture_status', message)
                        if active != gesture_active:
                            gesture_active = active
                            key = gesture_config.get('middle_finger_expression', 'middle_finger')
                            self.commands.put(('gesture', [key] if active else []))
                    except Exception as exc:
                        self.post_control_event('log', f'手勢辨識停止：{exc}')
                        self.post_control_event('gesture_status', f'手勢：辨識停止，{exc}')
                        gestures.close()
                        gestures = None
                        self.commands.put(('gesture', []))
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
                found = len(faces) > 0
                if found:
                    self.gaze_target = self.face_to_gaze(max(faces, key=lambda box: box[2] * box[3]), width, height)
                    last_seen = time.monotonic()
                elif time.monotonic() - last_seen >= float(config.get('lost_seconds', 1.0)):
                    self.gaze_target = (0.0, 0.0)
                if found != found_before:
                    self.post_control_event('camera', '眼睛追蹤：追蹤中' if found else '眼睛追蹤：等待人臉')
                    found_before = found
                self.camera_stop.wait(interval)
        except Exception as exc:
            print(f'攝影機錯誤：{exc}', flush=True)
            self.post_control_event('camera', f'眼睛追蹤停止：{exc}')
        finally:
            self.gaze_target = (0.0, 0.0)
            self.commands.put(('gesture', []))
            if gestures is not None:
                gestures.close()
            if capture is not None:
                capture.release()
