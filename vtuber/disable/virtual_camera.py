"""透過 OpenCV 在程式內擷取 DirectShow 攝影機。"""
import cv2
from cv2_enumerate_cameras import enumerate_cameras


class VirtualCamera:
    def __init__(self, name, width=640, height=360, fps=30, pixel_format='YUY2'):
        if isinstance(name, str) and not name.isdecimal():
            matches = [camera for camera in enumerate_cameras(cv2.CAP_DSHOW) if camera.name == name]
            if len(matches) != 1:
                raise RuntimeError(f'攝影機名稱必須符合唯一裝置：{name}，符合 {len(matches)} 個')
            index = matches[0].index
        else:
            index = int(name)
        self.capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        self.error = ''
        if not self.capture.isOpened():
            self.release()
            raise RuntimeError(f'無法開啟攝影機：{name}')
        self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*pixel_format))
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)

    def read(self):
        ok, frame = self.capture.read()
        if not ok:
            self.error = 'OpenCV DirectShow 未取得影像'
        return ok, frame

    def release(self):
        self.capture.release()
