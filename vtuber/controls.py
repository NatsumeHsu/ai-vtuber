"""控制視窗、介面事件與手動輸入。"""
import queue


class ControlsMixin:
    """控制視窗、介面事件與手動輸入。"""

    def post_control_event(self, kind, value):
        """背景工作只傳送事件，由主執行緒更新 Tk 元件。"""
        # 控制視窗與原本的終端並存，對話及狀態同步輸出到終端。
        if kind in ('status', 'log', 'error') and value:
            print(value, flush=True)
        self.control_events.put((kind, value))

    def create_control_window(self):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        window = self.control_window = tk.Tk()
        window.title('白璃｜麥克風對話控制')
        window.geometry('620x640')
        window.minsize(480, 400)
        window.protocol('WM_DELETE_WINDOW', self.stop.set)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        self.control_status = tk.StringVar(value='載入模型中……')
        self.control_speaker = tk.StringVar(value=self.speaker_name)
        ttk.Label(frame, text='麥克風對話控制', font=('', 16, 'bold')).pack(anchor='w')
        ttk.Label(frame, textvariable=self.control_status).pack(anchor='w', pady=(8, 12))
        services = ttk.LabelFrame(frame, text='子程序控制', padding=6)
        services.pack(fill='x', pady=(0, 8))
        for kind, label in (('discord', 'Discord'), ('twitch', 'Twitch'), ('text', '文字生成'), ('speech', '語音生成')):
            service_row = ttk.Frame(services)
            service_row.pack(fill='x')
            ttk.Label(service_row, text=label, width=12).pack(side='left')
            for action, title in (('stop', '停止'), ('start', '啟動'), ('restart', '重啟')):
                ttk.Button(service_row, text=title,
                           command=lambda k=kind, a=action: self.control_service(k, a)).pack(side='left', padx=3)
        self.camera_status = tk.StringVar(value='眼睛追蹤：啟動中' if self.live2d_settings.get('camera_tracking', {}).get('enabled', False) else '眼睛追蹤：未啟用')
        ttk.Label(frame, textvariable=self.camera_status).pack(anchor='w', pady=(0, 8))
        self.gesture_status = tk.StringVar(value='手勢：等待攝影機啟動')
        ttk.Label(frame, textvariable=self.gesture_status).pack(anchor='w')
        self.gesture_test_button = ttk.Button(frame, text='測試中指：關', command=self.toggle_middle_finger_test)
        self.gesture_test_button.pack(anchor='w', pady=(4, 8))
        row = ttk.Frame(frame)
        row.pack(fill='x')
        ttk.Label(row, text='發言者名稱').pack(side='left')
        self.speaker_entry = ttk.Entry(row, textvariable=self.control_speaker)
        self.speaker_entry.pack(side='left', fill='x', expand=True, padx=(10, 0))
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=12)
        self.record_button = ttk.Button(row, text='開始錄音', command=lambda: self.submit_control_job('microphone'))
        self.record_button.pack(side='left')
        self.finish_button = ttk.Button(row, text='結束錄音並送出', command=self.recording_finished.set, state='disabled')
        self.finish_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(row, text='取消錄音', command=self.recording_cancelled.set, state='disabled')
        self.cancel_button.pack(side='left')
        ttk.Label(frame, text='說完停頓後自動送出；播放回覆時不收音。').pack(anchor='w')
        self.control_log = ScrolledText(frame, height=14, wrap='word', state='disabled')
        self.control_log.pack(fill='both', expand=True, pady=12)
        self.control_text = tk.StringVar()
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.text_entry = ttk.Entry(row, textvariable=self.control_text)
        self.text_entry.pack(side='left', fill='x', expand=True)
        self.text_entry.bind('<Return>', lambda event: self.submit_control_job('text'))
        self.send_button = ttk.Button(row, text='傳送文字', command=lambda: self.submit_control_job('text'))
        self.send_button.pack(side='left', padx=(8, 0))
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(12, 0))
        self.clear_button = ttk.Button(row, text='開始新對話', command=lambda: self.submit_control_job('clear'))
        self.clear_button.pack(side='left')
        ttk.Button(row, text='結束程式', command=self.stop.set).pack(side='right')
        self.set_control_ready(False)

    def toggle_middle_finger_test(self):
        self.manual_middle_finger = not self.manual_middle_finger
        self.commands.put(('manual', ['middle_finger'] if self.manual_middle_finger else []))
        self.gesture_test_button.configure(text='測試中指：開' if self.manual_middle_finger else '測試中指：關')

    def set_control_ready(self, ready):
        self.control_busy = not ready
        for widget in (self.record_button, self.send_button, self.clear_button,
                       self.speaker_entry, self.text_entry):
            widget.configure(state='normal' if ready else 'disabled')
        if not self.asr_settings['enabled']:
            self.record_button.configure(state='disabled')
        self.finish_button.configure(state='disabled')
        self.cancel_button.configure(state='disabled')

    def submit_control_job(self, kind):
        if self.control_busy or self.stop.is_set():
            return
        speaker = self.control_speaker.get().strip()
        if not speaker:
            self.control_status.set('請先填入發言者名稱')
            return
        text = self.control_text.get().strip() if kind == 'text' else ''
        if kind == 'text' and not text:
            return
        self.recording_cancelled.clear()
        self.recording_finished.clear()
        if not self.schedule_conversation(kind, speaker, text):
            return
        self.set_control_ready(False)
        if kind == 'microphone':
            self.finish_button.configure(state='normal')
            self.cancel_button.configure(state='normal')
        if kind == 'text':
            self.control_text.set('')
        self.control_status.set('處理中……')

    def update_control_window(self):
        while True:
            try:
                kind, value = self.control_events.get_nowait()
            except queue.Empty:
                break
            if kind == 'status':
                self.control_status.set(value)
                if not value.startswith('錄音中'):
                    self.finish_button.configure(state='disabled')
                    self.cancel_button.configure(state='disabled')
            elif kind == 'camera':
                self.camera_status.set(value)
            elif kind == 'gesture_status':
                self.gesture_status.set(value)
            elif kind in ('ready', 'done'):
                self.set_control_ready(True)
                self.control_status.set('準備完成，可以開始錄音或輸入文字')
            elif kind == 'busy':
                self.set_control_ready(False)
            elif kind in ('log', 'error'):
                self.control_log.configure(state='normal')
                self.control_log.insert('end', value + '\n\n')
                self.control_log.see('end')
                self.control_log.configure(state='disabled')
                if kind == 'error':
                    self.control_status.set('處理失敗，請查看下方訊息')
        self.control_window.update_idletasks()
        self.control_window.update()
