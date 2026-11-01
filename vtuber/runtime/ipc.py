"""Bidirectional RPC over private process pipes."""
import itertools
import queue
import threading


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
