"""ORM storage for the shared YouTube queue, playback state and deduplicated history."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs, urlencode
from urllib.request import Request, urlopen
import json
import math
import re
import threading
import time
from sqlalchemy import select, func
from database.models import SongVideo, SongRequest, SongPlayback


def parse_youtube_url(value):
    try:
        url = urlsplit(str(value).strip())
        if url.scheme not in ('http', 'https') or url.username or url.password or url.port:
            raise ValueError()
        host = (url.hostname or '').lower()
        video_id = None
        if host == 'youtu.be':
            match = re.fullmatch(r'/([A-Za-z0-9_-]{11})/?', url.path)
            video_id = match[1] if match else None
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            if url.path == '/watch':
                video_id = parse_qs(url.query).get('v', [''])[0]
            else:
                match = re.fullmatch(r'/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?', url.path)
                video_id = match[1] if match else None
        if not video_id or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
        return video_id
    except (ValueError, TypeError):
        raise ValueError('只接受有效的 YouTube 影片連結。') from None


class SongQueue:
    def __init__(self, database, settings=None):
        self.database = database
        self.settings = settings or {}
        self.lock = threading.RLock()
        self.skip = 0
        self.seen_at = 0
        self.last_error = None
        self.closed = threading.Event()
        self.metadata = ThreadPoolExecutor(max_workers=2, thread_name_prefix='song-title')
        with self.database.session() as db:
            playback = db.get(SongPlayback, 1)
            if playback is None:
                playback = SongPlayback(id=1)
                db.add(playback)
            # The original JS starts unpaused at full volume on each service start.
            playback.paused, playback.volume = False, 100
            playback.current_id, playback.position = None, 0

    def enqueue(self, url, requested_by, source='web'):
        video_id = parse_youtube_url(url)
        with self.lock, self.database.session() as db:
            if db.scalar(select(SongRequest.id).where(SongRequest.video_id == video_id)) is not None:
                raise ValueError('這首影片已在待播清單中。')
            count = db.scalar(select(func.count()).select_from(SongRequest))
            if count >= max(1, min(1000, int(self.settings.get('max_queue', 100)))):
                raise ValueError('待播清單已滿。')
            video = db.get(SongVideo, video_id)
            if video is None:
                video = SongVideo(video_id=video_id)
                db.add(video)
            video.last_requested_by = str(requested_by)[:100]
            row = SongRequest(video_id=video_id, requested_by=str(requested_by)[:100], source=source)
            db.add(row)
            db.flush()
            result = {'id': row.id, 'videoId': video_id, 'position': count + 1,
                      'message': f'點歌成功，目前排第 {count + 1} 首。'}
            load_title = not video.title
        if load_title and not self.closed.is_set():
            self.metadata.submit(self.load_title, video_id)
        return result

    def load_title(self, video_id):
        try:
            address = 'https://www.youtube.com/oembed?' + urlencode({
                'url': f'https://www.youtube.com/watch?v={video_id}', 'format': 'json'})
            with urlopen(Request(address, headers={'User-Agent': 'ai-vtuber-song-queue'}), timeout=5) as response:
                title = str(json.loads(response.read(65536)).get('title', '')).strip()[:500]
            if title and not self.closed.is_set():
                with self.database.session() as db:
                    video = db.get(SongVideo, video_id)
                    if video:
                        video.title = title
        except Exception:
            pass  # Queueing still works when metadata cannot be fetched.

    def snapshot(self, search=''):
        with self.lock, self.database.session() as db:
            rows = list(db.scalars(select(SongRequest).order_by(SongRequest.id)))
            playback = db.get(SongPlayback, 1)
            videos = {video.video_id: video for video in db.scalars(select(SongVideo))}
            songs = [{'id': row.id, 'videoId': row.video_id, 'title': videos[row.video_id].title or row.video_id,
                      'url': f'https://www.youtube.com/watch?v={row.video_id}', 'requestedBy': row.requested_by,
                      'createdAt': row.created_at} for row in rows]
            needle = str(search).casefold().strip()
            history = [{'videoId': video.video_id, 'title': video.title or video.video_id,
                        'lastrequestedBy': video.last_requested_by, 'lastcompletedAt': video.last_completed_at,
                        'count': video.play_count} for video in videos.values()
                       if not needle or needle in video.title.casefold() or needle in video.video_id.casefold()]
            history.sort(key=lambda row: row['lastcompletedAt'] or '', reverse=True)
            return {'songs': songs, 'history': history, 'current_id': playback.current_id,
                    'paused': playback.paused, 'volume': playback.volume, 'position': playback.position,
                    'skip': self.skip, 'seen_at': self.seen_at,
                    'last_error': self.last_error,
                    'player_connected': time.time() * 1000 - self.seen_at < 10000}

    def action(self, action, requested_by='本機', **values):
        if action in ('add', 'requeue'):
            url = values.get('url', '') if action == 'add' else f'https://youtu.be/{values.get("video_id", "")}'
            return self.enqueue(url, requested_by, values.get('source', 'web'))
        if action == 'list':
            return self.snapshot(search=values.get('search', ''))
        if action == 'error':
            with self.lock:
                self.last_error = {key: str(values.get(key, ''))[:100] for key in ('id', 'videoId', 'code')}
            return {'message': '已記錄 YouTube 播放器錯誤。'}
        with self.lock, self.database.session() as db:
            playback = db.get(SongPlayback, 1)
            if action == 'skip':
                # Match JS createPlayback: signal the player; it completes its current song.
                self.skip += 1
            elif action in ('complete', 'remove'):
                request_id = int(values.get('id', 0))
                row = db.get(SongRequest, request_id)
                if row:
                    if action == 'complete':
                        video = db.get(SongVideo, row.video_id)
                        video.play_count += 1
                        video.last_requested_by = row.requested_by
                        video.last_completed_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                    db.delete(row)
                    if playback.current_id == row.id:
                        playback.current_id = None
                        playback.position = 0
            elif action == 'failed':
                # The original player skips errors in its local finished set without counting them.
                pass
            elif action in ('pause', 'resume'):
                playback.paused = action == 'pause'
            elif action == 'volume':
                volume = float(values.get('volume', 100))
                if not math.isfinite(volume) or not 0 <= volume <= 100:
                    raise ValueError('音量必須為 0～100。')
                playback.volume = int(volume)
            elif action == 'report':
                request_id = int(values.get('id') or 0)
                playback.current_id = request_id if db.get(SongRequest, request_id) else None
                self.seen_at = time.time() * 1000
            else:
                raise ValueError('不支援的點歌操作。')
        return {'message': '點歌狀態已更新。'}

    def close(self):
        self.closed.set()
        self.metadata.shutdown(wait=False, cancel_futures=True)
