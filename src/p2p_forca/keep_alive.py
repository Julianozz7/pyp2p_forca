import time
import uuid
import logging
import threading

logger = logging.getLogger("KeepAlive")

class KeepAliveManager:
    "Gerencia o envio periódico de PING e a verificação de PONG/Timeouts para todas as conexões ativas na PeerTable."
    def __init__(self, peer_table, ping_interval=30, miss_limit=3):
        self.peer_table = peer_table
        self.ping_interval = ping_interval
        self.miss_limit = miss_limit
        self.running = False
        self.ping_tracking = {}  # Mapeia msg_id -> (peer_id, send_timestamp)
        self.missed_pings = {}   # Mapeia peer_id -> contador de pings perdidos
        self.lock = threading.Lock()

    def start(self):
        "Inicia a thread de monitoramento do Keep-Alive."
        self.running = True
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()
        logger.info("[KeepAlive] Gerenciador iniciado.")

    def stop(self):
        self.running = False

    def handle_ping_received(self, msg, connection):
        "Chamado quando recebemos um PING de um peer. Deve responder com PONG."
        timestamp = msg.get("timestamp")
        pong_msg = {
            "type": "PONG",
            "msg_id": msg.get("msg_id"),
            "timestamp": timestamp,
            "ttl": 1
        }
        connection.send_json(pong_msg)

    def handle_pong_received(self, msg, connection):
        "Chamado quando recebemos um PONG. Calcula o RTT e reseta os pings perdidos."
        msg_id = msg.get("msg_id")
        remote_id = connection.remote_peer_id

        if not remote_id:
            return

        with self.lock:
            if remote_id in self.missed_pings:
                self.missed_pings[remote_id] = 0  # Reseta o contador de falhas

            if msg_id in self.ping_tracking:
                _, send_time = self.ping_tracking.pop(msg_id)
                rtt = (time.time() - send_time) * 1000  # em milissegundos
                logger.debug(f"[KeepAlive] RTT com {remote_id}: {rtt:.2f} ms")

    def _run_loop(self):
        "Loop executado em background para disparar PINGs e checar timeouts."
        while self.running:
            time.sleep(self.ping_interval)
            if not self.running:
                break

            connections = self.peer_table.get_connected_peers()
            current_time = time.time()

            for conn in connections:
                remote_id = conn.remote_peer_id
                if not remote_id:
                    continue

                with self.lock:
                    if remote_id not in self.missed_pings:
                        self.missed_pings[remote_id] = 0

                    # Incrementa o contador de perdidos; se receber PONG a tempo, ele zera
                    self.missed_pings[remote_id] += 1
                    if self.missed_pings[remote_id] > self.miss_limit:
                        logger.warning(f"[KeepAlive] {remote_id} perdeu {self.miss_limit} PINGs consecutivos. Marcando STALE.")
                        conn.close()
                        continue

                # Envia novo PING
                ping_msg_id = str(uuid.uuid4())
                iso_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(current_time))

                with self.lock:
                    self.ping_tracking[ping_msg_id] = (remote_id, current_time)

                ping_payload = {
                    "type": "PING",
                    "msg_id": ping_msg_id,
                    "timestamp": iso_timestamp,
                    "ttl": 1
                }
                conn.send_json(ping_payload)