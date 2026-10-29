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

    def initialize_models(self):
        from .runtime_support import prepare_runtime
        prepare_runtime()
        from .index_tts_client import IndexTTSClient
        from llama_cpp import Llama, llama_supports_gpu_offload
        if self.n_gpu_layers != 0 and not llama_supports_gpu_offload():
            raise RuntimeError('目前的 llama-cpp-python 沒有 GPU 加速支援')
        if not self.project_path(self.gguf_model).is_file():
            raise FileNotFoundError(self.project_path(self.gguf_model))
        needs_asr = self.asr_settings['enabled'] or self.settings.get('discord', {}).get('enabled', False)
        self.recognizer, self.converter = self.init_asr() if needs_asr else (None, None)
        if self.stop.is_set():
            return
        print('載入 Index-TTS……')
        self.tts = IndexTTSClient(self.root, self.tts_settings)
        if self.stop.is_set():
            self.tts.close()
            return
        print('載入 GGUF……')
        self.llm = Llama(model_path=str(self.project_path(self.gguf_model)), n_gpu_layers=self.n_gpu_layers, n_ctx=self.n_ctx, verbose=self.llm_settings['verbose'])
        self.output = self.project_path(self.output_path)
        self.output.parent.mkdir(parents=True, exist_ok=True)

    def close(self):
        self.stop.set()
        if self.discord is not None:
            self.discord.close()
            self.discord = None
        if self.twitch is not None:
            self.twitch.close()
            self.twitch = None
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
            if self.settings.get('twitch', {}).get('enabled', False) and not self.stop.is_set():
                from .twitch_chat import TwitchChat
                try:
                    self.twitch = TwitchChat(self.settings['twitch'], self.enqueue_twitch_message,
                                             lambda text: self.post_control_event('log', text),
                                             on_observation=self.observe_twitch_message)
                    self.twitch.start()
                except ValueError as exc:
                    self.post_control_event('log', str(exc))
            if self.settings.get('discord', {}).get('enabled', False) and not self.stop.is_set():
                from .discord_voice import DiscordVoiceService
                try:
                    self.discord = DiscordVoiceService(self.settings['discord'], self.recognize_samples,
                        self.enqueue_discord_message, lambda text: self.post_control_event('log', text))
                    self.discord.start()
                except (ValueError, ImportError) as exc:
                    self.post_control_event('log', str(exc))
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
