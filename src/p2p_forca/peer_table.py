"""Tabela de peers conhecidos: estado, RTT, backoff e reconciliação."""

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

log = logging.getLogger("PeerTable")

# Estados: DISCOVERED, CONNECTING, CONNECTED, STALE, GONE
RTT_WINDOW = 10


class PeerTable:
    """Armazena e gerencia os peers conhecidos na rede P2P."""

    def __init__(self):
        self.peers = {}
        self.lock = threading.RLock()

    # ------------------------------------------------------------------
    # CRUD básico
    # ------------------------------------------------------------------
    def add_peer(self, peer_id, address, connection=None):
        """Adiciona um peer ou atualiza suas informações."""
        if not peer_id:
            raise ValueError("O peer_id não pode ser vazio.")

        now = datetime.now(timezone.utc).isoformat()

        with self.lock:
            peer = self.peers.get(peer_id)

            if peer is None:
                peer = {
                    "peer_id": peer_id,
                    "rtt_samples": deque(maxlen=RTT_WINDOW),
                    "attempts": 0,
                    "next_retry": 0.0,
                    "seen_absent": True,
                    "was_connected": False,
                    "features": [],
                    "direction": None,
                }
                self.peers[peer_id] = peer

            peer["address"] = address
            peer["connection"] = connection
            peer["status"] = "CONNECTED" if connection else "DISCOVERED"
            peer["last_seen"] = now

            if connection is not None:
                peer["attempts"] = 0
                peer["next_retry"] = 0.0

            return peer

    def get_peer(self, peer_id):
        """Retorna as informações de um peer."""
        with self.lock:
            return self.peers.get(peer_id)

    def remove_peer(self, peer_id):
        """Remove um peer da tabela."""
        with self.lock:
            return self.peers.pop(peer_id, None)

    def get_all_peers(self):
        """Retorna uma cópia da lista de peers."""
        with self.lock:
            return list(self.peers.values())

    def get_connected_peers(self):
        """Retorna somente os peers conectados."""
        with self.lock:
            return [
                peer for peer in self.peers.values()
                if peer["status"] == "CONNECTED"
            ]

    def update_status(self, peer_id, status):
        """Atualiza o estado de um peer."""
        with self.lock:
            peer = self.peers.get(peer_id)

            if peer is None:
                return False

            peer["status"] = status
            peer["last_seen"] = datetime.now(timezone.utc).isoformat()

            return True

    def count(self):
        """Retorna a quantidade de peers conhecidos."""
        with self.lock:
            return len(self.peers)

    # ------------------------------------------------------------------
    # conexão
    # ------------------------------------------------------------------
    def set_connected(self, peer_id, connection, direction=None,
                      features=None):
        """Marca o peer como CONNECTED com a conexão informada."""
        with self.lock:
            peer = self.peers.get(peer_id)

            if peer is None:
                peer = self.add_peer(peer_id, connection.address)

            peer["connection"] = connection
            peer["status"] = "CONNECTED"
            peer["direction"] = direction
            peer["features"] = list(features or [])
            peer["was_connected"] = True
            peer["attempts"] = 0
            peer["next_retry"] = 0.0
            peer["last_seen"] = datetime.now(timezone.utc).isoformat()

            return peer

    def get_connection(self, peer_id):
        with self.lock:
            peer = self.peers.get(peer_id)

            if peer and peer["status"] == "CONNECTED":
                return peer["connection"]

            return None

    # ------------------------------------------------------------------
    # RTT
    # ------------------------------------------------------------------
    def record_rtt(self, peer_id, rtt_ms):
        """Registra uma amostra de RTT; devolve a média móvel (10 últimas)."""
        with self.lock:
            peer = self.peers.get(peer_id)

            if peer is None:
                return None

            peer["rtt_samples"].append(rtt_ms)

            return sum(peer["rtt_samples"]) / len(peer["rtt_samples"])

    def get_avg_rtt(self, peer_id):
        with self.lock:
            peer = self.peers.get(peer_id)

            if not peer or not peer["rtt_samples"]:
                return None

            return sum(peer["rtt_samples"]) / len(peer["rtt_samples"])

    def get_last_rtt(self, peer_id):
        with self.lock:
            peer = self.peers.get(peer_id)

            if not peer or not peer["rtt_samples"]:
                return None

            return peer["rtt_samples"][-1]

    # ------------------------------------------------------------------
    # backoff / reconexão
    # ------------------------------------------------------------------
    @staticmethod
    def backoff_delay(attempt, base, cap):
        """base * 2^attempt, limitado a cap."""
        return min(base * (2 ** attempt), cap)

    def schedule_retry(self, peer_id, base_backoff, max_backoff,
                       max_attempts, now=None):
        """Registra uma tentativa de conexão que falhou.

        Devolve o novo estado: o mesmo de antes (aguardando retry) ou
        ``GONE`` quando o limite de tentativas foi atingido.
        """
        now = time.monotonic() if now is None else now

        with self.lock:
            peer = self.peers.get(peer_id)

            if peer is None:
                return None

            peer["connection"] = None
            attempt = peer["attempts"]
            peer["attempts"] = attempt + 1

            if peer["attempts"] >= max_attempts:
                peer["status"] = "GONE"
                peer["seen_absent"] = False
                log.warning(
                    "[PeerTable] %s unreachable after %d attempts - GONE",
                    peer_id, peer["attempts"]
                )
                return "GONE"

            delay = self.backoff_delay(attempt, base_backoff, max_backoff)
            peer["next_retry"] = now + delay

            # STALE = conexão que existiu e foi perdida; antes disso o
            # peer segue DISCOVERED.
            peer["status"] = "STALE" if peer["was_connected"] else "DISCOVERED"

            log.info(
                "[PeerTable] %s retry #%d in %.0f s",
                peer_id, peer["attempts"], delay
            )

            return peer["status"]

    def due_for_retry(self, now=None):
        """Peers sem conexão cujo próximo retry já venceu."""
        now = time.monotonic() if now is None else now

        with self.lock:
            return [
                peer for peer in self.peers.values()
                if peer["status"] in ("DISCOVERED", "STALE")
                and peer["next_retry"] <= now
            ]

    def mark_gone(self, peer_id, seen_absent=True):
        with self.lock:
            peer = self.peers.get(peer_id)

            if peer is None:
                return False

            peer["status"] = "GONE"
            peer["connection"] = None
            peer["seen_absent"] = seen_absent

            return True

    # ------------------------------------------------------------------
    # reconciliação com o Rendezvous
    # ------------------------------------------------------------------
    def reconcile(self, discovered):
        """Sincroniza a tabela com o resultado de um DISCOVER.

        ``discovered`` é um dicionário ``peer_id -> (ip, port)`` já sem o
        próprio peer. Devolve ``(novos, ausentes)``:
          novos    -> peer_ids que entraram como DISCOVERED (ou reapareceram)
          ausentes -> peer_ids marcados GONE (a conexão deve ser fechada)
        """
        new, absent = [], []

        with self.lock:
            for peer_id, address in discovered.items():
                peer = self.peers.get(peer_id)

                if peer is None:
                    self.add_peer(peer_id, address)
                    new.append(peer_id)
                    continue

                peer["address"] = address

                # GONE só volta se o peer sumiu do Rendezvous e reapareceu
                # (evita insistir em registros "mortos" que ainda não
                # expiraram no servidor).
                if peer["status"] == "GONE" and peer["seen_absent"]:
                    peer["status"] = "DISCOVERED"
                    peer["attempts"] = 0
                    peer["next_retry"] = 0.0
                    new.append(peer_id)

            for peer_id, peer in self.peers.items():
                if peer_id in discovered:
                    continue

                if peer["status"] != "GONE":
                    absent.append(peer_id)

                peer["status"] = "GONE"
                peer["seen_absent"] = True

        return new, absent