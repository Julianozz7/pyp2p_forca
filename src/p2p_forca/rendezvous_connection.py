import logging
import socket
import threading
import time
from collections import deque

from p2p_forca.peer_connection import MAX_LINE, decode_line, encode_line

log = logging.getLogger("RDV")
LOCAL_NAMESPACE = object()

class RendezvousConnection:
    """Abre uma conexão TCP nova para cada comando Rendezvous."""

    def __init__(self, host, port, namespace, peer_id):
        self.host = host
        self.port = port
        self.namespace = namespace
        # O rendezvous recebe o nome simples, separado do namespace.
        self.name = peer_id.removesuffix(f"@{namespace}")
        self.requests = deque()
        self.lock = threading.Lock()
        self.blocked_until = 0

    def _send_request(self, request):
        command = request["type"]
        with self.lock:
            now = time.monotonic()
            while self.requests and self.requests[0] + 60 <= now:
                self.requests.popleft()
            # Reserva espaço para renovações e UNREGISTER durante consultas repetidas.
            limit = {"DISCOVER": 46, "REGISTER": 49, "UNREGISTER": 50}[command]
            if now < self.blocked_until or len(self.requests) >= limit:
                return {"status": "ERROR", "message": "request_rate_limited"}
            self.requests.append(now)

            try:
                with socket.create_connection((self.host, self.port), timeout=3) as sock:
                    with sock.makefile("rb") as stream:
                        data = encode_line(request)
                        log.debug("TX %s", data[:-1].decode("utf-8"))
                        sock.sendall(data)
                        line = stream.readline(MAX_LINE + 2)
                if not line.endswith(b"\n") or len(line) > MAX_LINE + 1:
                    raise ValueError("Resposta incompleta ou acima de 32 KiB.")
                log.debug("RX %s", line[:-1].decode("utf-8", errors="replace"))
                response = decode_line(line[:-1])
                if response.get("status") not in ("OK", "ERROR"):
                    raise ValueError("Resposta sem status válido.")
                if response["status"] == "ERROR":
                    detail = response.get("message")
                    log.warning("%s: %s", command, detail)
                    if isinstance(detail, str) and "has been blocked" in detail:
                        self.blocked_until = time.monotonic() + 60
                return response
            except (OSError, ValueError, RecursionError) as error:
                log.warning("%s em %s:%s: %s", command, self.host, self.port, error)
                return None

    def register(self, peer_port, ttl=7200):
        return self._send_request({
            "type": "REGISTER", "namespace": self.namespace, "name": self.name,
            "port": peer_port, "ttl": ttl,
        })

    def discover(self, namespace=LOCAL_NAMESPACE):
        if namespace is LOCAL_NAMESPACE:
            namespace = self.namespace
        request = {"type": "DISCOVER"}
        if namespace is not None:
            request["namespace"] = namespace
        return self._send_request(request)

    def unregister(self, peer_port):
        return self._send_request({
            "type": "UNREGISTER", "namespace": self.namespace, "name": self.name,
            "port": peer_port,
        })