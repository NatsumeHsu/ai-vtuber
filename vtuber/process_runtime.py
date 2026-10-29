"""Spawned services with bidirectional RPC over private OS pipes."""
import itertools
import multiprocessing as mp
import queue
import threading
from pathlib import Path


class Peer:
    def __init__(self, connection, handler):
        self.connection, self.handler = connection, handler
        self.lock = threading.Lock()
        self.pending = {}
        self.sequence = itertools.count()
        self.closed = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, packet):
        with self.lock:
            self.connection.send(packet)

    def call(self, method, *args, **kwargs):
        ident = next(self.sequence)
        result = queue.Queue(1)
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError('子程序已停止')
            self.pending[ident] = result
        try:
            self.send(('call', ident, method, args, kwargs))
            while True:
                try:
                    ok, value = result.get(timeout=0.2)
                    if not ok:
                        raise RuntimeError(value)
                    return value
                except queue.Empty:
                    if self.closed.is_set():
                        raise RuntimeError('子程序連線中斷')
        finally:
            with self.lock:
                self.pending.pop(ident, None)

    def _dispatch(self, ident, method, args, kwargs):
        try:
            value = self.handler(method, *args, **kwargs)
            packet = ('result', ident, True, value)
        except Exception as exc:
            packet = ('result', ident, False, f'{type(exc).__name__}: {exc}')
        try:
            self.send(packet)
        except (OSError, EOFError):
            pass

    def _read(self):
        try:
            while True:
                packet = self.connection.recv()
                if packet[0] == 'call':
                    threading.Thread(target=self._dispatch, args=packet[1:], daemon=True).start()
                else:
                    _, ident, ok, value = packet
                    with self.lock:
                        target = self.pending.get(ident)
                    if target:
                        target.put((ok, value))
        except (EOFError, OSError):
            self.closed.set()

    def close(self):
        self.closed.set()
        self.connection.close()


def service_main(connection, kind, root, settings_path):
    import os
    import yaml
    os.chdir(root)
    settings = yaml.safe_load(Path(settings_path).read_text(encoding='utf-8'))
    shutdown = threading.Event()
    initialized = threading.Event()
    resources = []
    handler = None
    error = None

    def dispatch(method, *args, **kwargs):
        if method == 'shutdown':
            shutdown.set()
            return None
        if method == 'ready':
            initialized.wait()
            if error:
                raise RuntimeError(error)
            return os.getpid()
        if error:
            raise RuntimeError(error)
        return handler(method, *args, **kwargs)

    # Initialize before serving requests; pipe buffers startup requests.
    try:
        if kind == 'text':
            from .runtime_support import prepare_runtime
            prepare_runtime()
            from llama_cpp import Llama, llama_supports_gpu_offload
            config = settings['llm']
            if config['n_gpu_layers'] != 0 and not llama_supports_gpu_offload():
                raise RuntimeError('llama-cpp-python 沒有 GPU 加速支援')
            model = Llama(model_path=str(Path(root) / config['model_path']),
                          n_gpu_layers=config['n_gpu_layers'], n_ctx=config['n_ctx'], verbose=config['verbose'])
            resources.append(model)
            model_lock = threading.Lock()
            def handler(method, *args, **kwargs):
                if method not in ('tokenize', 'create_chat_completion'):
                    raise ValueError(method)
                with model_lock:
                    return getattr(model, method)(*args, **kwargs)
        elif kind == 'speech':
            from .index_tts_client import IndexTTSClient
            model = IndexTTSClient(root, settings['tts'])
            resources.append(model)
            def handler(method, *args, **kwargs):
                if method != 'synthesize':
                    raise ValueError(method)
                return model.synthesize(*args, **kwargs)
        elif kind in ('discord', 'twitch'):
            discord = None
            # Start after Peer exists so callbacks can reach the main process.
            def handler(method, *args, **kwargs):
                if kind != 'discord' or method != 'play_audio' or discord is None:
                    raise RuntimeError('Discord 語音未啟用')
                return discord.play_audio(*args, on_level=lambda level: peer.call('mouth', level), **kwargs)
        else:
            raise ValueError(kind)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    peer = Peer(connection, dispatch)
    try:
        if kind in ('discord', 'twitch') and not error:
            try:
                if kind == 'twitch' and settings.get('twitch', {}).get('enabled', False):
                    from .twitch_chat import TwitchChat
                    twitch = TwitchChat(settings['twitch'],
                        lambda *a: peer.call('twitch_message', *a),
                        lambda text: peer.call('log', text),
                        on_observation=lambda *a: peer.call('observe', *a))
                    resources.append(twitch)
                    twitch.start()
                if kind == 'discord' and settings.get('discord', {}).get('enabled', False):
                    from .discord_voice import DiscordVoiceService
                    discord = DiscordVoiceService(settings['discord'],
                        lambda samples: peer.call('transcribe', samples),
                        lambda *a: peer.call('discord_message', *a),
                        lambda text: peer.call('log', text))
                    resources.append(discord)
                    discord.start()
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
                peer.call('log', f'{kind} 啟動失敗：{exc}')
        initialized.set()
        while not shutdown.wait(0.2) and not peer.closed.is_set():
            pass
    finally:
        for resource in reversed(resources):
            resource.close()
        peer.close()


class ServiceProcess:
    def __init__(self, kind, root, settings_path, callback):
        self.kind, self.root, self.settings_path = kind, root, settings_path
        self.callback = callback
        self.lifecycle_lock = threading.Lock()
        self.control_lock = threading.Lock()
        self.process = self.peer = None

    def start(self):
        with self.lifecycle_lock:
            if self.process and self.process.is_alive():
                return
            if self.peer:
                self.peer.close()
            if self.process:
                self.process.join()
                self.process.close()
            context = mp.get_context('spawn')
            parent, child = context.Pipe()
            self.process = context.Process(target=service_main,
                args=(child, self.kind, self.root, self.settings_path), name='vtuber-' + self.kind)
            self.process.start()
            child.close()
            self.peer = Peer(parent, self.callback)

    def call(self, method, *args, **kwargs):
        peer = self.peer
        if peer is None:
            raise RuntimeError(f'{self.kind} 子程序尚未啟動')
        return peer.call(method, *args, **kwargs)

    def close(self):
        with self.lifecycle_lock:
            process, peer = self.process, self.peer
            self.peer = self.process = None
            if peer:
                try:
                    peer.send(('call', -1, 'shutdown', (), {}))
                except (OSError, EOFError):
                    pass
            if process:
                process.join(2)
                if process.is_alive():
                    process.terminate()
                    process.join(3)
                if process.is_alive():
                    process.kill()
                    process.join()
                process.close()
            if peer:
                peer.close()


class ModelProxy:
    def __init__(self, service):
        self.service = service

    def tokenize(self, *args, **kwargs):
        return self.service.call('tokenize', *args, **kwargs)

    def create_chat_completion(self, **kwargs):
        return self.service.call('create_chat_completion', **kwargs)

    def synthesize(self, *args, **kwargs):
        return self.service.call('synthesize', *args, **kwargs)

    def close(self):
        self.service.close()


class DiscordProxy:
    def __init__(self, service):
        self.service = service

    def play_audio(self, audio, sample_rate, on_level):
        return self.service.call('play_audio', audio, sample_rate)

    def close(self):
        self.service.close()
