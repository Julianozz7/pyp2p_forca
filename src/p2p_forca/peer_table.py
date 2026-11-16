import threading
from datetime import datetime, timezone


class PeerTable:
    """Armazena e gerencia os peers conhecidos na rede P2P."""

    def __init__(self):
        self.peers = {}
        self.lock = threading.Lock()

    def add_peer(self, peer_id, address, connection=None):
        """Adiciona um peer ou atualiza suas informações."""
        if not peer_id:
            raise ValueError("O peer_id não pode ser vazio.")

        peer_info = {
            "peer_id": peer_id,
            "address": address,
            "connection": connection,
            "status": "CONNECTED" if connection else "DISCOVERED",
            "last_seen": datetime.now(timezone.utc).isoformat()
        }

        with self.lock:
            self.peers[peer_id] = peer_info

        return peer_info

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
