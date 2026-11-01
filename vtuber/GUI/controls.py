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
        window.title('白璃｜Discord 機器人控制')
        window.geometry('680x780')
        window.minsize(480, 400)
        window.protocol('WM_DELETE_WINDOW', self.hide_gui)
        window.bind('<Unmap>', lambda event: self.hide_gui() if event.widget is window and window.state() == 'iconic' else None)
        window.withdraw()
        self.start_notify_icon()
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        self.control_status = tk.StringVar(value='Discord 常駐，AI 尚未啟動')
        self.control_speaker = tk.StringVar(value=self.speaker_name)
        ttk.Label(frame, text='Discord 機器人控制', font=('', 16, 'bold')).pack(anchor='w')
        ttk.Label(frame, textvariable=self.control_status).pack(anchor='w', pady=(8, 12))
        services = ttk.LabelFrame(frame, text='子程序控制', padding=6)
        services.pack(fill='x', pady=(0, 8))
        service_labels = [('twitch', 'Twitch'), ('text', 'textAI'), ('speech', 'voiceAI')]
        if not hasattr(self, 'host_peer'):
            service_labels.insert(0, ('discord', 'Discord'))
        for kind, label in service_labels:
            service_row = ttk.Frame(services)
            service_row.pack(fill='x')
            ttk.Label(service_row, text=label, width=12).pack(side='left')
            for action, title in (('stop', '停止'), ('start', '啟動'), ('restart', '重啟')):
                ttk.Button(service_row, text=title,
                           command=lambda k=kind, a=action: self.control_service(k, a)).pack(side='left', padx=3)
        channels = ttk.LabelFrame(frame, text='頻道設定（Discord 僅本次，Twitch 會儲存）', padding=6)
        channels.pack(fill='x', pady=(0, 8))
        self.discord_channel_input = tk.StringVar(value='')
        self.twitch_channel_input = tk.StringVar(value=self.settings.get('twitch', {}).get('channel', ''))
        for kind, title, variable in (('discord', 'Discord 語音頻道 ID', self.discord_channel_input),
                                      ('twitch', 'Twitch 頻道名稱', self.twitch_channel_input)):
            channel_row = ttk.Frame(channels)
            channel_row.pack(fill='x', pady=2)
            ttk.Label(channel_row, text=title, width=22).pack(side='left')
            ttk.Entry(channel_row, textvariable=variable).pack(side='left', fill='x', expand=True)
            ttk.Button(channel_row, text='更新', command=lambda k=kind, v=variable:
                       self.update_channel(k, v.get())).pack(side='left', padx=4)
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
        ttk.Button(row, text='開啟角色網頁', command=self.open_avatar_page).pack(side='left', padx=8)
        ttk.Button(row, text='點歌台', command=self.open_song_page).pack(side='left', padx=4)
        ttk.Button(row, text='重新載入設定', command=self.reload_settings_from_ui).pack(side='left', padx=8)
        ttk.Button(row, text='結束程式', command=lambda: self.post_control_event('exit', None)).pack(side='right')
        self.set_control_ready(False)

    def reload_settings_from_ui(self):
        import threading
        def reload():
            try:
                if not hasattr(self, 'host_peer'):
                    raise RuntimeError('請透過 vtuberAI.py 啟動主服務')
                if not self.host_peer.call('reload_settings'):
                    self.post_control_event('log', '已有重新載入工作或服務正在關閉。')
            except Exception as exc:
                self.post_control_event('error', f'重新載入失敗：{type(exc).__name__}')
        threading.Thread(target=reload, daemon=True).start()

    def update_channel(self, kind, value):
        import threading
        def work():
            try:
                if not hasattr(self, 'host_peer'):
                    raise RuntimeError('請透過 vtuberAI.py 啟動 Discord 主服務')
                updated = self.host_peer.call(kind + '_channel', value)
                if kind == 'twitch':
                    self.settings['twitch']['channel'] = updated
                    service = getattr(self, 'services', {}).get('twitch')
                    if service and service.ready:
                        self.control_service('twitch', 'restart')
                self.post_control_event('log', f'{kind} 頻道已更新：{updated}')
            except Exception as exc:
                self.post_control_event('error', str(exc))
        threading.Thread(target=work, daemon=True).start()

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
            if kind == 'discord_channel':
                self.discord_channel_input.set(value)
            elif kind == 'status':
                self.control_status.set(value)
                if not value.startswith('錄音中'):
                    self.finish_button.configure(state='disabled')
                    self.cancel_button.configure(state='disabled')
            elif kind == 'show_gui':
                self.control_window.deiconify()
                self.control_window.lift()
            elif kind == 'exit':
                self.host_peer.call('shutdown_host') if hasattr(self, 'host_peer') else self.stop.set()
            elif kind in ('ready', 'done'):
                self.set_control_ready(True)
                self.control_status.set('控制介面已就緒，請按需啟動 textAI／voiceAI／Twitch')
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

    def hide_gui(self):
        self.control_window.withdraw()

    def open_avatar_page(self):
        import webbrowser
        if self.web_view is not None:
            webbrowser.open(self.web_view.url)
        else:
            self.post_control_event('log', '角色網頁正在準備，請稍後再開啟。')

    def open_song_page(self):
        import webbrowser
        if self.web_view is not None:
            webbrowser.open(self.web_view.url + 'songs')

    def start_notify_icon(self):
        import pystray
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (64, 64), '#24344b')
        ImageDraw.Draw(image).ellipse((12, 12, 52, 52), fill='#a9dfff')
        self.tray_icon = pystray.Icon('ai-vtuber', image, '白璃｜Discord 常駐', pystray.Menu(
            pystray.MenuItem('開啟控制介面', lambda *args: self.post_control_event('show_gui', None), default=True),
            pystray.MenuItem('開啟角色網頁', lambda *args: self.open_avatar_page()),
            pystray.MenuItem('開啟點歌台', lambda *args: self.open_song_page()),
            pystray.MenuItem('結束程式（含 Discord）', lambda *args: self.post_control_event('exit', None))))
        import threading
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
