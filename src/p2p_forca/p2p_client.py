import json
import logging
import socket
import threading
from pathlib import Path

from p2p_forca.peer_connection import PeerConnection
from p2p_forca.peer_table import PeerTable
from p2p_forca.rendezvous_connection import RendezvousConnection
from p2p_forca.keep_alive import KeepAlive
from p2p_forca.message_router import MessageRouter


class P2PClient:
    """Coordena a comunicação e o funcionamento do cliente P2P."""

    def __init__(self, config_path="config.json"):
        self.config_path = Path(config_path)

        self.config = self._load_config()

        self.name = self.config["name"]
        self.namespace = self.config["namespace"]
        self.peer_id = f"{self.name}@{self.namespace}"
        self.listen_port = self.config["listen_port"]

        self.peer_table = PeerTable()
        self.router = MessageRouter()

        self.rendezvous = RendezvousConnection(
            self.config["rdv_host"],
            self.config["rdv_port"],
            self.namespace,
            self.peer_id
        )

        self.keep_alive = KeepAlive(
            self.peer_table,
            self.config["ping_interval"],
            self.config["ping_miss_limit"]
        )

        self.server_socket = None
        self.connections = {}
        self.running = False

        self.lock = threading.Lock()

    def _load_config(self):
        """Carrega as configurações do arquivo JSON."""

        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                config = json.load(file)

        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Não foi possível carregar {self.config_path}: {error}"
            ) from error

        required = [
            "rdv_host",
            "rdv_port",
            "namespace",
            "name",
            "listen_port"
        ]

        for key in required:
            if key not in config:
                raise ValueError(
                    f"Configuração obrigatória ausente: {key}"
                )

        return config

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

        thread = threading.Thread(
            target=self._accept_connections,
            daemon=True
        )

        thread.start()

        logging.info(
            "Servidor iniciado na porta %s",
            self.listen_port
        )

    def _accept_connections(self):
        """Aceita conexões TCP de outros peers."""

        while self.running:
            try:
                sock, address = self.server_socket.accept()

                connection = PeerConnection(
                    sock,
                    address,
                    on_message=self._on_message,
                    on_disconnect=self._on_disconnect
                )

                connection.start()

                logging.info(
                    "Nova conexão recebida de %s",
                    address
                )

            except socket.timeout:
                continue

            except OSError:
                break

    def _on_message(self, connection, message):
        """Encaminha mensagens recebidas para o roteador."""

        self.router.route(connection, message)

    def _on_disconnect(self, connection):
        """Trata o encerramento de uma conexão."""

        peer_id = connection.peer_id

        if peer_id:
            with self.lock:
                self.connections.pop(peer_id, None)

            self.peer_table.remove_peer(peer_id)

            logging.info(
                "Peer desconectado: %s",
                peer_id
            )

    def register(self):
        """Registra este cliente no servidor Rendezvous."""

        response = self.rendezvous.register(
            self.listen_port,
            self.config.get("register_ttl", 7200)
        )

        if response is None:
            logging.error("Falha ao registrar no Rendezvous.")
            return False

        logging.info("Registro no Rendezvous realizado.")

        return response

    def discover_peers(self):
        """Solicita ao Rendezvous a lista de peers disponíveis."""

        response = self.rendezvous.discover()

        if response is None:
            logging.error("Falha ao descobrir peers.")
            return None

        return response

    def start(self):
        """Inicializa os serviços principais do cliente."""

        if self.running:
            return

        self.running = True

        try:
            self.start_server()

            response = self.register()

            if not response:
                self.stop()
                return False

            self.keep_alive.start(
                self.peer_table.get_connected_peers
            )

            logging.info(
                "Cliente P2P iniciado: %s",
                self.peer_id
            )

            return True

        except Exception:
            self.stop()
            raise

    def stop(self):
        """Encerra os serviços e remove o registro do Rendezvous."""

        if not self.running:
            return

        self.running = False

        self.keep_alive.stop()

        with self.lock:
            connections = list(self.connections.values())
            self.connections.clear()

        for connection in connections:
            connection.close()

        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass

        self.rendezvous.unregister(self.listen_port)

        logging.info("Cliente P2P encerrado.")
