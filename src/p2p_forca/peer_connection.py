import socket
import threading
import json
import uuid
import logging
from message_router import route_message

logger = logging.getLogger("PeerConnection")


class PeerConnection:
    """
    Representa uma conexão TCP ativa com um único peer vizinho.
    Cuida do envio e recebimento de linhas JSON terminadas por '\\n'.
    """
    def __init__(self, sock: socket.socket, addr, local_peer_id: str, is_outbound: bool, on_message=route_message, on_disconnect=None):
        self.sock = sock
        self.addr = addr
        self.local_peer_id = local_peer_id
        self.remote_peer_id = None  # Preenchido após receber HELLO ou HELLO_OK
        self.is_outbound = is_outbound  # True se nós iniciamos a conexão; False se recebemos
        self.handshake_done = False
        self.features = []
        self.running = True
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.lock = threading.Lock()

    def start_listening(self):
        """Inicia uma thread dedicada para ler mensagens que chegam neste socket."""
        thread = threading.Thread(target=self._read_loop, daemon=True)
        thread.start()

    def send_json(self, payload: dict):
        """Converte um dicionário para JSON, adiciona '\\n' e envia pelo socket."""
        if not self.running:
            return
        try:
            raw_line = json.dumps(payload) + "\n"
            with self.lock:
                self.sock.sendall(raw_line.encode("utf-8"))
            logger.debug(f"[{self.remote_peer_id or self.addr}] -> {raw_line.strip()}")
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem para {self.remote_peer_id or self.addr}: {e}")
            self.close()

    def send_hello(self):
        """Envia o pacote inicial HELLO (usado por quem abre a conexão Outbound)."""
        hello_msg = {
            "type": "HELLO",
            "msg_id": str(uuid.uuid4()),
            "peer_id": self.local_peer_id,
            "version": "1.0",
            "features": ["hangman"],
            "ttl": 1
        }
        self.send_json(hello_msg)

    def send_hello_ok(self, req_msg_id: str):
        """Responde ao HELLO recebido confirmando a sessão."""
        hello_ok_msg = {
            "type": "HELLO_OK",
            "msg_id": req_msg_id,
            "peer_id": self.local_peer_id,
            "version": "1.0",
            "features": ["hangman"],
            "ttl": 1
        }
        self.send_json(hello_ok_msg)

    def send_bye(self, reason: str = "shutdown"):
        """Envia aviso de encerramento limpo antes de fechar o socket."""
        if self.remote_peer_id:
            bye_msg = {
                "type": "BYE",
                "msg_id": str(uuid.uuid4()),
                "src": self.local_peer_id,
                "dst": self.remote_peer_id,
                "reason": reason,
                "ttl": 1
            }
            self.send_json(bye_msg)
        self.close()

    def _read_loop(self):
        """Lê bytes do socket TCP e separa as mensagens pela quebra de linha '\\n'."""
        buffer = ""
        try:
            while self.running:
                data = self.sock.recv(4096)
                if not data:
                    # Socket fechado pelo outro lado
                    break
                buffer += data.decode("utf-8", errors="replace")

                # Limite de segurança de 32 KiB por linha (conforme especificação)
                if len(buffer) > 32768 and "\n" not in buffer:
                    logger.warning(f"Linha excedeu 32 KiB de {self.addr}. Fechando conexão.")
                    break

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        logger.debug(f"[{self.remote_peer_id or self.addr}] <- {line.strip()}")
                        self.on_message(line, self)
        except Exception as e:
            if self.running:
                logger.debug(f"Conexão encerrada com {self.remote_peer_id or self.addr}: {e}")
        finally:
            self.close()

    def close(self):
        """Fecha o socket com segurança e notifica o gerenciador."""
        if not self.running:
            return
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass
        if self.on_disconnect:
            self.on_disconnect(self)


class PeerServer:
    """
    Servidor TCP local que fica ouvindo na 'listen_port' para aceitar
    conexões de entrada (Inbound) de outros peers da malha.
    """
    def __init__(self, host: str, port: int, local_peer_id: str, on_new_connection):
        self.host = host
        self.port = port
        self.local_peer_id = local_peer_id
        self.on_new_connection = on_new_connection
        self.server_sock = None
        self.running = False

    def start(self):
        """Inicia o servidor TCP em uma thread separada."""
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(16)
        self.running = True
        logger.info(f"[PeerServer] Escutando conexões P2P em {self.host}:{self.port}")

        thread = threading.Thread(target=self._accept_loop, daemon=True)
        thread.start()

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.server_sock.accept()
                conn = PeerConnection(
                    sock=client_sock,
                    addr=addr,
                    local_peer_id=self.local_peer_id,
                    is_outbound=False
                )
                self.on_new_connection(conn)
                conn.start_listening()
            except Exception as e:
                if self.running:
                    logger.error(f"[PeerServer] Erro ao aceitar conexão: {e}")

    def stop(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass


def connect_to_peer(ip: str, port: int, local_peer_id: str, on_disconnect=None) -> PeerConnection | None:
    """
    Abre uma conexão TCP de saída (Outbound) para um peer descoberto
    e envia automaticamente a mensagem inicial HELLO.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((ip, port))
        sock.settimeout(None)  # Remove timeout após conectar para leitura contínua

        conn = PeerConnection(
            sock=sock,
            addr=(ip, port),
            local_peer_id=local_peer_id,
            is_outbound=True,
            on_disconnect=on_disconnect
        )
        conn.start_listening()
        conn.send_hello()
        return conn
    except Exception as e:
        logger.warning(f"Falha ao conectar ao peer {ip}:{port} -> {e}")
        return None