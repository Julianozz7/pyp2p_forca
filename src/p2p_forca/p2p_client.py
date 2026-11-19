import json
import logging
import random
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from p2p_forca.game_host import GameHost
from p2p_forca.game_player import GamePlayer
from p2p_forca.hangman import Hangman
from p2p_forca.keep_alive import KeepAlive
from p2p_forca.message_router import CONNECTION_TYPES, MessageRouter, valid_peer_id
from p2p_forca.peer_connection import HANDSHAKE_TIMEOUT, PeerConnection, utc_timestamp
from p2p_forca.peer_table import PeerTable
from p2p_forca.rendezvous_connection import RendezvousConnection

log = logging.getLogger(__name__)
peer_log = logging.getLogger("PeerServer")
player_log = logging.getLogger("Player")
rdv_log = logging.getLogger("RDV")
REQUEST_TIMEOUT = 3
FINISHED_RETENTION = 60
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.json"

RESPONSES = {
    "GAME_LIST_OK": "GAME_LIST", "JOIN_OK": "JOIN", "JOIN_ERR": "JOIN",
    "LEAVE_OK": "LEAVE", "GUESS_RESULT": "GUESS",
}


@dataclass
class PendingRequest:
    connection: PeerConnection
    kind: str
    game_id: str | None
    deadline: float
    listing: dict | None = None
    done: threading.Event = field(default_factory=threading.Event)
    response: dict | None = None


class P2PClient:
    """Coordena a malha, as partidas e o ciclo de vida do peer."""

    def __init__(self, config_path="config.json", overrides=None):
        self.config_path = Path(config_path)
        self.config = self._load_config(overrides or {})
        config = self.config
        self.namespace = config["namespace"]
        self.peer_id = f"{config['name']}@{self.namespace}"
        self.listen_port = config["listen_port"]

        self.peer_table = PeerTable()
        self.rendezvous = RendezvousConnection(
            config["rdv_host"], config["rdv_port"], self.namespace, config["name"]
        )
        self.keep_alive = KeepAlive(config["ping_interval"], config["ping_miss_limit"])
        self.host = GameHost(self.peer_id, config["lives"], config["max_players"], config["turn_timeout"])
        self.player = GamePlayer(self.peer_id)
        self.interrupted_game = None
        self.rejoin_at = 0

        self.lock = threading.RLock()
        self.rdv_lock = threading.Lock()
        self.stopped = threading.Event()
        self.running = False
        self.stopping = False
        self.registered = False
        self.refresh_at = 0
        self.server_socket = None
        self.sockets = set()
        self.pending = {}

        self.router = MessageRouter(self.peer_id, self._error)
        handlers = {
            "HELLO": self._hello, "HELLO_OK": self._hello, "PING": self._ping,
            "PONG": self._pong, "BYE": self._bye, "BYE_OK": self._bye,
            "ERROR": self._received_error, "GAME_LIST": self._game_list,
            "JOIN": self._join_host, "LEAVE": self._leave_host, "GUESS": self._guess_host,
            "GAME_START": self._player_message, "STATE": self._player_message,
            "GAME_OVER": self._player_message,
            **dict.fromkeys(RESPONSES, self._response),
        }
        for kind, handler in handlers.items():
            self.router.register_handler(kind, handler)

    def _load_config(self, overrides):
        with self.config_path.open(encoding="utf-8-sig") as source:
            config = json.load(source)
        if not isinstance(config, dict):
            raise ValueError("A configuração precisa ser um objeto JSON.")
        if self.config_path.resolve() != DEFAULT_CONFIG:
            with DEFAULT_CONFIG.open(encoding="utf-8-sig") as source:
                config = {**json.load(source), **config}
        config.update(overrides)
        for key in ("name", "namespace"):
            value = config.get(key)
            if (not isinstance(value, str) or not 1 <= len(value) <= 64
                    or not value.isprintable() or "@" in value):
                raise ValueError(f"{key} deve ter de 1 a 64 caracteres imprimíveis, sem @.")
        for key in ("rdv_host", "words_file"):
            if not isinstance(config.get(key), str) or not config[key]:
                raise ValueError(f"{key} deve ser um texto não vazio.")
        if config.get("log_file") is not None and not isinstance(config["log_file"], str):
            raise ValueError("log_file deve ser um caminho ou null.")
        for key in ("rdv_port", "listen_port", "register_ttl", "ping_miss_limit",
                    "max_reconnect_attempts", "lives", "max_players", "turn_timeout"):
            if type(config.get(key)) is not int or config[key] < 1:
                raise ValueError(f"{key} deve ser um inteiro positivo.")
        for key in ("rdv_port", "listen_port"):
            if config[key] > 65535:
                raise ValueError(f"{key} deve estar entre 1 e 65535.")
        for key in ("discover_interval", "ping_interval", "base_backoff", "max_backoff"):
            value = config.get(key)
            if type(value) not in (int, float) or not 0 < value < float("inf"):
                raise ValueError(f"{key} deve ser um número positivo e finito.")
        return config

    def start(self):
        if self.running:
            return True
        self.running = True
        self.dialers = ThreadPoolExecutor(max_workers=12, thread_name_prefix="dial")
        try:
            self.server_socket = socket.create_server(("0.0.0.0", self.listen_port))
            self.server_socket.settimeout(0.5)
            if not self._register():
                self.stop()
                return False
            for target in (self._accept_connections, self._rdv_loop, self._maintenance):
                threading.Thread(target=target, daemon=True, name=target.__name__).start()
            self.keep_alive.start(self._active_connections)
            log.info("Cliente %s escutando na porta %d", self.peer_id, self.listen_port)
            return True
        except BaseException:
            self.stop()
            raise

    def stop(self):
        with self.lock:
            if not self.running or self.stopping:
                return
            self.stopping = True
            self.stopped.set()
            self.keep_alive.stop()
            if self.server_socket:
                self.server_socket.close()
            if self.host.status in ("OPEN", "RUNNING"):
                self._broadcast(self.host.finish_game(reason="host_shutdown"))
            leave = self._request_leave()

        if leave is not None:
            leave.done.wait(2)
        with self.lock:
            connections = list(self.sockets)
            for connection in connections:
                if connection.established:
                    self._begin_bye(connection, "shutdown")
                else:
                    connection.close()
        deadline = time.monotonic() + 2
        for connection in connections:
            connection.closed.wait(max(0, deadline - time.monotonic()))
            connection.close()

        self.dialers.shutdown(wait=False, cancel_futures=True)
        with self.rdv_lock:
            if self.registered:
                self.rendezvous.unregister(self.listen_port)
        self.running = False
        log.info("Cliente %s encerrado", self.peer_id)

    def _register(self):
        reply = self.rendezvous.register(self.listen_port, self.config["register_ttl"])
        if reply and reply["status"] == "OK":
            ttl, ip = reply.get("ttl"), reply.get("ip")
            if (type(ttl) is int and 1 <= ttl <= 86400 and isinstance(ip, str)
                    and reply.get("port") == self.listen_port):
                self.registered = True
                self.refresh_at = time.monotonic() + ttl / 2
                rdv_log.info("registered %s as %s:%d (ttl=%d)", self.peer_id, ip, self.listen_port, ttl)
                return True
            rdv_log.warning("Resposta REGISTER inválida.")
        self.refresh_at = time.monotonic() + min(10, self.config["discover_interval"])
        return False

    def _rdv_loop(self):
        discover_at = 0
        while not self.stopped.is_set():
            with self.rdv_lock:
                if self.stopping:
                    return
                now = time.monotonic()
                if now >= self.refresh_at:
                    self._register()
                if now >= discover_at and not self.stopping:
                    self._discover(self.namespace, reconcile=True)
                    discover_at = time.monotonic() + self.config["discover_interval"]
            wait_for = min(discover_at, self.refresh_at) - time.monotonic()
            self.stopped.wait(max(0, wait_for))

    def discover_peers(self, namespace, force=False):
        with self.rdv_lock:
            if self.stopping:
                return None
            return self._discover(namespace, reconcile=namespace == self.namespace, force=force)

    def _discover(self, namespace, reconcile=False, force=False):
        reply = self.rendezvous.discover(namespace)
        if not self.stopping and reply and reply.get("message") == "peer_not_registered" and self._register():
            reply = self.rendezvous.discover(namespace)
        if not reply or reply["status"] != "OK" or not isinstance(reply.get("peers"), list):
            return None

        peers = {}
        for entry in reply["peers"]:
            if not isinstance(entry, dict):
                continue
            name, room = entry.get("name"), entry.get("namespace")
            if not isinstance(name, str) or not isinstance(room, str):
                continue
            peer_id = f"{name}@{room}"
            if peer_id == self.peer_id or not valid_peer_id(peer_id):
                continue
            ip, port = entry.get("ip"), entry.get("port")
            if not isinstance(ip, str) or not ip or type(port) is not int or not 1 <= port <= 65535:
                continue
            if namespace is None or room == namespace:
                peers[peer_id] = (ip, port)
        if reconcile:
            with self.lock:
                if not self.stopping:
                    self._reconcile(peers, force)
        return peers

    def _reconcile(self, discovered, force):
        for peer in self.peer_table.get_all_peers():
            if peer["peer_id"] not in discovered:
                peer.update(advertised=False, status="GONE", duplicate_until=None)
                connection = peer["connection"]
                if connection:
                    self._begin_bye(connection, "idle")
                else:
                    self._game_peer_lost(peer["peer_id"], recover=True)
        for peer_id, address in discovered.items():
            peer = self.peer_table.get_peer(peer_id)
            if peer is None:
                peer = self.peer_table.add_peer(peer_id, address)
            changed = not peer["advertised"] or peer["address"] != address
            peer.update(advertised=True, address=address)
            if (changed or force) and peer["status"] != "CONNECTED":
                peer.update(attempts=0, retry_at=0)
                if not peer["dialing"]:
                    peer["status"] = "DISCOVERED"

    def _maintenance(self):
        while not self.stopped.wait(0.1):
            with self.lock:
                if self.stopping:
                    return
                now = time.monotonic()
                self._expire_host(now)
                self._expire_requests(now)
                for peer in self.peer_table.get_all_peers():
                    if peer["duplicate_until"] is not None and now >= peer["duplicate_until"]:
                        peer["duplicate_until"] = None
                        self._game_peer_lost(peer["peer_id"], recover=True)
                    if (peer["advertised"] and not peer["dialing"] and peer["connection"] is None
                            and peer["status"] in ("DISCOVERED", "STALE") and now >= peer["retry_at"]):
                        peer.update(dialing=True, status="CONNECTING")
                        self.dialers.submit(self._dial, peer["peer_id"], peer["address"])
                self._try_rejoin(now)

    def _dial(self, peer_id, address):
        try:
            sock = socket.create_connection(address, timeout=2)
        except OSError as error:
            with self.lock:
                peer = self.peer_table.get_peer(peer_id)
                peer["dialing"] = False
                if peer["address"] == address:
                    self._retry(peer)
                elif peer["status"] == "CONNECTING":
                    peer["status"] = "DISCOVERED" if peer["advertised"] else "GONE"
            peer_log.debug("Falha ao conectar a %s (%s): %s", peer_id, address, error)
            return

        with self.lock:
            peer = self.peer_table.get_peer(peer_id)
            if self.stopping or not peer["advertised"] or peer["address"] != address:
                sock.close()
                peer["dialing"] = False
                if peer["status"] == "CONNECTING":
                    peer["status"] = "DISCOVERED" if peer["advertised"] else "GONE"
                return
            connection = self._new_connection(sock, address, peer_id)
            connection.hello_id = str(uuid.uuid4())
            self._send(connection, "HELLO", msg_id=connection.hello_id, **self._hello_fields())

    def _accept_connections(self):
        while not self.stopping:
            try:
                sock, address = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                if self.stopping:
                    sock.close()
                    return
                self._new_connection(sock, address)

    def _new_connection(self, sock, address, expected_peer=None):
        connection = PeerConnection(sock, address, expected_peer, self._on_message, self._on_disconnect,
                                    outbound=expected_peer is not None, on_bad_line=self._bad_line)
        self.sockets.add(connection)
        connection.start()
        return connection

    def _hello_fields(self):
        return {"peer_id": self.peer_id, "version": "1.0", "features": ["hangman"]}

    def _hello(self, connection, message):
        kind = message["type"]
        remote = message["peer_id"]
        if (self.stopping or message["version"] != "1.0" or remote == self.peer_id
                or remote.split("@")[1] != self.namespace
                or (connection.outbound and (kind != "HELLO_OK" or remote != connection.expected_peer_id
                                             or message["msg_id"] != connection.hello_id))
                or (not connection.outbound and kind != "HELLO")):
            connection.peer_id = connection.peer_id or remote
            self._error(connection, "bad_request", "Handshake incompatível.", message)
            connection.close()
            return

        connection.peer_id = remote
        connection.features = tuple(message["features"])
        if not connection.outbound:
            if not self._send(connection, "HELLO_OK", msg_id=message["msg_id"], **self._hello_fields()):
                return
        connection.established = True

        peer = self.peer_table.get_peer(remote)
        if peer is None:
            peer = self.peer_table.add_peer(remote, connection.address)
        active = peer["connection"]
        if (active is not None and active.closed.is_set() and active.closing_reason != "duplicate"):
            self._game_peer_lost(remote, recover=active.closing_reason != "shutdown")
        if active is not None and not active.closed.is_set() and active.bye_id is None:
            prefer_outbound = self.peer_id < remote
            if active.outbound == prefer_outbound or active.outbound == connection.outbound:
                peer_log.info("Duplicate connection with %s — keeping %s", remote, self._direction(active))
                self._begin_bye(connection, "duplicate")
                return
            peer_log.info("Duplicate connection with %s — keeping %s", remote, self._direction(connection))
            self._begin_bye(active, "duplicate")

        peer.update(connection=connection, attempts=0, retry_at=0, duplicate_until=None)
        self.peer_table.update_status(remote, "CONNECTED")
        if self.interrupted_game and self.interrupted_game.host_id == remote:
            self.rejoin_at = time.monotonic() + 0.5
        peer_log.info("%s connected: %s (features=%s)", self._direction(connection),
                      remote, ",".join(connection.features))

    @staticmethod
    def _direction(connection):
        return "outbound" if connection.outbound else "inbound"

    def _retry(self, peer, *, failed_attempt=True):
        if peer["connection"] is not None or self.stopping:
            return
        if not peer["advertised"]:
            peer["status"] = "GONE"
            return
        if failed_attempt:
            peer["attempts"] += 1
        if peer["attempts"] >= self.config["max_reconnect_attempts"]:
            self.peer_table.update_status(peer["peer_id"], "GONE")
            peer_log.info("%s: limite de tentativas atingido", peer["peer_id"])
            return
        base, cap = self.config["base_backoff"], self.config["max_backoff"]
        exponent = max(0, min(peer["attempts"] - 1, 1023))
        delay = min(cap, base * (2.0 ** exponent))
        peer["retry_at"] = time.monotonic() + delay
        self.peer_table.update_status(peer["peer_id"], "STALE")

    def _on_disconnect(self, connection):
        with self.lock:
            self.sockets.discard(connection)
            self.keep_alive.forget(connection)
            self._fail_requests(connection)
            peer = self.peer_table.get_peer(connection.peer_id)
            if peer is None:
                return
            if connection.outbound:
                peer["dialing"] = False
            if peer["connection"] is not connection:
                if not connection.established and connection.address == peer["address"]:
                    self._retry(peer)
                return

            peer["connection"] = None
            peer_log.info("%s disconnected (%s)", connection.peer_id, connection.closing_reason or "STALE")
            if connection.closing_reason == "duplicate":
                peer["duplicate_until"] = time.monotonic() + HANDSHAKE_TIMEOUT
            else:
                self._game_peer_lost(connection.peer_id, recover=connection.closing_reason != "shutdown")
            self._retry(peer, failed_attempt=False)

    def _game_peer_lost(self, peer_id, recover):
        if self.host.remove_player(peer_id, disconnected=recover):
            self._after_removal()
        if peer_id == self.player.host_id:
            player_log.info("[Game] Host %s unreachable — game aborted", peer_id)
            self.interrupted_game = self.player if recover and not self.stopping else None
            self.player = GamePlayer(self.peer_id)

    def _active_connections(self, *, game_only=False):
        with self.lock:
            return [connection for row in self.peer_table.get_connected_peers()
                    if (connection := row["connection"]) is not None
                    and connection.bye_id is None and not connection.closed.is_set()
                    and (not game_only or "hangman" in connection.features)]

    def _connection_to(self, peer_id):
        row = self.peer_table.get_peer(peer_id)
        connection = row["connection"] if row else None
        if (connection and row["status"] == "CONNECTED" and connection.bye_id is None
                and not connection.closed.is_set() and "hangman" in connection.features):
            return connection
        return None

    def _on_message(self, connection, message):
        with self.lock:
            self.router.route(connection, message)

    def _send(self, connection, kind, **fields):
        message = {"type": kind, **fields}
        if kind not in CONNECTION_TYPES:
            message.update(src=self.peer_id, dst=connection.peer_id or "")
            message.setdefault("timestamp", utc_timestamp())
        try:
            connection.send_message(message)
        except (OSError, ValueError) as error:
            peer_log.warning("Falha ao enviar %s para %s: %s", kind, connection.peer_id, error)
            return False
        return True

    def _error(self, connection, code, detail, message=None):
        fields = {"code": code, "message": detail}
        if message and isinstance(message.get("msg_id"), str) and len(message["msg_id"]) <= 36:
            fields["ref_msg_id"] = message["msg_id"]
        self._send(connection, "ERROR", **fields)

    def _bad_line(self, connection, detail):
        self._error(connection, "bad_json", detail)

    def _received_error(self, connection, message):
        peer_log.warning("ERROR de %s (%s): %s", connection.peer_id, message["code"], message["message"])
        request = self.pending.pop((connection, message.get("ref_msg_id")), None)
        if request:
            request.response = message
            request.done.set()

    def _ping(self, connection, message):
        self._send(connection, "PONG", msg_id=message["msg_id"], timestamp=message["timestamp"])

    def _pong(self, connection, message):
        self.keep_alive.receive_pong(connection, message)

    def _begin_bye(self, connection, reason):
        try:
            connection.begin_bye(self.peer_id, reason)
        except OSError:
            pass

    def _bye(self, connection, message):
        if message["type"] == "BYE":
            connection.closing_reason = message["reason"]
            self._send(connection, "BYE_OK", msg_id=message["msg_id"])
            connection.close()
        elif message["msg_id"] == connection.bye_id:
            connection.close()
        else:
            self._error(connection, "invalid_state", "BYE_OK sem BYE correspondente.", message)

    def _eligible(self, connection, message):
        row = self.peer_table.get_peer(connection.peer_id)
        if row is not None and row["connection"] is connection and connection.bye_id is None:
            return True
        self._error(connection, "invalid_state", "Conexão fora da malha ativa.", message)
        return False

    def _begin_request(self, connection, kind, *, listing=None, **fields):
        request_id = str(uuid.uuid4())
        request = PendingRequest(connection, kind, fields.get("game_id"),
                                 time.monotonic() + REQUEST_TIMEOUT, listing)
        key = connection, request_id
        self.pending[key] = request
        if not self._send(connection, kind, msg_id=request_id, **fields):
            self.pending.pop(key)
            request.done.set()
        return request

    def _wait_reply(self, request):
        request.done.wait(max(0, request.deadline - time.monotonic()))
        with self.lock:
            self._expire_requests(time.monotonic())
            return request.response

    def _expire_requests(self, now):
        for key, request in list(self.pending.items()):
            if now >= request.deadline:
                self.pending.pop(key)
                if request.kind in ("JOIN", "LEAVE"):
                    # Sem resposta, fechar o TCP evita deixar um jogador preso no host.
                    request.connection.close()
                request.done.set()

    def _fail_requests(self, connection):
        for key, request in list(self.pending.items()):
            if request.connection is connection:
                self.pending.pop(key)
                request.done.set()

    def _response(self, connection, message):
        key = connection, message["msg_id"]
        request = self.pending.get(key)
        if (request is None or request.kind != RESPONSES[message["type"]]
                or request.game_id != message.get("game_id")):
            self._error(connection, "invalid_state", "Resposta sem requisição compatível.", message)
            return

        kind = message["type"]
        if kind == "JOIN_OK":
            if not self._eligible(connection, message):
                self.pending.pop(key)
                request.done.set()
                return
            if self.peer_id not in message["players"]:
                self._error(connection, "bad_request", "JOIN_OK não inclui o jogador.", message)
                connection.close()
                return
            self._apply_join(connection, message, request.listing)
        elif kind == "JOIN_ERR":
            if self.interrupted_game and request.game_id == self.interrupted_game.game_id:
                self.interrupted_game = None
            player_log.info("[Game] Entrada recusada: %s", message["reason"])
        elif kind == "LEAVE_OK":
            if self.player.host_id == connection.peer_id and self.player.game_id == request.game_id:
                self.player.leave_game()
        elif kind == "GUESS_RESULT":
            if message["accepted"]:
                text = "correto" if message["correct"] else "incorreto"
            else:
                text = message["reason"]
            player_log.info("[Game] Palpite: %s", text)

        self.pending.pop(key)
        request.response = message
        request.done.set()

    def _apply_join(self, connection, message, listing):
        recovery = self.interrupted_game
        if recovery and recovery.game_id == message["game_id"]:
            self.player.word_length = recovery.word_length
            self.player.initial_lives = recovery.initial_lives
            if message["rejoin"]:
                self.player.word_hash = recovery.word_hash
                self.player.turn_timeout = recovery.turn_timeout
        if listing:
            self.player.word_length = listing["word_length"]
            self.player.lives = listing["lives"]
            if listing["status"] == "OPEN":
                self.player.initial_lives = listing["lives"]
        if self.player.word_length is not None:
            self.player.masked = "_" * self.player.word_length
        self.player.join_game(connection.peer_id, message["game_id"], message["players"], message["rejoin"])
        self.interrupted_game = None
        player_log.info("[Game] %s %s", "Reconectado a" if message["rejoin"] else "Entrou em", message["game_id"])

    def _try_rejoin(self, now):
        game = self.interrupted_game
        if game and now >= self.rejoin_at and self._available():
            connection = self._connection_to(game.host_id)
            if connection:
                self.rejoin_at = now + REQUEST_TIMEOUT
                self._begin_request(connection, "JOIN", game_id=game.game_id)

    def _game_list(self, connection, message):
        if self._eligible(connection, message):
            self._send(connection, "GAME_LIST_OK", msg_id=message["msg_id"], games=self.host.get_listing())

    def _join_host(self, connection, message):
        if not self._eligible(connection, message):
            return
        game_id = message["game_id"]
        reason, rejoin = self.host.join(connection.peer_id, game_id)
        if reason:
            self._send(connection, "JOIN_ERR", msg_id=message["msg_id"], game_id=game_id, reason=reason)
            return
        self._send(connection, "JOIN_OK", msg_id=message["msg_id"], game_id=game_id,
                   players=self.host.players.copy(), rejoin=rejoin)
        if rejoin:
            self._broadcast(self.host.next_state())

    def _leave_host(self, connection, message):
        if not self._eligible(connection, message):
            return
        if message["game_id"] != self.host.game_id or not self.host.remove_player(connection.peer_id):
            self._error(connection, "invalid_state", "O peer não participa desta partida.", message)
            return
        self._send(connection, "LEAVE_OK", msg_id=message["msg_id"], game_id=message["game_id"])
        self._after_removal()

    def _after_removal(self):
        if not self.host.players:
            self.host.finish_game(reason="no_players")
            self.host.reset()
        elif self.host.status == "RUNNING":
            self._broadcast(self.host.next_state())

    def _guess_host(self, connection, message):
        if not self._eligible(connection, message):
            return
        if self.host.game_id != message["game_id"] or connection.peer_id not in self.host.players:
            self._error(connection, "invalid_state", "O peer não participa desta partida.", message)
            return
        if self.host.expire_turn():
            self._broadcast(self.host.next_state())
        kind = "letter" if "letter" in message else "word"
        reply = self.host.guess(connection.peer_id, kind, message[kind])
        self._send(connection, "GUESS_RESULT", msg_id=message["msg_id"], game_id=message["game_id"], **reply)
        if reply["accepted"]:
            self._broadcast(self.host.next_state())
            if self.host.game.is_finished():
                winner = connection.peer_id if self.host.game.is_won() else None
                self._broadcast(self.host.finish_game(winner=winner))

    def _player_message(self, connection, message):
        if not self._eligible(connection, message):
            return
        kind = message["type"]
        status = self.player.status
        if (connection.peer_id != self.player.host_id or message["game_id"] != self.player.game_id
                or (kind == "STATE" and status != "PLAYING") or (kind == "GAME_START" and status != "JOINED")):
            self._error(connection, "invalid_state", "Mensagem fora do contexto da partida local.", message)
            return
        if kind != "GAME_OVER" and self.peer_id not in message["players"]:
            self._error(connection, "bad_request", "O estado não inclui o jogador local.", message)
            return

        if kind == "GAME_START":
            self.player.start_game(message)
            player_log.info("[Game] Partida iniciada: %s", self.player.game_id)
        elif kind == "STATE":
            if self.player.word_length is not None and self.player.word_length != len(message["masked"]):
                self._error(connection, "bad_request", "A máscara não corresponde ao tamanho da palavra.", message)
            else:
                self.player.apply_state(message)
        else:
            verified = self.player.verify_game_over(message)
            verdict = {True: "hash OK", False: "hash divergente", None: "sem hash recebido"}[verified]
            player_log.info("[Game] GAME_OVER %s palavra=%s vencedor=%s pontos=%s (%s)",
                            message["result"], message["word"], message["winner"], message["scores"], verdict)
            self.player.leave_game()

    def _broadcast(self, message):
        fields = message.copy()
        kind = fields.pop("type")
        for player in self.host.players:
            connection = self._connection_to(player)
            if connection:
                self._send(connection, kind, **fields)

    def _expire_host(self, now):
        if self.host.expire_turn():
            self._broadcast(self.host.next_state())
        if self.host.status == "FINISHED" and now >= self.host.finished_at + FINISHED_RETENTION:
            self.host.reset()

    def _available(self):
        return (self.host.status not in ("OPEN", "RUNNING") and self.player.status == "IDLE"
                and not any(request.kind == "JOIN" for request in self.pending.values()))

    def list_games(self):
        with self.lock:
            requests = [self._begin_request(conn, "GAME_LIST") for conn in self._active_connections(game_only=True)]
        games = []
        for request in requests:
            response = self._wait_reply(request)
            if response and response["type"] == "GAME_LIST_OK":
                games.extend(game for game in response["games"] if game["status"] == "OPEN")
        return sorted(games, key=lambda game: game["game_id"])

    def create_game(self, word, max_players=None):
        if max_players is not None and max_players < 1:
            raise ValueError("O número máximo de jogadores deve ser positivo.")
        if word == "--random":
            path = Path(self.config["words_file"])
            if not path.is_absolute():
                path = self.config_path.parent / path
            words = [line for line in path.read_text(encoding="utf-8-sig").splitlines()
                     if Hangman.valid_word(line)]
            if not words:
                raise ValueError(f"Nenhuma palavra válida em {path}.")
            word = random.choice(words)
        with self.lock:
            self._expire_host(time.monotonic())
            self._require_available()
            if not self.host.create_game(word, max_players):
                raise ValueError("A partida anterior ainda aparece como FINISHED por até 60 s.")
            self.interrupted_game = None
            return self.host.game_id

    def start_game(self):
        with self.lock:
            message = self.host.start_game()
            if message is None:
                raise ValueError("Abra uma partida e aguarde pelo menos um jogador.")
            self._broadcast(message)
            self._broadcast(self.host.next_state())

    def cancel_game(self):
        with self.lock:
            if self.host.status not in ("OPEN", "RUNNING"):
                raise ValueError("Você não hospeda uma partida ativa.")
            self._broadcast(self.host.finish_game(reason="host_shutdown"))

    def join_game(self, host_id):
        with self.lock:
            self._require_available()
            connection = self._connection_to(host_id)
            if connection is None:
                raise ValueError("Host sem conexão de jogo ativa. Consulte /conn.")
            listing = self._begin_request(connection, "GAME_LIST")

        response = self._wait_reply(listing)
        if not response or response["type"] != "GAME_LIST_OK":
            raise ValueError("O host não respondeu à consulta de partidas.")
        if not response["games"]:
            raise ValueError("O peer não está hospedando uma partida.")

        with self.lock:
            self._require_available()
            if self._connection_to(host_id) is not connection:
                raise ValueError("A conexão com o host mudou. Tente novamente.")
            listing = response["games"][0]
            request = self._begin_request(connection, "JOIN", game_id=listing["game_id"], listing=listing)
        reply = self._wait_reply(request)
        if reply is None or reply["type"] == "ERROR":
            raise ValueError("O host não confirmou a entrada.")

    def _require_available(self):
        if not self._available():
            raise ValueError("Você já está em uma partida ou solicitou entrada.")

    def _request_leave(self):
        if self.player.status != "IDLE":
            connection = self._connection_to(self.player.host_id)
            if connection:
                return self._begin_request(connection, "LEAVE", game_id=self.player.game_id)
        return None

    def leave_game(self):
        with self.lock:
            self.interrupted_game = None
            request = self._request_leave()
            if request is None:
                raise ValueError("Você não está em uma partida como jogador.")
        reply = self._wait_reply(request)
        if not reply or reply["type"] != "LEAVE_OK":
            raise ValueError("O host não confirmou a saída.")

    def send_guess(self, kind, value):
        with self.lock:
            connection = self._connection_to(self.player.host_id)
            if self.player.status != "PLAYING" or connection is None:
                raise ValueError("Você precisa estar jogando para enviar um palpite.")
            self._begin_request(connection, "GUESS", game_id=self.player.game_id, **{kind: value})

    def connection_status(self):
        with self.lock:
            return [(conn.peer_id, self._direction(conn), "CONNECTED", self.keep_alive.average_rtt(conn))
                    for conn in self._active_connections()]

    def game_status(self):
        with self.lock:
            if self.host.status in ("OPEN", "RUNNING", "FINISHED") and self.player.status == "IDLE":
                state = self.host.get_state()
                if self.host.status != "RUNNING":
                    state["turn"] = None
                return dict(state, host=self.peer_id, status=self.host.status, initial_lives=self.host.lives,
                            deadline=self.host.deadline)
            player = self.player
            if player.status == "IDLE":
                return None
            return {
                "game_id": player.game_id, "host": player.host_id, "status": player.status,
                "masked": player.masked, "lives": player.lives, "initial_lives": player.initial_lives,
                "wrong_letters": player.wrong_letters.copy(), "turn": player.turn,
                "players": player.players.copy(), "deadline": player.deadline,
            }