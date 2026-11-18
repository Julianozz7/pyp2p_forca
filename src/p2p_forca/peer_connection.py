"""Conexão TCP com um peer: framing JSON-por-linha."""

import json
import logging
import socket
import threading
import uuid
from datetime import datetime, timezone

log = logging.getLogger("PeerServer")

MAX_LINE = 32768


def utc_timestamp():
    """Instante atual em ISO 8601 UTC, ex.: 2026-10-27T10:00:00.120Z."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class PeerConnection:
    """Gerencia a comunicação TCP com outro peer.

    Cada mensagem é um objeto JSON em uma única linha terminada em ``\\n``.
    """

    def __init__(self, sock, address, peer_id=None, on_message=None,
                 on_disconnect=None, inbound=False):
        self.sock = sock
        self.address = address
        self.peer_id = peer_id
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.inbound = inbound

        # Preenchidos durante o handshake HELLO / HELLO_OK
        self.ready = False
        self.features = []
        self.replaced = False          # True quando perdeu o desempate
        self.closed_by_peer_shutdown = False

        self.running = False
        self.send_lock = threading.Lock()
        self.buffer = b""
        self.bye_event = threading.Event()   # setado ao receber BYE_OK
        self._closed = False
        self._close_lock = threading.Lock()

    # ------------------------------------------------------------------
    # envio
    # ------------------------------------------------------------------
    def send_message(self, message):
        """Envia uma mensagem JSON terminada em quebra de linha."""

        if not isinstance(message, dict):
            raise TypeError("A mensagem precisa ser um dicionário.")

        message.setdefault("msg_id", str(uuid.uuid4()))
        message.setdefault("ttl", 1)
        message.setdefault("timestamp", utc_timestamp())

        data = json.dumps(
            message,
            ensure_ascii=False
        ).encode("utf-8") + b"\n"

        if len(data) > MAX_LINE + 1:
            raise ValueError("Mensagem maior que o limite de 32 KiB.")

        with self.send_lock:
            self.sock.sendall(data)

        log.debug("[PeerServer] -> %s %s", self.peer_id or self.address, data.decode("utf-8").strip())

    # ------------------------------------------------------------------
    # recepção
    # ------------------------------------------------------------------
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
        """Recebe, divide em linhas e entrega as mensagens JSON."""

        try:
            while self.running:
                data = self.sock.recv(4096)

                if not data:
                    break

                self.buffer += data

                # Linha acima do limite sem fim de linha: protocolo violado.
                if b"\n" not in self.buffer and len(self.buffer) > MAX_LINE:
                    self._protocol_error("line_too_long")
                    return

                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)

                    if not line.strip():
                        continue

                    if len(line) > MAX_LINE:
                        self._protocol_error("line_too_long")
                        return

                    try:
                        message = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        # JSON inválido: ERROR bad_json e fecha a conexão.
                        self._protocol_error("bad_json")
                        return

                    if not isinstance(message, dict):
                        self._protocol_error("bad_json")
                        return

                    log.debug(
                        "[PeerServer] <- %s %s",
                        self.peer_id or self.address,
                        line.decode("utf-8")
                    )

                    if self.on_message:
                        try:
                            self.on_message(self, message)
                        except Exception:
                            log.exception("[PeerServer] handler error")

        except OSError:
            pass

        finally:
            self.close()

            if self.on_disconnect:
                try:
                    self.on_disconnect(self)
                except Exception:
                    log.exception("[PeerServer] disconnect handler error")

    def _protocol_error(self, code):
        """Responde ERROR e encerra a conexão (erro de framing/JSON)."""

        log.warning(
            "[PeerServer] %s sent invalid data (%s) - closing",
            self.peer_id or self.address,
            code
        )

        try:
            self.send_message({
                "type": "ERROR",
                "code": code,
                "message": "invalid line received",
            })
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------
    # encerramento
    # ------------------------------------------------------------------
    def close(self):
        """Encerra a conexão (idempotente)."""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True

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

        return self.running and not self._closed

    @classmethod
    def connect(cls, host, port, timeout=5, **kwargs):
        """Abre uma conexão TCP de saída (outbound) com um peer."""

        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(None)

        return cls(sock, (host, port), inbound=False, **kwargs)