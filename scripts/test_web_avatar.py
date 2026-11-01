"""Real loopback FastAPI requests without Discord, AI models or a sound device."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import queue
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock, patch
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vtuber.GUI.web_avatar import WebAvatar
from database.engine import Database
from database.songs import SongQueue


class WebAvatarTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        model = self.root / '角色.model3.json'
        (self.root / 'happy.exp3.json').write_text('{}', encoding='utf-8')
        model.write_text(json.dumps({'FileReferences': {'Expressions': [
            {'Name': '開心', 'File': 'happy.exp3.json'}, {'Name': '不存在', 'File': 'missing.json'}]}}), encoding='utf-8')
        app = SimpleNamespace(path=model, live2d_settings={}, mouth=0.3,
                              emotions={'happy': '開心'}, stop=threading.Event())
        self.songs = SongQueue(Database(self.root / 'songs.sqlite3'))
        self.songs.load_title = Mock()
        app.songs = self.songs
        self.view = WebAvatar(app, port=0)
        self.view.start()

    def tearDown(self):
        self.view.close()
        self.songs.close()
        self.folder.cleanup()

    def get(self, path, **headers):
        with urlopen(Request(self.view.url.rstrip('/') + path, headers=headers), timeout=3) as response:
            return response.read()

    def post(self, body, **headers):
        payload = json.dumps(body).encode()
        request = Request(self.view.url + 'api/playback', data=payload,
                          headers={'Content-Type': 'application/json', **headers})
        return urlopen(request, timeout=3)

    def test_local_assets_and_filtered_model_preserve_source(self):
        self.assertIn('Live2D'.encode(), self.get('/'))
        config = json.loads(self.get('/api/config'))
        model = json.loads(self.get(config['model']))
        self.assertEqual([row['Name'] for row in model['FileReferences']['Expressions']], ['開心'])
        original = json.loads(self.view.model_path.read_text(encoding='utf-8'))
        self.assertEqual(len(original['FileReferences']['Expressions']), 2)
        self.assertNotIn('settings', config)

    def test_original_music_api_can_autoplay_complete_and_skip_without_a_lease(self):
        first = self.songs.enqueue('https://youtu.be/dQw4w9WgXcQ', '觀眾')
        second = self.songs.enqueue('https://youtu.be/9bZkp7q19f0', '觀眾')
        snapshot = json.loads(self.get('/api/songs'))
        self.assertEqual(snapshot['songs'][0]['id'], str(first['id']))
        self.assertFalse(snapshot['playback']['paused'])
        self.assertEqual(snapshot['playback']['volume'], 100)
        self.assertEqual(len(snapshot['history']['videos']), 2)
        def control(action, value=''):
            from urllib.parse import urlencode
            request = Request(self.view.url + 'api/control?' + urlencode({'action': action, 'value': value}),
                              data=b'', headers={'X-Playback-Token': self.view.token})
            return urlopen(request, timeout=3)
        with control('report', first['id']) as response:
            self.assertEqual(response.status, 200)
        with control('next') as response:
            self.assertEqual(response.headers['X-Playback-Skip'], '1')
        self.assertEqual(len(json.loads(self.get('/api/songs'))['songs']), 2)
        with control('complete', first['id']):
            pass
        with control('complete', first['id']):
            pass
        snapshot = json.loads(self.get('/api/songs'))
        self.assertEqual([row['id'] for row in snapshot['songs']], [str(second['id'])])
        video = next(row for row in snapshot['history']['videos'] if row['videoId'] == first['videoId'])
        self.assertEqual(video['playCount'], 1)

    def test_original_music_controls_require_local_origin_and_token(self):
        for headers in ({}, {'X-Playback-Token': self.view.token, 'Origin': 'https://evil.example'}):
            request = Request(self.view.url + 'api/control?action=next', data=b'', headers=headers)
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 403)

    def test_youtube_error_is_reported_without_counting_or_removing_song(self):
        from urllib.parse import urlencode
        song = self.songs.enqueue('https://youtu.be/dQw4w9WgXcQ', '觀眾')
        value = json.dumps({'id': str(song['id']), 'videoId': song['videoId'], 'code': 150})
        request = Request(self.view.url + 'api/control?' + urlencode({'action': 'error', 'value': value}),
                          data=b'', headers={'X-Playback-Token': self.view.token})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
        state = json.loads(self.get('/api/songs'))
        self.assertEqual(state['player_error']['code'], '150')
        self.assertEqual(len(state['songs']), 1)
        self.assertEqual(state['history']['videos'][0]['playCount'], 0)

    def test_generated_wav_and_playback_acknowledgement(self):
        self.get('/api/state?client=obs&ready=1')
        samples = np.sin(np.arange(2205) * 0.2).astype('float32') * 0.1
        clip = self.view.publish_audio(samples, 22050, ['happy'])
        self.assertTrue(clip['wait'])
        state = json.loads(self.get('/api/state'))
        self.assertEqual(state['mouth'], 0.3)
        self.assertEqual(state['audio'][0]['emotions'], ['開心'])
        decoded, rate = sf.read(io.BytesIO(self.get(state['audio'][0]['url'])))
        self.assertEqual(rate, 22050)
        self.assertEqual(len(decoded), len(samples))
        np.testing.assert_allclose(decoded, samples, atol=1 / 32768)
        with self.post({'id': 1, 'token': self.view.token, 'state': 'ended'}) as response:
            self.assertEqual(response.status, 200)
        self.assertTrue(clip['finished'].is_set())
        self.view.wait_audio(clip)

    def test_rejects_traversal_foreign_hosts_and_playback_without_token(self):
        for path, headers in (('/model/%2e%2e%2fsetting.yml', {}), ('/api/config', {'Host': 'evil.example'})):
            with self.assertRaises(HTTPError) as error:
                self.get(path, **headers)
            self.assertIn(error.exception.code, (403, 404))
        for body, headers in (({'id': 1, 'token': 'wrong', 'state': 'ended'}, {}),
                              ({'id': 1, 'token': self.view.token, 'state': 'ended'}, {'Origin': 'https://evil.example'})):
            with self.assertRaises(HTTPError) as error:
                self.post(body, **headers)
            self.assertEqual(error.exception.code, 403)

    def test_clip_retention_is_bounded_and_expired_waiters_are_released(self):
        first = self.view.publish_audio(np.zeros(100), 16000)
        for _ in range(12):
            self.view.publish_audio(np.zeros(100), 16000)
        self.assertEqual(len(self.view.clips), 8)
        self.assertTrue(first['finished'].is_set())
        self.assertFalse(first['wait'])

    def test_synthesis_also_reaches_web_for_discord_replies(self):
        from vtuber.voiceAI.speech import SpeechMixin
        audio = np.sin(np.arange(1600) * 0.1).astype('float32') * 0.1
        output = self.root / 'generated.wav'
        tts = Mock()
        tts.synthesize.side_effect = lambda *args, **kwargs: sf.write(output, audio, 16000)
        app = SimpleNamespace(tts=tts, stop=threading.Event(), emotions={'happy': '開心'},
            split_dialogue=lambda *args: (['happy'], '你好'), output=output, llm=None,
            tts_settings={}, model_lock=threading.RLock(), reply_destination='discord',
            discord=Mock(), web_view=self.view, commands=queue.Queue(), mouth=0, mouth_gain=6,
            auto_play=False)
        with patch('vtuber.voiceAI.emotion_middleware.EmotionAnalyzer') as analyzer:
            analyzer.return_value.analyze.return_value = [0] * 8
            analyzer.return_value.last_error = None
            SpeechMixin.synthesize(app, '你好')
        app.discord.play_audio.assert_called_once()
        self.assertEqual(len(self.view.clips), 1)
        self.assertEqual(self.view.clips[1]['emotions'], ['開心'])


if __name__ == '__main__':
    unittest.main()
