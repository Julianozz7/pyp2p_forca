import threading
import time
import uuid
from datetime import datetime, timezone


class KeepAlive:
    """Monitora a conexão com outros peers usando PING e PONG."""

    def __init__(self, peer_table, ping_interval=30, miss_limit=3):
        self.peer_table = peer_table
        self.ping_interval = ping_interval
        self.miss_limit = miss_limit

        self.running = False
        self.pending_pings = {}

        self.lock = threading.Lock()
        self.thread = None

    def send_ping(self, connection):
        """Envia um PING para verificar se o peer está ativo."""
        if not connection.peer_id:
            return

        ping_id = str(uuid.uuid4())

        message = {
            "type": "PING",
            "msg_id": ping_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        with self.lock:
            pending = self.pending_pings.setdefault(
                connection.peer_id,
                []
            )
            pending.append(ping_id)

        connection.send_message(message)

    def receive_pong(self, peer_id, message):
        """Registra a resposta PONG recebida."""
        with self.lock:
            pending = self.pending_pings.get(peer_id, [])

            if not pending:
                return False

            ping_id = message.get("msg_id")

            if ping_id not in pending:
                return False

            pending.remove(ping_id)

            if not pending:
                self.pending_pings.pop(peer_id, None)

            return True

    def check_peers(self):
        """Verifica quais peers não estão respondendo."""
        with self.lock:
            for peer_id, pending in list(self.pending_pings.items()):
                if len(pending) >= self.miss_limit:
                    self.peer_table.update_status(peer_id, "STALE")
                    self.pending_pings.pop(peer_id, None)

    def start(self, get_connections):
        """Inicia o monitoramento periódico dos peers."""
        if self.running:
            return

        self.running = True

        def monitor():
            while self.running:
                for connection in get_connections():
                    self.send_ping(connection)

                self.check_peers()

                time.sleep(self.ping_interval)

        self.thread = threading.Thread(
            target=monitor,
            daemon=True
        )
        self.thread.start()

    def stop(self):
        """Encerra o monitoramento."""
        self.running = False
