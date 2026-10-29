"""Exercise real Windows spawn/IPC without models or account connections."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtuber.process_runtime import Peer, ServiceProcess


class ProcessTests(unittest.TestCase):
    def test_invalid_twitch_settings_do_not_break_discord(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'settings.yml'
            config.write_text('twitch: {enabled: true, channel: "!!!"}\ndiscord: {enabled: false}\n', encoding='utf-8')
            discord = ServiceProcess('discord', folder, config, lambda *a: None)
            twitch = ServiceProcess('twitch', folder, config, lambda *a: None)
            try:
                discord.start()
                pid = discord.call('ready')
                twitch.start()
                with self.assertRaisesRegex(RuntimeError, 'Twitch'):
                    twitch.call('ready')
                self.assertEqual(discord.call('ready'), pid)
            finally:
                discord.close()
                twitch.close()

    def test_stopping_one_service_keeps_other_alive(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'settings.yml'
            config.write_text('twitch: {enabled: false}\ndiscord: {enabled: false}\n', encoding='utf-8')
            first = ServiceProcess('discord', folder, config, lambda *a: None)
            second = ServiceProcess('twitch', folder, config, lambda *a: None)
            try:
                first.start()
                second.start()
                first.call('ready')
                pid = second.call('ready')
                first.close()
                self.assertEqual(second.call('ready'), pid)
                first.start()
                discord_pid = first.call('ready')
                second.close()
                self.assertEqual(first.call('ready'), discord_pid)
            finally:
                first.close()
                second.close()

    def test_disconnected_inflight_request_returns_error(self):
        import multiprocessing
        a, b = multiprocessing.Pipe()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def handler(*args):
            entered.set()
            release.wait(3)
        first = Peer(a, lambda *args: None)
        second = Peer(b, handler)
        errors = []
        def request():
            try:
                first.call('slow')
            except RuntimeError as exc:
                errors.append(exc)
            finally:
                finished.set()
        try:
            threading.Thread(target=request, daemon=True).start()
            self.assertTrue(entered.wait(3))
            first.close()
            self.assertTrue(finished.wait(3))
            self.assertTrue(errors)
        finally:
            release.set()
            first.close()
            second.close()

    def test_twitch_stop_edit_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'settings.yml'
            config.write_text('twitch: {enabled: false}\ndiscord: {enabled: false}\n', encoding='utf-8')
            service = ServiceProcess('twitch', Path(folder), config, lambda *a: None)
            try:
                service.start()
                pid = service.call('ready')
                self.assertNotEqual(pid, __import__('os').getpid())
                service.close()
                with self.assertRaises(RuntimeError):
                    service.call('ready')
                # Invalid edited config must surface in the fresh interpreter.
                config.write_text('twitch: {enabled: true, channel: "!!!"}\n', encoding='utf-8')
                service.start()
                with self.assertRaisesRegex(RuntimeError, 'Twitch'):
                    service.call('ready')
                service.close()
                config.write_text('twitch: {enabled: false}\ndiscord: {enabled: false}\n', encoding='utf-8')
                service.start()
                self.assertNotEqual(pid, service.call('ready'))
                service.process.terminate()
                service.process.join(5)
                with self.assertRaises((RuntimeError, OSError, EOFError)):
                    service.call('ready')
            finally:
                service.close()

    def test_duplex_callback(self):
        import multiprocessing
        a, b = multiprocessing.Pipe()
        first = Peer(a, lambda method, value: value * 2)
        second = Peer(b, lambda method, value: second.call('callback', value) + 1)
        try:
            self.assertEqual(first.call('request', 4), 9)
        finally:
            first.close()
            second.close()


if __name__ == '__main__':
    unittest.main()
