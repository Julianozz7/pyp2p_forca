import json
import socket


class RendezvousConnection:
    """Gerencia a comunicação com o servidor Rendezvous."""

    def __init__(self, host, port, namespace, peer_id):
        self.host = host
        self.port = port
        self.namespace = namespace
        self.peer_id = peer_id

    def _send_request(self, request):
        """Envia uma requisição JSON e recebe a resposta."""
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=10
            ) as sock:

                message = json.dumps(request).encode("utf-8") + b"\n"
                sock.sendall(message)

                response = b""

                while not response.endswith(b"\n"):
                    data = sock.recv(4096)

                    if not data:
                        break

                    response += data

                if not response:
                    return None

                return json.loads(response.decode("utf-8"))

        except (OSError, json.JSONDecodeError):
            return None
    def register(self, peer_port, ttl=7200):
        """Registra o peer no servidor Rendezvous."""
        request = {
            "type": "REGISTER",
            "namespace": self.namespace,
            "name": self.peer_id,
            "port": peer_port,
            "ttl": ttl
        }

        return self._send_request(request)

    def discover(self):
        """Descobre os peers registrados no mesmo namespace."""
        request = {
            "type": "DISCOVER",
            "namespace": self.namespace
        }

        return self._send_request(request)

    def unregister(self, peer_port):
        """Remove o registro do peer no servidor."""
        request = {
            "type": "UNREGISTER",
            "namespace": self.namespace,
            "name": self.peer_id,
            "port": peer_port
        }

        return self._send_request(request)
