"""Orquestração do peer: Rendezvous, malha de conexões, jogo e encerramento."""

import json
import logging
import re
import socket
import threading
import time
import uuid
from pathlib import Path

from p2p_forca.game_host import GameHost, random_word
from p2p_forca.game_player import GamePlayer
from p2p_forca.keep_alive import KeepAlive
from p2p_forca.message_router import MessageRouter
from p2p_forca.peer_connection import PeerConnection
from p2p_forca.peer_table import PeerTable
from p2p_forca.rendezvous_connection import RendezvousConnection

log = logging.getLogger("Client")
server_log = logging.getLogger("PeerServer")

PROTOCOL_VERSION = "1.0"
FEATURES = ["hangman"]

LOBBY_AND_GAME_TYPES = {
    "GAME_LIST", "GAME_LIST_OK", "JOIN", "JOIN_OK", "JOIN_ERR",
    "LEAVE", "LEAVE_OK", "GAME_START", "STATE", "GUESS",
    "GUESS_RESULT", "GAME_OVER",
}

DEFAULT_CONFIG = {
    "rdv_host": "pyp2p.mfcaetano.cc",
    "rdv_port": 8080,
    "namespace": "CIC",
    "name": "alice",
    "listen_port": 8081,
    "register_ttl": 7200,
    "discover_interval": 20,
    "ping_interval": 30,
    "ping_miss_limit": 3,
    "max_reconnect_attempts": 5,
    "base_backoff": 2,
    "max_backoff": 60,
    "lives": 6,
    "max_players": 4,
    "turn_timeout": 30,
    "words_file": "words.txt",
    "log_file": None,
}

PEER_ID_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


class P2PClient:
    """Coordena a comunicação e o funcionamento do cliente P2P."""

    def __init__(self, config_path="config.json", overrides=None,
                 notify=None):
        self.config_path = Path(config_path)
        self.notify = notify or print

        self.config = self._load_config(overrides or {})

        self.name = self.config["name"]
        self.namespace = self.config["namespace"]
        self.peer_id = f"{self.name}@{self.namespace}"
        self.listen_port = self.config["listen_port"]

        self.peer_table = PeerTable()
        self.router = MessageRouter(local_peer_id=self.peer_id)

        self.rendezvous = RendezvousConnection(
            self.config["rdv_host"],
            self.config["rdv_port"],
            self.namespace,
            self.peer_id,
            name=self.name
        )

        self.keep_alive = KeepAlive(
            self.peer_table,
            self.config["ping_interval"],
            self.config["ping_miss_limit"],
            on_stale=self._on_stale
        )

        self.game_host = GameHost(
            self.peer_id,
            lives=self.config["lives"],
            max_players=self.config["max_players"],
            turn_timeout=self.config["turn_timeout"],
            send=self.send_to_peer,
            notify=self.notify
        )

        self.game_player = GamePlayer(
            self.peer_id,
            send=self.send_to_peer,
            notify=self.notify
        )

        self.server_socket = None
        self.connections = {}          # peer_id -> PeerConnection (prontas)
        self.pending = set()           # conexões ainda sem handshake
        self.running = False
        self.stopping = False

        self.lock = threading.RLock()
        self._connecting = set()       # peer_ids com conexão em andamento
        self._stop_event = threading.Event()
        self._threads = []

        # /games
        self._games_cond = threading.Condition()
        self._games_replies = {}       # msg_id -> lista de games (ou None)

        self._register_handlers()

    # ------------------------------------------------------------------
    # configuração
    # ------------------------------------------------------------------
    def _load_config(self, overrides):
        """Carrega config.json, aplica padrões e sobrescritas da CLI."""

        config = dict(DEFAULT_CONFIG)

        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                config.update(json.load(file))

        except FileNotFoundError:
            pass    # usa os padrões

        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Não foi possível carregar {self.config_path}: {error}"
            ) from error

        for key, value in overrides.items():
            if value is not None:
                config[key] = value

        for key in ("rdv_host", "rdv_port", "namespace", "name",
                    "listen_port"):
            if config.get(key) in (None, ""):
                raise ValueError(f"Configuração obrigatória ausente: {key}")

        if "@" in str(config["name"]) or "@" in str(config["namespace"]):
            raise ValueError("name e namespace não podem conter '@'.")

        return config

    def words_path(self):
        """Caminho do words.txt (relativo ao config.json ou ao diretório
        atual)."""
        path = Path(self.config.get("words_file") or "words.txt")

        if path.is_absolute() or path.exists():
            return path

        candidate = self.config_path.parent / path

        return candidate if candidate.exists() else path

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------
    def _register_handlers(self):
        r = self.router

        # sessão
        r.register_handler("HELLO", self._handle_hello)
        r.register_handler("HELLO_OK", self._handle_hello_ok)
        r.register_handler("PING", self.keep_alive.handle_ping)
        r.register_handler("PONG", self.keep_alive.handle_pong)
        r.register_handler("BYE", self._handle_bye)
        r.register_handler("BYE_OK", self._handle_bye_ok)
        r.register_handler("ERROR", self._handle_error)

        # lobby (lado host / lado consulta)
        r.register_handler("GAME_LIST", self.game_host.handle_game_list)
        r.register_handler("GAME_LIST_OK", self._handle_game_list_ok)
        r.register_handler("JOIN", self.game_host.handle_join)
        r.register_handler("LEAVE", self.game_host.handle_leave)
        r.register_handler("GUESS", self.game_host.handle_guess)

        # jogador
        r.register_handler("JOIN_OK", self.game_player.handle_join_ok)
        r.register_handler("JOIN_ERR", self.game_player.handle_join_err)
        r.register_handler("LEAVE_OK", self.game_player.handle_leave_ok)
        r.register_handler("GAME_START", self.game_player.handle_game_start)
        r.register_handler("STATE", self.game_player.handle_state)
        r.register_handler(
            "GUESS_RESULT", self.game_player.handle_guess_result
        )
        r.register_handler("GAME_OVER", self.game_player.handle_game_over)

    def _on_message(self, connection, message):
        """Porta de entrada de todas as mensagens recebidas."""

        message_type = message.get("type")

        # Até o HELLO/HELLO_OK nenhuma outra mensagem é aceita.
        if not connection.ready and message_type not in ("HELLO", "HELLO_OK"):
            self._reply_error(
                connection, message, "invalid_state",
                "handshake not completed"
            )
            return

        if connection.ready and message_type in LOBBY_AND_GAME_TYPES:
            if "hangman" not in connection.features:
                self._reply_error(
                    connection, message, "invalid_state",
                    "peer did not announce the hangman feature"
                )
                return

        # src deve ser o peer identificado na conexão (sem relay).
        if connection.ready and "src" in message:
            if message["src"] != connection.peer_id:
                self._reply_error(
                    connection, message, "bad_request",
                    "src does not match the connection peer"
                )
                return

        self.router.route(connection, message)

    def _reply_error(self, connection, message, code, text):
        if message.get("type") == "ERROR":
            return

        reply = {
            "type": "ERROR",
            "src": self.peer_id,
            "ref_msg_id": message.get("msg_id"),
            "code": code,
            "message": text,
        }

        if connection.peer_id:
            reply["dst"] = connection.peer_id

        try:
            connection.send_message(reply)
        except (OSError, ValueError):
            pass

    def _handle_error(self, connection, message):
        log.warning(
            "[Client] ERROR from %s: %s (%s)",
            connection.peer_id, message.get("code"), message.get("message")
        )

    # ------------------------------------------------------------------
    # servidor TCP e conexões de saída
    # ------------------------------------------------------------------
    def start_server(self):
        """Inicia o servidor TCP que recebe conexões de outros peers."""

        self.server_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        self.server_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        self.server_socket.bind(
            ("0.0.0.0", self.listen_port)
        )

        self.server_socket.listen()
        self.server_socket.settimeout(1)

        self._spawn(self._accept_connections)

        log.info("[PeerServer] listening on port %s", self.listen_port)

    def _spawn(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def _accept_connections(self):
        """Aceita conexões TCP de outros peers."""

        while self.running:
            try:
                sock, address = self.server_socket.accept()

            except socket.timeout:
                continue

            except OSError:
                break

            sock.settimeout(None)

            connection = PeerConnection(
                sock,
                address,
                on_message=self._on_message,
                on_disconnect=self._on_disconnect,
                inbound=True
            )

            with self.lock:
                self.pending.add(connection)

            connection.start()

            log.debug("[PeerServer] inbound TCP connection from %s", address)

            # Sem HELLO em 10 s, descarta a conexão.
            self._spawn(self._handshake_watchdog, connection)

    def _handshake_watchdog(self, connection, timeout=10):
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if connection.ready or not connection.is_connected():
                return

            time.sleep(0.2)

        if not connection.ready:
            log.warning(
                "[PeerServer] handshake timeout with %s - closing",
                connection.peer_id or connection.address
            )
            connection.close()

    def connect_to_peer(self, peer_id, address):
        """Abre conexão de saída com um peer descoberto e envia HELLO."""

        with self.lock:
            if (
                self.stopping
                or peer_id in self._connecting
                or peer_id in self.connections
            ):
                return False

            self._connecting.add(peer_id)

        self.peer_table.update_status(peer_id, "CONNECTING")

        try:
            connection = PeerConnection.connect(
                address[0], address[1],
                timeout=5,
                peer_id=peer_id,
                on_message=self._on_message,
                on_disconnect=self._on_disconnect
            )

        except OSError as error:
            log.info("[PeerServer] connect to %s failed: %s", peer_id, error)
            self._connection_failed(peer_id)
            return False

        with self.lock:
            self.pending.add(connection)

        connection.start()

        try:
            connection.send_message({
                "type": "HELLO",
                "peer_id": self.peer_id,
                "version": PROTOCOL_VERSION,
                "features": list(FEATURES),
            })

        except (OSError, ValueError):
            connection.close()
            self._connection_failed(peer_id)
            return False

        # Aguarda o HELLO_OK (que marca connection.ready).
        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            if connection.ready or not connection.is_connected():
                break

            time.sleep(0.05)

        with self.lock:
            self._connecting.discard(peer_id)

        if not connection.ready:
            log.info("[PeerServer] no HELLO_OK from %s", peer_id)
            connection.close()
            self._connection_failed(peer_id)
            return False

        return True

    def _connection_failed(self, peer_id):
        with self.lock:
            self._connecting.discard(peer_id)

        if self.stopping:
            return

        peer = self.peer_table.get_peer(peer_id)

        if peer is None or peer["status"] == "GONE":
            return

        if peer["status"] == "CONNECTED":
            return    # outra conexão já está ativa

        self.peer_table.schedule_retry(
            peer_id,
            self.config["base_backoff"],
            self.config["max_backoff"],
            self.config["max_reconnect_attempts"]
        )

    # ------------------------------------------------------------------
    # handshake e desempate de conexões duplicadas
    # ------------------------------------------------------------------
    def _valid_hello(self, message):
        peer_id = message.get("peer_id")
        features = message.get("features", [])

        return (
            isinstance(peer_id, str)
            and PEER_ID_RE.match(peer_id) is not None
            and isinstance(features, list)
            and isinstance(message.get("version"), str)
        )

    def _handle_hello(self, connection, message):
        """Recebido por quem ACEITOU a conexão."""

        if not self._valid_hello(message):
            self._reply_error(
                connection, message, "bad_request", "invalid HELLO"
            )
            connection.close()
            return

        peer_id = message["peer_id"]

        if peer_id == self.peer_id:
            connection.close()
            return

        connection.peer_id = peer_id
        connection.features = list(message.get("features", []))

        try:
            connection.send_message({
                "type": "HELLO_OK",
                "msg_id": message["msg_id"],
                "peer_id": self.peer_id,
                "version": PROTOCOL_VERSION,
                "features": list(FEATURES),
            })
        except (OSError, ValueError):
            connection.close()
            return

        connection.ready = True

        server_log.info(
            "[PeerServer] Inbound connected: %s (features=%s)",
            peer_id, ",".join(connection.features)
        )

        self._register_connection(connection)

    def _handle_hello_ok(self, connection, message):
        """Recebido por quem ABRIU a conexão."""

        if not self._valid_hello(message):
            connection.close()
            return

        peer_id = message["peer_id"]

        if connection.peer_id and connection.peer_id != peer_id:
            log.warning(
                "[PeerServer] expected %s but got %s - closing",
                connection.peer_id, peer_id
            )
            connection.close()
            return

        connection.peer_id = peer_id
        connection.features = list(message.get("features", []))
        connection.ready = True

        server_log.info(
            "[PeerServer] Outbound connected: %s (features=%s)",
            peer_id, ",".join(connection.features)
        )

        self._register_connection(connection)

    def _initiator(self, connection):
        return connection.peer_id if connection.inbound else self.peer_id

    def _register_connection(self, connection):
        """Registra uma conexão pronta, resolvendo duplicidades.

        Regra: fica a conexão aberta pelo peer de MENOR peer_id.
        """
        peer_id = connection.peer_id
        loser = None

        with self.lock:
            self.pending.discard(connection)
            self._connecting.discard(peer_id)

            existing = self.connections.get(peer_id)

            if existing is not None and existing is not connection:
                preferred = min(self.peer_id, peer_id)
                keep_new = (
                    self._initiator(connection) == preferred
                    and self._initiator(existing) != preferred
                )
                keep_old = (
                    self._initiator(existing) == preferred
                    and self._initiator(connection) != preferred
                )

                if keep_old:
                    loser = connection
                elif keep_new:
                    loser = existing
                else:
                    # Mesma origem nas duas: a nova substitui a antiga
                    # (a antiga provavelmente está morta).
                    loser = existing

                if loser is existing:
                    self.connections[peer_id] = connection

                server_log.info(
                    "[PeerServer] Duplicate connection with %s - "
                    "keeping %s (lower peer_id)",
                    peer_id,
                    "outbound" if self._initiator(
                        self.connections[peer_id]
                    ) == self.peer_id else "inbound"
                )
            else:
                self.connections[peer_id] = connection

        winner = self.connections.get(peer_id)

        if winner is connection:
            self.peer_table.add_peer(peer_id, self._known_address(
                peer_id, connection))
            self.peer_table.set_connected(
                peer_id,
                connection,
                direction="inbound" if connection.inbound else "outbound",
                features=connection.features
            )

        if loser is not None:
            loser.replaced = True
            self._spawn(self.close_gracefully, loser, "duplicate")

    def _known_address(self, peer_id, connection):
        peer = self.peer_table.get_peer(peer_id)

        if peer and peer.get("address"):
            return peer["address"]

        return connection.address

    # ------------------------------------------------------------------
    # BYE / desconexão
    # ------------------------------------------------------------------
    def close_gracefully(self, connection, reason="shutdown", wait=2.0):
        """Envia BYE, aguarda BYE_OK por até 2 s e fecha o socket."""

        try:
            if connection.peer_id:
                connection.bye_event.clear()
                connection.send_message({
                    "type": "BYE",
                    "src": self.peer_id,
                    "dst": connection.peer_id,
                    "reason": reason,
                })
                connection.bye_event.wait(wait)

        except (OSError, ValueError):
            pass

        finally:
            connection.close()

    def _handle_bye(self, connection, message):
        reason = message.get("reason", "")

        try:
            connection.send_message({
                "type": "BYE_OK",
                "msg_id": message["msg_id"],
                "src": self.peer_id,
                "dst": connection.peer_id,
            })
        except (OSError, ValueError):
            pass

        log.info("[PeerServer] BYE from %s (%s)", connection.peer_id, reason)

        if reason == "duplicate":
            connection.replaced = True

        elif reason == "shutdown":
            # Saída limpa: não adianta insistir em reconectar.
            connection.closed_by_peer_shutdown = True

        connection.close()

    def _handle_bye_ok(self, connection, message):
        connection.bye_event.set()

    def _on_disconnect(self, connection):
        """Trata o encerramento de uma conexão."""

        with self.lock:
            self.pending.discard(connection)

        peer_id = connection.peer_id

        if not peer_id:
            return

        with self.lock:
            current = self.connections.get(peer_id)

            if current is not connection:
                return     # conexão perdedora/antiga: nada a fazer

            self.connections.pop(peer_id, None)

        self.keep_alive.forget(peer_id)

        if self.stopping:
            return

        if connection.replaced:
            # Perdeu o desempate de duplicadas: não é queda do peer.
            self.peer_table.update_status(peer_id, "DISCOVERED")
            return

        log.info("[PeerServer] Peer disconnected: %s", peer_id)

        if getattr(connection, "closed_by_peer_shutdown", False):
            self.peer_table.mark_gone(peer_id, seen_absent=False)
        else:
            self.peer_table.update_status(peer_id, "STALE")
            self.peer_table.schedule_retry(
                peer_id,
                self.config["base_backoff"],
                self.config["max_backoff"],
                self.config["max_reconnect_attempts"]
            )

        self._notify_peer_lost(peer_id, "disconnected")

    def _on_stale(self, peer_id):
        """KeepAlive: 3 PINGs sem PONG. Fecha a conexão e reage."""

        with self.lock:
            connection = self.connections.pop(peer_id, None)

        if connection is not None:
            connection.close()

        if self.stopping:
            return

        self.peer_table.update_status(peer_id, "STALE")
        self.peer_table.schedule_retry(
            peer_id,
            self.config["base_backoff"],
            self.config["max_backoff"],
            self.config["max_reconnect_attempts"]
        )

        self._notify_peer_lost(peer_id, "STALE")

    def _notify_peer_lost(self, peer_id, reason):
        """Avisa o jogo: jogador caiu (host) ou host caiu (jogador)."""
        self.game_host.player_lost(peer_id, reason)
        self.game_player.on_peer_lost(peer_id)

    # ------------------------------------------------------------------
    # envio
    # ------------------------------------------------------------------
    def send_to_peer(self, peer_id, message):
        """Envia ``message`` ao peer (conexão já existente na malha)."""

        with self.lock:
            connection = self.connections.get(peer_id)

        if connection is None or not connection.ready:
            log.warning("[Client] no connection to %s", peer_id)
            return False

        message.setdefault("src", self.peer_id)
        message.setdefault("dst", peer_id)

        try:
            connection.send_message(message)
            return True

        except (OSError, ValueError) as error:
            log.warning("[Client] send to %s failed: %s", peer_id, error)
            # A thread de leitura detectará a queda e acionará o jogo.
            connection.close()
            return False

    # ------------------------------------------------------------------
    # Rendezvous
    # ------------------------------------------------------------------
    def register(self):
        """Registra este cliente no servidor Rendezvous."""

        response = self.rendezvous.register(
            self.listen_port,
            self.config["register_ttl"]
        )

        if not response or response.get("status") != "OK":
            log.error("[RDV] REGISTER failed: %s", response)
            return False

        log.info(
            "[RDV] public address as seen by the server: %s:%s "
            "(behind NAT, only forwarded ports accept inbound peers)",
            response.get("ip"), response.get("port")
        )

        return response

    def discover_peers(self, namespace=None, all_namespaces=False):
        """DISCOVER: devolve a lista de peers (sem o próprio) ou None."""

        if all_namespaces:
            response = self.rendezvous.discover(namespace=None)
        elif namespace:
            response = self.rendezvous.discover(namespace=namespace)
        else:
            response = self.rendezvous.discover()

        if not response:
            return None

        if response.get("status") != "OK":
            if response.get("message") == "peer_not_registered":
                log.warning("[RDV] not registered - registering again")
                self.register()

            log.warning("[RDV] DISCOVER error: %s", response.get("message"))
            return None

        peers = []

        for entry in response.get("peers", []):
            try:
                name, namespace_ = entry["name"], entry["namespace"]
                entry["peer_id"] = f"{name}@{namespace_}"
                entry["port"] = int(entry["port"])
            except (KeyError, TypeError, ValueError):
                continue

            if entry["peer_id"] == self.peer_id:
                continue    # filtra o próprio peer

            peers.append(entry)

        return peers

    def reconcile(self):
        """DISCOVER no namespace local, atualiza a PeerTable e conecta."""

        peers = self.discover_peers()

        if peers is None:
            return False

        discovered = {
            entry["peer_id"]: (entry["ip"], entry["port"])
            for entry in peers
        }

        new, absent = self.peer_table.reconcile(discovered)

        for peer_id in absent:
            with self.lock:
                connection = self.connections.pop(peer_id, None)

            if connection is not None:
                log.info("[Client] %s left the Rendezvous - BYE", peer_id)
                self._spawn(self.close_gracefully, connection, "idle")
                self._notify_peer_lost(peer_id, "left rendezvous")

        if new:
            log.info("[Client] new peers: %s", ", ".join(new))

        self._connect_due_peers()

        return True

    def _connect_due_peers(self):
        """Tenta conectar a peers sem conexão cujo retry venceu."""

        for peer in self.peer_table.due_for_retry():
            peer_id = peer["peer_id"]

            with self.lock:
                if peer_id in self.connections or peer_id in self._connecting:
                    continue

            self._spawn(self.connect_to_peer, peer_id, peer["address"])

    # ------------------------------------------------------------------
    # tarefas periódicas
    # ------------------------------------------------------------------
    def _discover_loop(self):
        interval = max(5, self.config["discover_interval"])

        while not self._stop_event.wait(interval):
            try:
                self.reconcile()
            except Exception:
                log.exception("[Client] discover loop error")

    def _refresh_loop(self):
        ttl = self.config["register_ttl"]
        interval = max(10, ttl / 2)

        while not self._stop_event.wait(interval):
            try:
                if not self.register():
                    # tenta de novo em breve
                    self._stop_event.wait(15)
                    self.register()
            except Exception:
                log.exception("[Client] refresh loop error")

    def _reconnect_loop(self):
        while not self._stop_event.wait(1):
            try:
                self._connect_due_peers()
            except Exception:
                log.exception("[Client] reconnect loop error")

    def _active_connections(self):
        with self.lock:
            return [c for c in self.connections.values() if c.ready]

    def start(self):
        """Inicializa os serviços principais do cliente."""

        if self.running:
            return True

        self.running = True
        self.stopping = False
        self._stop_event.clear()

        try:
            self.start_server()

            response = self.register()

            if not response:
                self.stop()
                return False

            self.keep_alive.start(self._active_connections)

            self.reconcile()

            self._threads = [
                self._spawn(self._discover_loop),
                self._spawn(self._refresh_loop),
                self._spawn(self._reconnect_loop),
            ]

            log.info("[Client] P2P client started: %s", self.peer_id)

            return True

        except Exception:
            self.stop()
            raise

    # ------------------------------------------------------------------
    # comandos de rede (usados pela CLI)
    # ------------------------------------------------------------------
    def list_peers(self, scope=None):
        """/peers [* | #namespace]. Devolve lista de dicts ou None."""

        if scope == "*":
            return self.discover_peers(all_namespaces=True)

        if scope and scope.startswith("#") and len(scope) > 1:
            return self.discover_peers(namespace=scope[1:])

        return self.discover_peers()

    def list_connections(self):
        rows = []

        for peer in self.peer_table.get_all_peers():
            connection = peer.get("connection")

            rows.append({
                "peer_id": peer["peer_id"],
                "status": peer["status"],
                "direction": peer.get("direction") or "-",
                "address": peer.get("address"),
                "rtt": self.peer_table.get_avg_rtt(peer["peer_id"]),
                "connected": bool(connection),
            })

        return rows

    def force_reconcile(self):
        """/reconnect: reconcilia agora, zerando o backoff dos peers."""

        for peer in self.peer_table.get_all_peers():
            if peer["status"] in ("STALE", "DISCOVERED", "GONE"):
                peer["next_retry"] = 0.0

                if peer["status"] == "GONE":
                    peer["seen_absent"] = True

        return self.reconcile()

    # ---- lobby ---------------------------------------------------------
    def _handle_game_list_ok(self, connection, message):
        msg_id = message.get("msg_id")
        games = message.get("games")

        if not isinstance(games, list):
            games = []

        with self._games_cond:
            if msg_id in self._games_replies:
                self._games_replies[msg_id] = games
                self._games_cond.notify_all()

    def query_games(self, peer_ids, timeout=2.0):
        """Envia GAME_LIST aos peers e consolida as respostas."""

        sent = {}

        for peer_id in peer_ids:
            connection = self.peer_table.get_connection(peer_id)

            if connection is None or "hangman" not in connection.features:
                continue

            msg_id = str(uuid.uuid4())

            with self._games_cond:
                self._games_replies[msg_id] = None

            if self.send_to_peer(peer_id, {
                "type": "GAME_LIST",
                "msg_id": msg_id,
            }):
                sent[msg_id] = peer_id
            else:
                with self._games_cond:
                    self._games_replies.pop(msg_id, None)

        deadline = time.monotonic() + timeout

        with self._games_cond:
            while time.monotonic() < deadline:
                if all(
                    self._games_replies.get(m) is not None for m in sent
                ):
                    break

                self._games_cond.wait(0.1)

            results = []

            for msg_id, peer_id in sent.items():
                games = self._games_replies.pop(msg_id, None) or []

                for game in games:
                    if isinstance(game, dict):
                        results.append(game)

        return results

    def list_games(self):
        """/games: GAME_LIST a todos os vizinhos conectados."""

        peer_ids = [p["peer_id"] for p in self.peer_table.get_connected_peers()]
        games = self.query_games(peer_ids)

        own = self.game_host.get_game_info()

        if own:
            games.insert(0, own)

        return games

    # ---- partida como host ----------------------------------------------
    def create_game(self, word=None, max_players=None):
        """/create. Devolve (ok, texto)."""

        if self.game_player.is_in_game():
            return False, "Você já está em uma partida como jogador."

        random_pick = word is None

        if random_pick:
            word = random_word(self.words_path())

        try:
            game_id = self.game_host.create_game(word, max_players)

        except ValueError as error:
            return False, str(error)

        if game_id is None:
            return False, "Você já hospeda uma partida (use /cancel)."

        text = f"Partida {game_id} criada"

        if random_pick:
            text += " com palavra sorteada"

        return True, text + ". Aguardando jogadores (use /start)."

    def start_game(self):
        return self.game_host.start_game()

    def cancel_game(self):
        if self.game_host.cancel_game():
            return True, "Partida cancelada."

        return False, "Você não hospeda nenhuma partida ativa."

    # ---- partida como jogador -------------------------------------------
    def normalize_peer_id(self, text):
        return text if "@" in text else f"{text}@{self.namespace}"

    def join_game(self, host_text):
        """/join <host_peer_id>. Devolve (ok, texto)."""

        host_id = self.normalize_peer_id(host_text)

        if self.game_host.is_active():
            return False, "Você está hospedando uma partida."

        if self.game_player.is_in_game():
            return False, "Você já está em uma partida (use /leave)."

        if host_id == self.peer_id:
            return False, "O host não entra na própria partida."

        if self.peer_table.get_connection(host_id) is None:
            return False, f"Sem conexão com {host_id} (veja /conn)."

        games = self.query_games([host_id])
        games = [g for g in games if g.get("host") == host_id]

        if not games:
            return False, f"{host_id} não hospeda nenhuma partida."

        game = games[0]

        if not self.game_player.request_join(host_id, game["game_id"]):
            return False, "Não foi possível enviar o JOIN."

        result = self.game_player.wait_join(timeout=5)

        if result is None:
            return False, "Sem resposta do host ao JOIN."

        if result[0] == "ERR":
            return False, f"JOIN recusado ({result[1]})."

        return True, "Entrou na partida" + (
            " (reentrada)." if result[1] else "."
        )

    def leave_game(self):
        if not self.game_player.is_in_game():
            return False, "Você não está em nenhuma partida."

        if self.game_player.status == "JOINING":
            self.game_player.leave_game()
            return True, "Entrada cancelada."

        self.game_player.leave()
        self.game_player.wait_leave(timeout=2)

        return True, "Você saiu da partida."

    def status_lines(self):
        lines = []

        if self.game_host.status != "IDLE":
            self.game_host.refresh()

        if self.game_host.status != "IDLE":
            lines.extend(self.game_host.status_lines())

        if self.game_player.is_in_game():
            lines.extend(self.game_player.status_lines())

        if not lines:
            lines.append("[Game] Nenhuma partida em andamento.")

        return lines

    # ------------------------------------------------------------------
    # encerramento
    # ------------------------------------------------------------------
    def stop(self):
        """Encerra: GAME_OVER/LEAVE, BYE a todos e UNREGISTER (nessa ordem)."""

        if not self.running:
            return

        self.stopping = True

        # 1. Papel na partida
        try:
            if self.game_host.is_active():
                self.game_host.shutdown("host_shutdown")

            if self.game_player.status in ("JOINED", "PLAYING"):
                self.game_player.leave()
                self.game_player.wait_leave(timeout=1)
        except Exception:
            log.exception("[Client] error while leaving the game")

        # 2. BYE a todos os vizinhos (em paralelo, até 2 s)
        with self.lock:
            connections = list(self.connections.values())
            self.connections.clear()

        workers = [
            self._spawn(self.close_gracefully, c, "shutdown")
            for c in connections
        ]

        for worker in workers:
            worker.join(timeout=3)

        # 3. Encerra tarefas e sockets
        self.running = False
        self._stop_event.set()
        self.keep_alive.stop()

        with self.lock:
            pending = list(self.pending)

        for connection in pending:
            connection.close()

        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass

        # 4. UNREGISTER
        self.rendezvous.unregister(self.listen_port)

        log.info("[Client] P2P client stopped")