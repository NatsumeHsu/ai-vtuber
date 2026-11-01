"""手部關節與中指手勢辨識，使用時間確認避免表情閃爍。"""
import time

import numpy as np


def is_middle_finger(landmarks, straight_angle=145, extension_ratio=1.05):
    points = np.array([(p.x, p.y, p.z) for p in landmarks])
    if points.shape != (21, 3):
        return False

    def straight(mcp, pip, dip, tip):
        a, b = points[mcp] - points[pip], points[tip] - points[pip]
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm < 1e-10:
            return False
        angle = np.degrees(np.arccos(np.clip(np.dot(a, b) / norm, -1, 1)))
        return angle > straight_angle and np.linalg.norm(points[tip] - points[0]) > np.linalg.norm(points[pip] - points[0]) * extension_ratio

    return straight(9, 10, 11, 12) and not any(
        straight(*finger) for finger in ((5, 6, 7, 8), (13, 14, 15, 16), (17, 18, 19, 20))
    )


class HandGestures:
    def __init__(self, model_path, config):
        import mediapipe as mp
        self.mp = mp
        self.config = config
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=float(config.get('confidence', 0.5)),
            min_hand_presence_confidence=float(config.get('confidence', 0.5)),
        )
        self.model = mp.tasks.vision.HandLandmarker.create_from_options(options)
        self.active = False
        self.pending = False
        self.changed_at = time.monotonic()
        self.timestamp = 0
        self.hand_count = 0
        self.detected = False

    def process(self, frame):
        import cv2
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self.timestamp = max(self.timestamp + 1, int(time.monotonic() * 1000))
        result = self.model.detect_for_video(image, self.timestamp)
        hands = result.hand_world_landmarks or result.hand_landmarks
        self.hand_count = len(hands)
        detected = any(is_middle_finger(hand, self.config.get('straight_angle', 145),
                                        self.config.get('extension_ratio', 1.05)) for hand in hands)
        self.detected = detected
        now = time.monotonic()
        if detected != self.pending:
            self.pending, self.changed_at = detected, now
        delay = self.config.get('confirm_seconds', 0.25) if detected else self.config.get('release_seconds', 0.4)
        if now - self.changed_at >= delay:
            self.active = detected
        return self.active

    def close(self):
        self.model.close()
