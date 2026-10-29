"""應用程式組裝、共用狀態、模型載入與資源生命週期。"""
from pathlib import Path
import queue
import threading
from .config import SettingsMixin
from .storage import StorageMixin
from .speech import SpeechMixin
from .avatar import AvatarMixin
from .tracking import TrackingMixin
from .controls import ControlsMixin
from .conversation import ConversationMixin


class VtuberAI(SettingsMixin, StorageMixin, SpeechMixin, AvatarMixin, TrackingMixin, ControlsMixin, ConversationMixin):
    """應用程式組裝、共用狀態、模型載入與資源生命週期。"""

    def __init__(self, settings_path=None):
        self.root = Path(__file__).resolve().parents[1]
        default_settings = self.root / 'setting.yml'
        self.settings_path = default_settings if settings_path is None else Path(settings_path).resolve()
        self.twitch = None
        self.discord = None
        self.reply_destination = 'local'
        self.llm = None
        self.tts = None
        self.recognizer = None
        self.converter = None
        self.history = []
        self.settings = self.load_settings()
        self.llm_settings = self.settings['llm']
        self.chat_settings = self.settings['chat']
        self.tts_settings = self.settings['tts']
        self.output_settings = self.settings['output']
        self.asr_settings = self.settings['asr']
        self.live2d_settings = self.settings['live2d']
        self.gguf_model = self.llm_settings['model_path']
        self.n_gpu_layers = self.llm_settings['n_gpu_layers']
        self.n_ctx = self.llm_settings['n_ctx']
        self.max_tokens = self.llm_settings['max_tokens']
        self.temperature = self.llm_settings['temperature']
        self.system_prompt = self.chat_settings['system_prompt']
        self.history_turns = self.chat_settings['history_turns']
        self.auto_play = self.output_settings['auto_play']
        self.output_path = self.output_settings['path']
        self.speaker_name = self.chat_settings['speaker_name']
        self._init_view(self.project_path(self.live2d_settings['model_path']), size=(800, 900), scale=1.0, mouth_gain=6.0, emotion_map=self.live2d_settings['emotion_map'])
        self.database_path = self.project_path(self.chat_settings['database_path'])
        self.init_database()
        from .audience_memory import AudienceMemory
        self.audience_memory = AudienceMemory(self.database_path, self.settings.get('audience_memory', {}))
        self.control_window = None
        self.control_events = queue.Queue()
        self.init_conversation_jobs()
        self.recording_cancelled = threading.Event()
        self.recording_finished = threading.Event()
        self.control_busy = False
        self.camera_stop = threading.Event()
        self.camera_thread = None
        self.gaze_target = (0.0, 0.0)
        self.manual_middle_finger = False
        self._close_lock = threading.Lock()

    def initialize_models(self):
        from .runtime_support import prepare_runtime
        prepare_runtime()
        from .process_runtime import ServiceProcess, ModelProxy, DiscordProxy
        self.services = {
            kind: ServiceProcess(kind, self.root, self.settings_path, self.service_callback)
            for kind in (('text', 'speech', 'twitch') if hasattr(self, 'host_peer')
                         else ('text', 'speech', 'discord', 'twitch'))}
        for kind in ('text', 'speech'):
            if self.stop.is_set():
                return
            self.services[kind].start()
            self.services[kind].call('ready')
        self.llm = ModelProxy(self.services['text'])
        self.tts = ModelProxy(self.services['speech'])
        self.recognizer, self.converter = self.init_asr()
        if self.stop.is_set():
            return
        for kind in (('twitch',) if hasattr(self, 'host_peer') else ('discord', 'twitch')):
            if self.stop.is_set():
                return
            self.services[kind].start()
            try:
                self.services[kind].call('ready')
            except RuntimeError as exc:
                self.post_control_event('error', str(exc))
        if not hasattr(self, 'host_peer'):
            self.discord = DiscordProxy(self.services['discord'])
        self.output = self.project_path(self.output_path)
        self.output.parent.mkdir(parents=True, exist_ok=True)

    def service_callback(self, method, *args, **kwargs):
        callbacks = {
            'observe': self.observe_twitch_message,
            'twitch_message': self.enqueue_twitch_message,
            'discord_message': self.enqueue_discord_message,
            'transcribe': self.recognize_samples,
            'log': lambda text: self.post_control_event('log', text),
            'mouth': lambda level: setattr(self, 'mouth', min(1.0, level * self.mouth_gain)),
        }
        if self.stop.is_set():
            return None
        return callbacks[method](*args, **kwargs)

    def control_service(self, kind, action):
        if not hasattr(self, 'services') or self.stop.is_set():
            self.post_control_event('error', '子程序尚未準備完成')
            return
        def work():
            try:
                service = self.services[kind]
                with service.control_lock:
                    if action in ('stop', 'restart'):
                        service.close()
                    if action in ('start', 'restart') and not self.stop.is_set():
                        service.start()
                        pid = service.call('ready')
                        fresh = self.load_settings()
                        if kind == 'text':
                            self.system_prompt = fresh['chat']['system_prompt']
                            self.temperature = fresh['llm']['temperature']
                            self.max_tokens = fresh['llm']['max_tokens']
                            self.n_ctx = fresh['llm']['n_ctx']
                            self.history_turns = fresh['chat']['history_turns']
                        elif kind == 'speech':
                            self.tts_settings = fresh['tts']
                        elif kind == 'discord':
                            from .process_runtime import DiscordProxy
                            self.settings['discord'] = fresh.get('discord', {})
                            self.discord = DiscordProxy(service)
                        elif kind == 'twitch':
                            self.settings['twitch'] = fresh.get('twitch', {})
                        self.post_control_event('log', f'{kind} 已啟動，PID {pid}')
                    else:
                        self.post_control_event('log', f'{kind} 已停止，可修改程式後啟動')
            except Exception as exc:
                self.post_control_event('error', str(exc))
        threading.Thread(target=work, daemon=True).start()

    def close(self):
        with self._close_lock:
            self._close_resources()

    def _close_resources(self):
        self.stop.set()
        if self.discord is not None:
            self.discord.close()
            self.discord = None
        if self.twitch is not None:
            self.twitch.close()
            self.twitch = None
        for service in getattr(self, 'services', {}).values():
            service.close()
        with self.model_lock:
            tts, self.tts = self.tts, None
            try:
                if tts is not None:
                    tts.close()
            finally:
                if self.llm is not None:
                    self.llm.close()
                    self.llm = None
                self.recognizer = None
                self.converter = None

    def _ai_worker(self):
        try:
            self.post_control_event('status', '載入模型中……')
            self.initialize_models()
            if not self.stop.is_set():
                self.audience_memory.analyze_startup(self.llm, self.n_ctx, self.stop,
                                                     lambda text: self.post_control_event('status', text))
            self.post_control_event('ready', None)
            self.conversation_loop()
        except Exception as exc:
            self.post_control_event('error', str(exc))
            raise
        finally:
            self.close()

    def run(self):
        self.create_control_window()
        try:
            self._run_view(self._ai_worker)
        finally:
            if self.control_window is not None:
                self.control_window.destroy()
                self.control_window = None
