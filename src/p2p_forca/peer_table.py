import threading
from datetime import datetime, timezone


class PeerTable:
    def __init__(self):
        self.peers, self.lock = {}, threading.RLock()

    def add_peer(self, peer_id, address, connection=None):
        if not peer_id: raise ValueError("O peer_id não pode ser vazio.")
        with self.lock:
            old = self.peers.get(peer_id, {})
            info = {"peer_id":peer_id,"address":address,
                    "connection":connection or old.get("connection"),
                    "status":"CONNECTED" if connection or old.get("connection") else "DISCOVERED",
                    "last_seen":datetime.now(timezone.utc).isoformat(),
                    "rtt":old.get("rtt"),"direction":getattr(connection or old.get("connection"),"direction",None),
                    "failures":old.get("failures",0),"next_attempt":old.get("next_attempt",0)}
            self.peers[peer_id] = info
            return info

    def get_peer(self, peer_id):
        with self.lock: return self.peers.get(peer_id)
    def remove_peer(self, peer_id):
        with self.lock: return self.peers.pop(peer_id, None)
    def get_all_peers(self):
        with self.lock: return list(self.peers.values())
    def get_connected_peers(self):
        with self.lock: return [p for p in self.peers.values() if p["status"]=="CONNECTED" and p["connection"]]
    def update_status(self, peer_id, status):
        with self.lock:
            p=self.peers.get(peer_id)
            if not p:return False
            p["status"]=status; p["last_seen"]=datetime.now(timezone.utc).isoformat()
            return True
    def set_rtt(self, peer_id, rtt):
        with self.lock:
            p=self.peers.get(peer_id)
            if p:
                p.setdefault("rtt_samples",[]).append(rtt); p["rtt_samples"]=p["rtt_samples"][-10:]
                p["rtt"]=sum(p["rtt_samples"])/len(p["rtt_samples"])
    def count(self):
        with self.lock:return len(self.peers)
