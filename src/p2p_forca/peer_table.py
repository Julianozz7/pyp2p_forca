import logging
import time

logger = logging.getLogger("PeerTable")

class PeerEntry:
    "Representa um peer conhecido na rede e seu estado atual."
    def __init__(self, name: str, namespace: str, ip: str, port: int):
        self.name = name
        self.namespace = namespace
        self.peer_id = f"{name}@{namespace}"
        self.ip = ip
        self.port = port
        self.state = "DISCOVERED"  # DISCOVERED, CONNECTING, CONNECTED, STALE, GONE
        self.connection = None     # Objeto PeerConnection associado, se conectado
        self.last_seen = time.time()
        self.reconnect_attempts = 0
        self.backoff = 2

class PeerTable:
    "Gerencia a tabela de todos os peers conhecidos e conexões ativas."
    def __init__(self, local_peer_id: str):
        self.local_peer_id = local_peer_id
        self.peers = {}  # Mapeia peer_id -> PeerEntry
        self.active_connections = {}  # Mapeia socket/conexao -> PeerEntry

    def add_or_update_from_discover(self, rdv_peer_info: dict):
        "Atualiza a tabela com a lista de peers recebida do Rendezvous (DISCOVER)."
        name = rdv_peer_info.get("name")
        namespace = rdv_peer_info.get("namespace")
        ip = rdv_peer_info.get("ip")
        port = rdv_peer_info.get("port")

        peer_id = f"{name}@{namespace}"

        # Ignora a si mesmo
        if peer_id == self.local_peer_id:
            return None

        if peer_id in self.peers:
            entry = self.peers[peer_id]
            entry.ip = ip
            entry.port = port
            if entry.state == "GONE":
                entry.state = "DISCOVERED"
        else:
            entry = PeerEntry(name, namespace, ip, port)
            self.peers[peer_id] = entry
            logger.info(f"[PeerTable] Novo peer descoberto: {peer_id} em {ip}:{port}")

        return entry

    def register_connection(self, conn) -> bool:
        "Registra uma conexão estabelecida e aplica a regra de desempate para conexões duplicadas (menor peer_id lexicográfico vence)."
        remote_id = conn.remote_peer_id
        if not remote_id:
            return True

        # Regra de desempate de conexões duplicadas
        if remote_id in self.peers:
            entry = self.peers[remote_id]
            if entry.connection and entry.connection != conn:
                # Já temos uma conexão aberta com este peer. Vamos desempatar!
                logger.warning(f"[PeerTable] Conexão duplicada detectada com {remote_id}")
                if self.local_peer_id < remote_id:
                    # Nós vencemos, fechamos a conexão que veio deles (ou a duplicada)
                    if not conn.is_outbound:
                        logger.info(f"[PeerTable] Fechando conexão duplicada de entrada com {remote_id}")
                        conn.send_bye("duplicate")
                        return False
                else:
                    # Eles vencem, fechamos a nossa conexão antiga de saída
                    if entry.connection.is_outbound and conn.is_outbound:
                        pass
                    elif entry.connection.is_outbound:
                        logger.info(f"[PeerTable] Substituindo conexão antiga de saída pela de entrada com {remote_id}")
                        entry.connection.close()

        # Associa a conexão válida
        if remote_id in self.peers:
            entry = self.peers[remote_id]
            entry.connection = conn
            entry.state = "CONNECTED"
            entry.reconnect_attempts = 0
        else:
            # Caso o peer não estivesse na tabela (ex: conexão inbound inesperada)
            name, namespace = remote_id.split("@", 1) if "@" in remote_id else (remote_id, "default")
            entry = PeerEntry(name, namespace, conn.addr[0], conn.addr[1])
            entry.connection = conn
            entry.state = "CONNECTED"
            self.peers[remote_id] = entry

        return True

    def remove_connection(self, conn):
        "Remove a associação de conexão quando um socket é fechado."
        remote_id = conn.remote_peer_id
        if remote_id and remote_id in self.peers:
            entry = self.peers[remote_id]
            if entry.connection == conn:
                entry.connection = None
                entry.state = "STALE"
                logger.info(f"[PeerTable] Conexão perdida com {remote_id}. Marcado como STALE.")

    def get_connected_peers(self):
        "Retorna uma lista de todas as conexões ativas."
        return [entry.connection for entry in self.peers.values() if entry.state == "CONNECTED" and entry.connection]