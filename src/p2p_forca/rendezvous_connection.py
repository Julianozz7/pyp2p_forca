"""Comunicação com o servidor Rendezvous (uma conexão TCP por comando)."""

import json
import logging
import socket
import threading
import time
from collections import deque

log = logging.getLogger("RDV")

MAX_LINE = 32768
_DEFAULT = object()


class RendezvousConnection:
    """REGISTER / DISCOVER / UNREGISTER.

    O servidor aceita exatamente um comando por conexão TCP e fecha a
    conexão após a resposta, então cada operação abre um socket novo.
    """

    # O servidor bloqueia o IP por 1 min após 50 requisições/min.
    # Mantemos uma trava local um pouco abaixo disso.
    LOCAL_LIMIT = 45

    def __init__(self, host, port, namespace, peer_id, name=None):
        self.host = host
        self.port = port
        self.namespace = namespace
        self.peer_id = peer_id
        # Nome registrado no servidor (sem o "@namespace").
        self.name = name if name is not None else peer_id

        self.public_ip = None      # IP observado pelo servidor (REGISTER)
        self._times = deque()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # transporte
    # ------------------------------------------------------------------
    def _allow_request(self):
        now = time.monotonic()

        with self._lock:
            while self._times and now - self._times[0] > 60:
                self._times.popleft()

            if len(self._times) >= self.LOCAL_LIMIT:
                return False

            self._times.append(now)
            return True

    def _send_request(self, request):
        """Envia uma requisição JSON e devolve a resposta (dict) ou None."""

        if not self._allow_request():
            log.warning("[RDV] local rate limit reached - request skipped")
            return {"status": "ERROR", "message": "local_rate_limit"}

        log.debug("[RDV] -> %s", request)

        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=10
            ) as sock:

                message = json.dumps(request).encode("utf-8") + b"\n"
                sock.sendall(message)

                response = b""

                while b"\n" not in response:
                    data = sock.recv(4096)

                    if not data:
                        break

                    response += data

                    if len(response) > MAX_LINE * 4:
                        break

                if not response.strip():
                    return None

                line = response.split(b"\n", 1)[0]
                reply = json.loads(line.decode("utf-8"))

                log.debug("[RDV] <- %s", reply)
                return reply

        except (OSError, ValueError) as error:
            log.error("[RDV] request failed: %s", error)
            return None

    # ------------------------------------------------------------------
    # comandos
    # ------------------------------------------------------------------
    def register(self, peer_port, ttl=7200):
        """Registra (ou renova o TTL de) este peer."""
        request = {
            "type": "REGISTER",
            "namespace": self.namespace,
            "name": self.name,
            "port": peer_port,
            "ttl": ttl
        }

        response = self._send_request(request)

        if response and response.get("status") == "OK":
            self.public_ip = response.get("ip")
            log.info(
                "[RDV] registered %s as %s:%s (ttl=%s)",
                self.peer_id,
                response.get("ip"),
                response.get("port"),
                response.get("ttl")
            )

        return response

    def discover(self, namespace=_DEFAULT):
        """Lista peers.

        Sem argumento usa o namespace local; ``namespace=None`` consulta
        todos os namespaces; ou informe um namespace específico.
        """
        if namespace is _DEFAULT:
            namespace = self.namespace

        request = {"type": "DISCOVER"}

        if namespace is not None:
            request["namespace"] = namespace

        return self._send_request(request)

    def unregister(self, peer_port):
        """Remove o registro deste peer."""
        request = {
            "type": "UNREGISTER",
            "namespace": self.namespace,
            "name": self.name,
            "port": peer_port
        }

        response = self._send_request(request)

        if response and response.get("status") == "OK":
            log.info("[RDV] unregistered %s", self.peer_id)

        return response