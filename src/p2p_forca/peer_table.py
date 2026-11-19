import threading
from datetime import datetime, timezone

class PeerTable:
    """Tabela de presença, conexões e tentativas de cada vizinho.

    O cliente usa seu lock de estado para transições que também envolvem partidas.
    """

    def __init__(self):
        self.peers = {}
        self.lock = threading.Lock()

    def add_peer(self, peer_id, address, connection=None):
        if not peer_id:
            raise ValueError("O peer_id não pode ser vazio.")
        with self.lock:
            peer = self.peers.setdefault(peer_id, {
                "peer_id": peer_id,
                "advertised": False,
                "attempts": 0,
                "retry_at": 0,
                "dialing": False,
                "duplicate_until": None,
            })
            peer.update(address=address, connection=connection,
                        status="CONNECTED" if connection else "DISCOVERED",
                        last_seen=datetime.now(timezone.utc).isoformat())
            return peer

    def get_peer(self, peer_id):
        with self.lock:
            return self.peers.get(peer_id)

    def remove_peer(self, peer_id):
        with self.lock:
            return self.peers.pop(peer_id, None)

    def get_all_peers(self):
        with self.lock:
            return list(self.peers.values())

    def get_connected_peers(self):
        with self.lock:
            return [peer for peer in self.peers.values() if peer["status"] == "CONNECTED"]

    def update_status(self, peer_id, status):
        with self.lock:
            peer = self.peers.get(peer_id)
            if peer is None:
                return False
            peer["status"] = status
            peer["last_seen"] = datetime.now(timezone.utc).isoformat()
            return True

    def count(self):
        with self.lock:
            return len(self.peers)