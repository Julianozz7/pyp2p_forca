import logging
import threading
import time
import uuid
from collections import deque

from p2p_forca.peer_connection import utc_timestamp

log = logging.getLogger("KeepAlive")

class KeepAlive:
    """Mede RTT e monitora PONGs pendentes, por conexão TCP."""

    def __init__(self, ping_interval=30, miss_limit=3):
        self.ping_interval = ping_interval
        self.miss_limit = miss_limit
        self.pending_pings = {}
        self.samples = {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = None

    def send_ping(self, connection):
        ping_id = str(uuid.uuid4())
        timestamp = utc_timestamp()
        with self.lock:
            pending = self.pending_pings.setdefault(connection, {})
            pending[ping_id] = (time.monotonic(), timestamp)
        try:
            connection.send_message({"type": "PING", "msg_id": ping_id, "timestamp": timestamp})
        except OSError:
            self.forget(connection)

    def receive_pong(self, connection, message):
        with self.lock:
            pending = self.pending_pings.get(connection, {})
            ping = pending.get(message["msg_id"])
            if ping is None or message["timestamp"] != ping[1]:
                return False
            # Um PONG válido interrompe a sequência de perdas até este PING.
            for ping_id in tuple(pending):
                del pending[ping_id]
                if ping_id == message["msg_id"]:
                    break
            if not pending:
                self.pending_pings.pop(connection, None)
            rtt = (time.monotonic() - ping[0]) * 1000
            samples = self.samples.setdefault(connection, deque(maxlen=10))
            samples.append(rtt)
            average = sum(samples) / len(samples)
        log.info("%s RTT=%.1f ms (avg %.1f ms)", connection.peer_id, rtt, average)
        return True

    def average_rtt(self, connection):
        with self.lock:
            samples = self.samples.get(connection)
            return sum(samples) / len(samples) if samples else None

    def forget(self, connection):
        with self.lock:
            self.pending_pings.pop(connection, None)
            self.samples.pop(connection, None)

    def check_peers(self, connections):
        for connection in connections:
            if self.stopped.is_set():
                break
            if connection.closed.is_set() or connection.bye_id is not None:
                continue
            with self.lock:
                misses = len(self.pending_pings.get(connection, {}))
            if misses >= self.miss_limit:
                log.info("%s missed %d PINGs — marking STALE", connection.peer_id, misses)
                connection.close()
            else:
                self.send_ping(connection)

    def start(self, get_connections):
        def monitor():
            while not self.stopped.wait(self.ping_interval):
                self.check_peers(get_connections())

        self.thread = threading.Thread(target=monitor, daemon=True, name="keep-alive")
        self.thread.start()

    def stop(self):
        self.stopped.set()