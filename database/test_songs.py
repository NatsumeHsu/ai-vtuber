"""Queue and history behavior shared with the original Node song project."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from database.engine import Database
from database.songs import SongQueue, parse_youtube_url


class SongTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.folder.name) / 'songs.sqlite3')
        self.queue = SongQueue(self.database)
        self.queue.load_title = Mock()

    def tearDown(self):
        self.queue.close()
        self.folder.cleanup()

    def add(self, video='dQw4w9WgXcQ'):
        return self.queue.enqueue('https://youtu.be/' + video, '觀眾')

    def test_youtube_urls_are_strictly_validated(self):
        for url in ('https://youtu.be/dQw4w9WgXcQ', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1',
                    'https://youtube.com/shorts/dQw4w9WgXcQ'):
            self.assertEqual(parse_youtube_url(url), 'dQw4w9WgXcQ')
        for url in ('https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ', 'https://user:pass@youtu.be/dQw4w9WgXcQ',
                    'https://youtu.be:443/dQw4w9WgXcQ', 'file:///tmp/test', 'https://youtu.be/short',
                    'https://www.youtube.com/playlist?list=test', '搜尋歌曲'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_youtube_url(url)

    def test_queue_limit_duplicates_and_monotonic_ids(self):
        self.queue.settings['max_queue'] = 1
        first = self.add()
        with self.assertRaises(ValueError):
            self.add()
        with self.assertRaises(ValueError):
            self.add('9bZkp7q19f0')
        self.queue.action('complete', id=first['id'])
        second = self.add('9bZkp7q19f0')
        self.assertGreater(second['id'], first['id'])

    def test_skip_signals_player_and_completion_counts_exactly_once(self):
        first = self.add()
        self.add('9bZkp7q19f0')
        self.queue.action('skip')
        snapshot = self.queue.snapshot()
        self.assertEqual(snapshot['skip'], 1)
        self.assertEqual(len(snapshot['songs']), 2)  # JS player sends completion separately.
        self.queue.action('complete', id=first['id'])
        self.queue.action('complete', id=first['id'])
        snapshot = self.queue.snapshot()
        self.assertEqual(len(snapshot['songs']), 1)
        self.assertEqual(next(v['count'] for v in snapshot['history'] if v['videoId'] == first['videoId']), 1)

    def test_failed_playback_does_not_count_or_delete_original_queue(self):
        first = self.add()
        self.queue.action('failed', id=first['id'])
        snapshot = self.queue.snapshot()
        self.assertEqual(snapshot['history'][0]['count'], 0)
        self.assertEqual(len(snapshot['songs']), 1)

    def test_history_requeue_and_restart_preserve_records_but_reset_controls(self):
        first = self.add()
        self.queue.action('complete', id=first['id'])
        self.queue.action('requeue', video_id=first['videoId'])
        self.queue.action('pause')
        self.queue.action('volume', volume=25)
        self.queue.close()
        self.queue = SongQueue(self.database)
        self.queue.load_title = Mock()
        snapshot = self.queue.snapshot()
        self.assertEqual(len(snapshot['songs']), 1)
        self.assertEqual(len(snapshot['history']), 1)
        self.assertEqual(snapshot['history'][0]['count'], 1)
        self.assertFalse(snapshot['paused'])
        self.assertEqual(snapshot['volume'], 100)

    def test_report_does_not_require_a_playback_lease(self):
        first = self.add()
        self.queue.action('report', id=str(first['id']))
        self.assertTrue(self.queue.snapshot()['player_connected'])
        self.assertEqual(self.queue.snapshot()['current_id'], first['id'])
        self.queue.action('report', id='')
        self.assertIsNone(self.queue.snapshot()['current_id'])

    def test_concurrent_duplicate_requests_only_create_one_entry(self):
        def request(_):
            try:
                return self.add()
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as workers:
            result = list(workers.map(request, range(8)))
        self.assertEqual(sum(row is not None for row in result), 1)
        self.assertEqual(len(self.queue.snapshot()['songs']), 1)


if __name__ == '__main__':
    unittest.main()
