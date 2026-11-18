"""Validação do envelope e despacho de mensagens por ``type``."""

import logging
import threading
from collections import OrderedDict

log = logging.getLogger("Router")

# Mensagens de conexão: o par já é conhecido pelo socket, sem src/dst.
CONNECTION_TYPES = {"HELLO", "HELLO_OK", "PING", "PONG"}

DEDUP_LIMIT = 5000


class MessageRouter:
    """Recebe mensagens e encaminha para os handlers correspondentes."""

    def __init__(self, local_peer_id=None):
        self.handlers = {}
        self.local_peer_id = local_peer_id
        self._seen = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # registro de handlers
    # ------------------------------------------------------------------
    def register_handler(self, message_type, handler):
        """Registra uma função para processar determinado tipo de mensagem."""
        if not callable(handler):
            raise TypeError("O handler precisa ser uma função.")

        self.handlers[message_type] = handler

    def unregister_handler(self, message_type):
        """Remove um handler registrado."""
        return self.handlers.pop(message_type, None)

    # ------------------------------------------------------------------
    # utilitários
    # ------------------------------------------------------------------
    @property
    def processed_messages(self):
        return set(self._seen)

    def _send_error(self, connection, code, text, message):
        """Responde ERROR (nunca em resposta a outro ERROR)."""
        if message.get("type") == "ERROR":
            return

        reply = {
            "type": "ERROR",
            "code": code,
            "message": text,
        }

        if message.get("msg_id"):
            reply["ref_msg_id"] = message["msg_id"]

        if self.local_peer_id:
            reply["src"] = self.local_peer_id

        peer_id = getattr(connection, "peer_id", None)

        if isinstance(peer_id, str) and peer_id:
            reply["dst"] = peer_id

        try:
            connection.send_message(reply)
        except Exception:
            pass

    def _is_duplicate(self, connection, message_type, msg_id):
        key = (id(connection), message_type, msg_id)

        with self._lock:
            if key in self._seen:
                return True

            self._seen[key] = True

            while len(self._seen) > DEDUP_LIMIT:
                self._seen.popitem(last=False)

        return False

    # ------------------------------------------------------------------
    # despacho
    # ------------------------------------------------------------------
    def route(self, connection, message):
        """Valida o envelope e encaminha a mensagem. Devolve True se um
        handler a processou."""

        if not isinstance(message, dict):
            return False

        message_type = message.get("type")

        if not isinstance(message_type, str) or not message_type:
            self._send_error(
                connection, "bad_request", "missing type", message
            )
            return False

        msg_id = message.get("msg_id")

        if not isinstance(msg_id, str) or not msg_id:
            self._send_error(
                connection, "bad_request", "missing msg_id", message
            )
            return False

        # ttl é sempre 1; qualquer outro valor é descartado.
        if "ttl" in message and message["ttl"] != 1:
            log.debug("[Router] %s discarded (ttl=%r)", message_type,
                      message["ttl"])
            return False

        if message_type not in CONNECTION_TYPES:
            src, dst = message.get("src"), message.get("dst")

            if not isinstance(src, str) or not isinstance(dst, str):
                self._send_error(
                    connection, "bad_request",
                    "src and dst are required", message
                )
                return False

            if self.local_peer_id and dst != self.local_peer_id:
                self._send_error(
                    connection, "not_for_me",
                    f"message addressed to {dst}", message
                )
                return False

        if self._is_duplicate(connection, message_type, msg_id):
            log.debug("[Router] duplicate %s %s ignored", message_type,
                      msg_id)
            return False

        handler = self.handlers.get(message_type)

        if handler is None:
            self._send_error(
                connection, "unknown_type",
                f"unknown type {message_type}", message
            )
            return False

        handler(connection, message)

        return True