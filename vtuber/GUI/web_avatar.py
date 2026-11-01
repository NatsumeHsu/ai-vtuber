"""Loopback-only Live2D assets, avatar state and generated speech playback."""
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
import io
import json
import secrets
import threading
import time


class WebAvatar:
    def __init__(self, app, port=None):
        self.app = app
        self.model_path = Path(app.path).resolve()
        self.assets = Path(__file__).parent / 'web'
        self.port = int(port if port is not None else app.live2d_settings.get('web', {}).get('port', 8765))
        self.token = secrets.token_urlsafe(32)
        self.instance = secrets.token_hex(12)
        self.lock = threading.RLock()
        self.clips = OrderedDict()
        self.clients = {}
        self.sequence = 0
        self.expressions = []
        self.server = self.thread = None

    @property
    def url(self):
        return f'http://localhost:{self.port}/'

    def create_api(self):
        api = FastAPI(title='白璃 Live2D', docs_url=None, redoc_url=None, openapi_url=None)

        @api.middleware('http')
        async def local_requests(request: Request, call_next):
            if request.headers.get('host') not in (f'localhost:{self.port}', f'localhost:{self.port}'):
                return JSONResponse({'error': 'Invalid host'}, status_code=403)
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response

        @api.get('/api/config')
        def config():
            with self.lock:
                return {'model': '/model/' + quote(self.model_path.name), 'token': self.token,
                        'instance': self.instance, 'audio_sequence': self.sequence}

        @api.get('/api/state')
        def state(client: str = '', ready: bool = False):
            with self.lock:
                now = time.monotonic()
                self.clients = {key: value for key, value in self.clients.items() if now - value[0] < 5}
                if client:
                    self.clients[client[:80]] = (now, ready)
                clips = [{'id': key, 'url': f'/audio/{key}.wav', 'duration': value['duration'],
                          'emotions': value['emotions']} for key, value in self.clips.items()
                         if now - value['created'] < 60]
                return {'mouth': float(self.app.mouth), 'expressions': self.expressions,
                        'audio': clips, 'instance': self.instance, 'audio_sequence': self.sequence}

        @api.post('/api/playback')
        async def playback(request: Request):
            if request.headers.get('origin') not in (None, self.url.rstrip('/'), f'http://localhost:{self.port}'):
                raise HTTPException(status_code=403)
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 4096:
                    raise HTTPException(status_code=413)
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get('token', '')), self.token):
                    raise HTTPException(status_code=403)
                with self.lock:
                    clip = self.clips.get(int(payload['id']))
                    if clip and payload.get('state') in ('ended', 'blocked', 'error'):
                        clip['finished'].set()
                return {'ok': True}
            except (ValueError, KeyError, TypeError):
                raise HTTPException(status_code=400)

        @api.get('/audio/{key}.wav')
        def audio(key: int):
            with self.lock:
                clip = self.clips.get(key)
                if not clip:
                    raise HTTPException(status_code=404)
                return Response(clip['data'], media_type='audio/wav')

        @api.get('/model/{relative:path}')
        def model_asset(relative: str):
            target = self.safe_asset(self.model_path.parent, relative)
            if target == self.model_path:
                model = json.loads(target.read_text(encoding='utf-8-sig'))
                references = model.get('FileReferences', {})
                references['Expressions'] = [item for item in references.get('Expressions', [])
                                            if (target.parent / item['File']).is_file()]
                return model
            return FileResponse(target)

        @api.get('/songs')
        @api.get('/songs/')
        def song_page():
            return FileResponse(self.assets / 'music' / 'index.html')

        @api.get('/api/songs')
        def song_state():
            snapshot = self.app.songs.snapshot()
            records = [{**row, 'title': row['title'], 'url': f'https://www.youtube.com/watch?v={row["videoId"]}'}
                       for row in snapshot['history']]
            return {'songs': [{**song, 'id': str(song['id'])} for song in snapshot['songs']],
                    'history': {'records': records, 'videos': [
                        {'videoId': row['videoId'], 'title': row['title'], 'url': row['url'],
                         'playCount': row['count'], 'lastPlayedAt': row['lastcompletedAt']} for row in records]},
                    'playback': {'paused': snapshot['paused'], 'volume': snapshot['volume'],
                                 'skip': snapshot['skip'], 'currentId': str(snapshot['current_id']) if snapshot['current_id'] else None,
                                 'seenAt': snapshot['seen_at'], 'connected': snapshot['player_connected']},
                    'player_error': snapshot['last_error'],
                    'status': '點歌台已就緒'}

        @api.post('/api/control')
        def original_song_control(request: Request, action: str, value: str = ''):
            if request.headers.get('origin') not in (None, self.url.rstrip('/'), f'http://localhost:{self.port}'):
                raise HTTPException(status_code=403)
            if not secrets.compare_digest(request.headers.get('X-Playback-Token', ''), self.token):
                raise HTTPException(status_code=403)
            commands = {'play': ('resume', {}), 'pause': ('pause', {}), 'next': ('skip', {}),
                        'volume': ('volume', {'volume': value}), 'enqueue': ('requeue', {'video_id': value}),
                        'complete': ('complete', {'id': value}), 'report': ('report', {'id': value})}
            if action not in commands and action != 'error':
                raise HTTPException(status_code=400, detail='不支援的控制指令。')
            try:
                if action == 'error':
                    if len(value) > 2048:
                        raise ValueError('錯誤回報過長。')
                    payload = json.loads(value)
                    if not isinstance(payload, dict):
                        raise ValueError('錯誤回報格式無效。')
                    result = self.app.songs.action('error', **{key: payload.get(key) for key in ('id', 'videoId', 'code')})
                    return JSONResponse(result)
                method, values = commands[action]
                result = self.app.songs.action(method, **values)
                return JSONResponse(result, headers={'X-Playback-Skip': str(self.app.songs.skip)})
            except (ValueError, TypeError) as exc:
                return JSONResponse({'error': str(exc)}, status_code=400)

        @api.post('/api/music')
        async def song_control(request: Request):
            if request.headers.get('origin') not in (None, self.url.rstrip('/'), f'http://localhost:{self.port}'):
                raise HTTPException(status_code=403)
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 8192:
                    raise HTTPException(status_code=413)
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get('token', '')), self.token):
                    raise HTTPException(status_code=403)
                action = payload.get('action')
                if action not in ('add', 'requeue', 'pause', 'resume', 'skip', 'remove', 'volume', 'report', 'complete', 'failed'):
                    raise ValueError('不支援的點歌操作。')
                values = {key: payload[key] for key in ('url', 'video_id', 'id', 'volume', 'position', 'client') if key in payload}
                from starlette.concurrency import run_in_threadpool
                return await run_in_threadpool(self.app.songs.action, action,
                    str(payload.get('requested_by') or '本機')[:100], **values)
            except (ValueError, KeyError, TypeError) as exc:
                return JSONResponse({'error': str(exc)}, status_code=400)

        @api.get('/{relative:path}')
        def page(relative: str):
            return FileResponse(self.safe_asset(self.assets, relative or 'index.html'))

        return api

    @staticmethod
    def safe_asset(root, relative):
        target = (root / relative).resolve()
        if (not target.is_relative_to(root.resolve()) or not target.is_file()
                or target.suffix.lower() not in {'.html', '.css', '.js', '.json', '.moc3', '.png', '.jpg', '.webp', '.txt'}):
            raise HTTPException(status_code=404)
        return target

    def start(self):
        import socket
        import uvicorn
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(('localhost', self.port))
            listener.listen(128)
            listener.setblocking(False)
            self.port = listener.getsockname()[1]
            self.api = self.create_api()
            self.server = uvicorn.Server(uvicorn.Config(self.api, host='localhost', port=self.port,
                log_config=None, access_log=False, log_level='error'))
            self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [listener]},
                                           name='avatar-fastapi', daemon=True)
            self.thread.start()
            deadline = time.monotonic() + 5
            while not self.server.started:
                if not self.thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError('FastAPI 角色服務無法啟動')
                time.sleep(0.01)
        except BaseException:
            listener.close()
            if self.server:
                self.server.should_exit = True
            raise

    def publish_audio(self, audio, sample_rate, emotions=()):
        import numpy as np
        import soundfile as sf
        buffer = io.BytesIO()
        sf.write(buffer, np.asarray(audio, dtype='float32'), sample_rate, format='WAV', subtype='PCM_16')
        data = buffer.getvalue()
        with self.lock:
            self.sequence += 1
            clip = {'data': data, 'duration': len(audio) / sample_rate, 'created': time.monotonic(),
                    'emotions': [self.app.emotions[key] for key in emotions if key in self.app.emotions],
                    'finished': threading.Event()}
            self.clips[self.sequence] = clip
            while len(self.clips) > 8 or (len(self.clips) > 1 and sum(len(c['data']) for c in self.clips.values()) > 32 * 1024 * 1024):
                _, expired = self.clips.popitem(last=False)
                expired['finished'].set()
            clip['wait'] = any(ready and time.monotonic() - seen < 5 for seen, ready in self.clients.values())
        return clip

    def wait_audio(self, clip):
        if not clip['wait']:
            return
        deadline = time.monotonic() + clip['duration'] + 8
        while not self.app.stop.is_set() and time.monotonic() < deadline:
            if clip['finished'].wait(0.1):
                return

    def close(self):
        with self.lock:
            for clip in self.clips.values():
                clip['finished'].set()
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(3)
            self.thread = None
        self.server = None
