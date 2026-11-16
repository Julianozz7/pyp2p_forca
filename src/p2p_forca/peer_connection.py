
import json
import socket
import threading
import uuid
from datetime import datetime, timezone


class PeerConnection:
    """Gerencia a comunicação TCP com outro peer."""

    def __init__(self, sock, address, peer_id=None, on_message=None,
                 on_disconnect=None):
        self.sock = sock
        self.address = address
        self.peer_id = peer_id
        self.on_message = on_message
        self.on_disconnect = on_disconnect

        self.running = False
        self.send_lock = threading.Lock()
        self.buffer = b""

    def send_message(self, message):
        """Envia uma mensagem JSON terminada em quebra de linha."""

        if not isinstance(message, dict):
            raise TypeError("A mensagem precisa ser um dicionário.")

        message.setdefault("msg_id", str(uuid.uuid4()))
        message.setdefault("ttl", 1)
        message.setdefault(
            "timestamp",
            datetime.now(timezone.utc).isoformat()
        )

        data = json.dumps(
            message,
            ensure_ascii=False
        ).encode("utf-8") + b"\n"

        with self.send_lock:
            self.sock.sendall(data)

    def start(self):
        """Inicia a leitura de mensagens em uma thread."""

        if self.running:
            return

        self.running = True

        thread = threading.Thread(
            target=self._receive_loop,
            daemon=True
        )

        thread.start()

    def _receive_loop(self):
        """Recebe e processa mensagens JSON."""

        try:
            while self.running:
                data = self.sock.recv(4096)

                if not data:
                    break

                self.buffer += data

                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)

                    if not line.strip():
                        continue

                    try:
                        message = json.loads(line.decode("utf-8"))

                        if not isinstance(message, dict):
                            continue

                        if self.on_message:
                            self.on_message(self, message)

                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue

        except OSError:
            pass

        finally:
            self.close()

            if self.on_disconnect:
                self.on_disconnect(self)

    def close(self):
        """Encerra a conexão."""

        self.running = False

        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            self.sock.close()
        except OSError:
            pass

    def is_connected(self):
        """Informa se a conexão está ativa."""

        return self.running
