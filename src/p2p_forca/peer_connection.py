import json
import logging
import socket
import threading
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger("PeerServer")
MAX_LINE = 32 * 1024
IO_POLL = 0.25
HANDSHAKE_TIMEOUT = 4


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _invalid_constant(value):
    raise ValueError(f"Constante JSON inválida: {value}")


def decode_line(line):
    message = json.loads(line.decode("utf-8"), parse_constant=_invalid_constant)
    if not isinstance(message, dict):
        raise ValueError("Esperado um objeto JSON.")
    return message


def encode_line(message):
    data = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_LINE:
        raise ValueError("Linha JSON acima de 32 KiB.")
    return data + b"\n"


class PeerConnection:
    """Conexão TCP persistente com leitor e escrita serializada."""

    def __init__(self, sock, address, peer_id=None, on_message=None, on_disconnect=None,
                 *, outbound=False, on_bad_line=None):
        self.sock = sock
        self.address = address
        self.expected_peer_id = peer_id
        self.peer_id = peer_id
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.on_bad_line = on_bad_line

        self.outbound = outbound
        self.features = ()
        self.established = False
        self.hello_id = None
        self.handshake_deadline = time.monotonic() + HANDSHAKE_TIMEOUT
        self.bye_id = None
        self.bye_deadline = None
        self.closing_reason = None
        self.closed = threading.Event()
        self.running = False
        self.send_lock = threading.Lock()
        self.buffer = b""

    def send_message(self, message):
        if not isinstance(message, dict):
            raise TypeError("A mensagem precisa ser um dicionário.")
        message = {"msg_id": str(uuid.uuid4()), "ttl": 1, **message}
        data = encode_line(message)
        with self.send_lock:
            try:
                self.sock.sendall(data)
            except OSError:
                self.close()
                raise
        log.debug("TX %s %s", self.peer_id or self.address, data[:-1].decode("utf-8"))
        return message["msg_id"]

    def begin_bye(self, src, reason):
        if self.closed.is_set() or self.bye_id is not None:
            return
        self.bye_id = str(uuid.uuid4())
        self.bye_deadline = time.monotonic() + 2
        self.closing_reason = reason
        self.send_message({
            "type": "BYE", "msg_id": self.bye_id, "src": src, "dst": self.peer_id,
            "reason": reason,
        })

    def start(self):
        if self.running or self.closed.is_set():
            return
        self.sock.settimeout(IO_POLL)
        self.running = True
        threading.Thread(target=self._receive_loop, daemon=True).start()

    def _receive_loop(self):
        try:
            while self.running:
                deadline = self.bye_deadline if self.established else self.handshake_deadline
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    data = self.sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break

                self.buffer += data
                while self.running and b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    self._receive_line(line)
                if self.running and len(self.buffer) > MAX_LINE:
                    self._bad_line("Linha acima de 32 KiB.")
        except OSError as exc:
            log.debug("Conexão com %s encerrada: %s", self.peer_id or self.address, exc)
        finally:
            self.close()
            if self.on_disconnect:
                self.on_disconnect(self)

    def _receive_line(self, line):
        if len(line) > MAX_LINE:
            self._bad_line("Linha acima de 32 KiB.")
            return
        log.debug("RX %s %s", self.peer_id or self.address, line.decode("utf-8", errors="replace"))
        try:
            message = decode_line(line)
        except (ValueError, RecursionError):
            self._bad_line("Esperado um objeto JSON válido em UTF-8.")
            return
        if self.on_message:
            self.on_message(self, message)

    def _bad_line(self, detail):
        try:
            if self.on_bad_line:
                self.on_bad_line(self, detail)
        finally:
            self.close()

    def close(self):
        self.running = False
        if self.closed.is_set():
            return
        for operation in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                operation()
            except OSError:
                pass
        self.closed.set()

    def is_connected(self):
        return self.running