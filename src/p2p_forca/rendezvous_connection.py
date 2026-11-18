"""One TCP connection per Rendezvous command."""
import json, socket, logging


class RendezvousConnection:
    def __init__(self, host, port, namespace, peer_id):
        self.host, self.port, self.namespace = host, int(port), namespace
        self.peer_id = peer_id
        self.name = peer_id.split("@", 1)[0]

    def _send_request(self, request):
        try:
            with socket.create_connection((self.host, self.port), timeout=10) as sock:
                sock.settimeout(10)
                raw=json.dumps(request, separators=(",", ":"))
                logging.getLogger(__name__).debug("RDV SEND %s",raw)
                sock.sendall(raw.encode() + b"\n")
                chunks = bytearray()
                while b"\n" not in chunks and len(chunks) <= 32768:
                    part = sock.recv(4096)
                    if not part: break
                    chunks.extend(part)
                if not chunks: return None
                response=bytes(chunks).split(b"\n", 1)[0].decode()
                logging.getLogger(__name__).debug("RDV RECV %s",response)
                return json.loads(response)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def register(self, peer_port, ttl=7200):
        return self._send_request({"type":"REGISTER","namespace":self.namespace,
                                   "name":self.name,"port":int(peer_port),"ttl":int(ttl)})
    def discover(self, namespace=None):
        req = {"type":"DISCOVER", "namespace": self.namespace if namespace is None else namespace}
        return self._send_request(req)

    def discover_all(self):
        return self._send_request({"type":"DISCOVER"})
    def unregister(self, peer_port):
        return self._send_request({"type":"UNREGISTER","namespace":self.namespace,
                                   "name":self.name,"port":int(peer_port)})
