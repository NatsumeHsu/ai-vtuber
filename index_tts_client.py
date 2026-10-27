"""Persistent Index-TTS subprocess; uses the shared project environment."""
import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import threading


class IndexTTSClient:
    def __init__(self, root, settings):
        self.root = Path(root)
        self.settings = settings
        self.process = None
        self.log = None
        self.responses = queue.Queue()
        self.lock = threading.Lock()
        def absolute(value):
            path = Path(value)
            return path if path.is_absolute() else self.root / path
        self.engine_root = absolute(settings['root'])
        self.python = absolute(settings['python'])
        shared_python = self.root / '.venv/Scripts/python.exe'
        if self.python.resolve() != shared_python.resolve():
            raise ValueError('TTS 必須使用主專案 .venv/Scripts/python.exe')
        self.reference = absolute(settings['reference_audio'])
        for path in (self.python, self.reference, self.engine_root / settings['config_path']):
            if not path.is_file():
                raise FileNotFoundError(path)
        self.timeout = float(settings.get('inference_timeout', 600))
        logs = self.root / 'logs'
        logs.mkdir(exist_ok=True)
        self.log = (logs / 'index-tts.log').open('a', encoding='utf-8', buffering=1)
        env = os.environ.copy()
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PATH'] = str(self.root) + os.pathsep + env.get('PATH', '')
        try:
            self.process = subprocess.Popen(
                [str(self.python), '-u', str(self.root / 'scripts/index_tts_worker.py'),
                 json.dumps(settings, ensure_ascii=False)],
                cwd=self.engine_root, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self.log, text=True, encoding='utf-8',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=self._read, args=(self.process.stdout,), daemon=True).start()
            self._receive(float(settings.get('startup_timeout', 600)))
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)

    def _read(self, stream):
        try:
            for line in stream:
                self.responses.put(json.loads(line))
        except Exception as exc:
            self.responses.put({'error': str(exc)})
        finally:
            self.responses.put({'error': 'Index-TTS 背景程序已結束；請查看 logs/index-tts.log'})

    def _receive(self, timeout):
        try:
            result = self.responses.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise TimeoutError('Index-TTS 等待逾時；請查看 logs/index-tts.log') from None
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result

    def synthesize(self, text, output, emo_vector=None):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError('Index-TTS 未啟動')
            output = Path(output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            request = {'text': text, 'output': str(output), 'reference': str(self.reference),
                       'generation': self.settings.get('generation', {}), 'emo_vector': emo_vector}
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
            self._receive(self.timeout)

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        if self.log is not None:
            self.log.close()
            self.log = None
