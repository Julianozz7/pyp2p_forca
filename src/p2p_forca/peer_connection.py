"""Threaded TCP connection using newline-delimited JSON."""
import json, socket, threading, uuid, logging
from datetime import datetime, timezone

MAX_LINE = 32768


class PeerConnection:
    def __init__(self, sock, address, peer_id=None, on_message=None, on_disconnect=None,
                 direction="outbound"):
        self.sock, self.address, self.peer_id = sock, address, peer_id
        self.on_message, self.on_disconnect = on_message, on_disconnect
        self.direction = direction
        self.features = []
        self.running = False
        self.send_lock = threading.Lock()
        self.buffer = b""
        self._closed_callback = False

    def send_message(self, message):
        if not isinstance(message, dict):
            raise TypeError("A mensagem precisa ser um dicionário.")
        msg = dict(message)
        msg.setdefault("msg_id", str(uuid.uuid4()))
        msg.setdefault("ttl", 1)
        if msg.get("type") in ("PING", "PONG") or "timestamp" in msg:
            msg.setdefault("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        if msg.get("ttl") != 1:
            raise ValueError("ttl peer-peer deve ser 1")
        data = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(data) - 1 > MAX_LINE:
            raise ValueError("Mensagem excede 32 KiB")
        logging.getLogger(__name__).debug("SEND %s", data.decode("utf-8").rstrip())
        with self.send_lock:
            if not self.running and self.sock.fileno() < 0:
                raise OSError("Conexão fechada")
            self.sock.sendall(data)

    def start(self):
        if self.running: return
        self.running = True
        threading.Thread(target=self._receive_loop, daemon=True).start()

    def _receive_loop(self):
        try:
            while self.running:
                chunk = self.sock.recv(4096)
                if not chunk: break
                self.buffer += chunk
                if len(self.buffer) > MAX_LINE and b"\n" not in self.buffer:
                    self._send_error(None, "bad_json", "line_too_long")
                    break
                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    if len(line) > MAX_LINE:
                        self._send_error(None, "bad_json", "line_too_long")
                        return
                    if not line.strip():
                        self._send_error(None, "bad_json", "Empty request line")
                        return
                    try:
                        msg = json.loads(line.decode("utf-8"))
                        if not isinstance(msg, dict): raise ValueError("JSON deve ser objeto")
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                        self._send_error(None, "bad_json", str(exc))
                        return
                    logging.getLogger(__name__).debug("RECV %s", line.decode("utf-8", "replace"))
                    if self.on_message: self.on_message(self, msg)
        except OSError:
            pass
        finally:
            self.close(notify=True)

    def _send_error(self, ref, code, message):
        try:
            msg = {"type":"ERROR","code":code,"message":message}
            if getattr(self,"local_peer_id",None) and self.peer_id:
                msg.update({"src":self.local_peer_id,"dst":self.peer_id})
            if ref: msg["ref_msg_id"] = ref
            self.send_message(msg)
        except OSError: pass

    def close(self, notify=False):
        was_running = self.running
        self.running = False
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        try: self.sock.close()
        except OSError: pass
        if notify and not self._closed_callback:
            self._closed_callback = True
            if self.on_disconnect: self.on_disconnect(self)
    def is_connected(self):
        return self.running and self.sock.fileno() >= 0
