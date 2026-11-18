"""Keep-alive: PING / PONG periódicos, cálculo de RTT e detecção de STALE."""

import logging
import threading
import time
import uuid

from p2p_forca.peer_connection import utc_timestamp

log = logging.getLogger("KeepAlive")


class KeepAlive:
    """Monitora a conexão com outros peers usando PING e PONG."""

    def __init__(self, peer_table, ping_interval=30, miss_limit=3,
                 on_stale=None):
        self.peer_table = peer_table
        self.ping_interval = ping_interval
        self.miss_limit = miss_limit
        self.on_stale = on_stale        # callback(peer_id) quando fica STALE

        self.running = False
        self.pending_pings = {}         # peer_id -> [msg_id, ...]
        self.sent_at = {}               # msg_id -> time.monotonic()

        self.lock = threading.Lock()
        self.thread = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # PING / PONG
    # ------------------------------------------------------------------
    def send_ping(self, connection):
        """Envia um PING para verificar se o peer está ativo."""
        if not connection.peer_id:
            return

        ping_id = str(uuid.uuid4())

        message = {
            "type": "PING",
            "msg_id": ping_id,
            "timestamp": utc_timestamp()
        }

        with self.lock:
            pending = self.pending_pings.setdefault(
                connection.peer_id,
                []
            )
            pending.append(ping_id)
            self.sent_at[ping_id] = time.monotonic()

        try:
            connection.send_message(message)
        except (OSError, ValueError) as error:
            log.warning(
                "[KeepAlive] could not send PING to %s: %s",
                connection.peer_id, error
            )

    def receive_pong(self, peer_id, message):
        """Registra um PONG recebido. Devolve True se casou com um PING."""
        rtt_ms = None

        with self.lock:
            pending = self.pending_pings.get(peer_id, [])
            ping_id = message.get("msg_id")

            if not pending or ping_id not in pending:
                return False

            # Qualquer PONG válido zera a contagem de PINGs sem resposta.
            for old_id in pending:
                started = self.sent_at.pop(old_id, None)

                if old_id == ping_id and started is not None:
                    rtt_ms = (time.monotonic() - started) * 1000

            self.pending_pings.pop(peer_id, None)

        if rtt_ms is not None:
            average = self.peer_table.record_rtt(peer_id, rtt_ms)

            if isinstance(average, (int, float)):
                log.info(
                    "[KeepAlive] %s RTT=%.1f ms (avg %.1f ms)",
                    peer_id, rtt_ms, average
                )

        return True

    def check_peers(self):
        """Marca como STALE quem acumulou ``miss_limit`` PINGs sem PONG."""
        stale = []

        with self.lock:
            for peer_id, pending in list(self.pending_pings.items()):
                if len(pending) >= self.miss_limit:
                    for ping_id in pending:
                        self.sent_at.pop(ping_id, None)

                    self.pending_pings.pop(peer_id, None)
                    stale.append(peer_id)

        for peer_id in stale:
            log.warning(
                "[KeepAlive] %s missed %d PINGs - marking STALE",
                peer_id, self.miss_limit
            )
            self.peer_table.update_status(peer_id, "STALE")

            if self.on_stale:
                try:
                    self.on_stale(peer_id)
                except Exception:
                    log.exception("[KeepAlive] on_stale callback failed")

        return stale

    def forget(self, peer_id):
        """Descarta PINGs pendentes de um peer (conexão encerrada)."""
        with self.lock:
            for ping_id in self.pending_pings.pop(peer_id, []):
                self.sent_at.pop(ping_id, None)

    # ------------------------------------------------------------------
    # handlers de mensagem (registrados no MessageRouter)
    # ------------------------------------------------------------------
    def handle_ping(self, connection, message):
        """Responde PING com PONG repetindo msg_id e timestamp."""
        reply = {
            "type": "PONG",
            "msg_id": message.get("msg_id"),
            "timestamp": message.get("timestamp", utc_timestamp()),
        }

        try:
            connection.send_message(reply)
        except (OSError, ValueError):
            pass

    def handle_pong(self, connection, message):
        if connection.peer_id:
            self.receive_pong(connection.peer_id, message)

    # ------------------------------------------------------------------
    # laço periódico
    # ------------------------------------------------------------------
    def start(self, get_connections):
        """Inicia o monitoramento periódico (``get_connections`` devolve
        as conexões ativas)."""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()

        def monitor():
            while self.running:
                # Primeiro verifica quem já acumulou PINGs sem resposta,
                # depois envia a nova rodada.
                self.check_peers()

                for connection in get_connections():
                    self.send_ping(connection)

                if self._stop_event.wait(self.ping_interval):
                    break

        self.thread = threading.Thread(
            target=monitor,
            daemon=True
        )
        self.thread.start()

    def stop(self):
        """Encerra o monitoramento."""
        self.running = False
        self._stop_event.set()