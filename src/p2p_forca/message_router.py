import json
import logging

# Configura o sistema de logs para ajudar a debugar o que está chegando
logger = logging.getLogger("MessageRouter")


def route_message(message_str: str, connection, peer_table=None, keep_alive=None, game_manager=None):
    "Função principal que recebe a linha de texto JSON recebida via TCP, analisa o seu conteúdo e decide para onde encaminhar."
    if not message_str or not message_str.strip():
        logger.warning("Recebida uma linha vazia.")
        return

    try:
        # Passo 1: Converte a string JSON em um dicionário Python
        msg = json.loads(message_str.strip())
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar JSON: {e} | Conteúdo: {message_str}")
        return

    # Passo 2: Valida se a mensagem possui os campos obrigatórios básicos (como 'type' e 'ttl')
    msg_type = msg.get("type")
    msg_ttl = msg.get("ttl")

    if not msg_type:
        logger.error("Mensagem recebida sem o campo obrigatório 'type'.")
        return

    # A especificação exige que o TTL das mensagens P2P seja estritamente 1
    if msg_ttl != 1:
        logger.warning(f"Mensagem com TTL inválido ({msg_ttl}), descartada.")
        return

    logger.debug(f"Roteando mensagem do tipo: {msg_type} com id: {msg.get('msg_id')}")

    # Passo 3: Despacha a mensagem dependendo do tipo dela
    if msg_type in ["HELLO", "HELLO_OK"]:
        # Mensagens de handshake da sessão P2P
        handle_session_message(msg, connection, peer_table)

    elif msg_type in ["PING", "PONG"]:
        # Mensagens de keep-alive para manter a conexão ativa e medir RTT
        handle_keep_alive_message(msg, connection, keep_alive)

    elif msg_type in ["GAME_LIST", "GAME_LIST_OK", "JOIN", "JOIN_OK", "JOIN_ERR", "LEAVE", "LEAVE_OK"]:
        # Mensagens de lobby / gerenciamento de partidas
        handle_lobby_message(msg, connection, game_manager)

    elif msg_type in ["GAME_START", "STATE", "GUESS", "GUESS_RESULT", "GAME_OVER"]:
        # Mensagens exclusivas de andamento da partida (jogo da forca)
        handle_game_message(msg, connection, game_manager)

    else:
        logger.warning(f"Tipo de mensagem desconhecido: {msg_type}")


def handle_session_message(msg: dict, connection, peer_table=None):
    "Trata o handshake HELLO e HELLO_OK e registra o peer na PeerTable."
    msg_type = msg.get("type")
    remote_peer_id = msg.get("peer_id")

    if not remote_peer_id:
        logger.warning(f"Mensagem {msg_type} recebida sem 'peer_id'.")
        return

    connection.remote_peer_id = remote_peer_id
    connection.features = msg.get("features", [])
    connection.handshake_done = True

    if msg_type == "HELLO":
        logger.info(f"[Router] HELLO recebido de {remote_peer_id}. Respondendo com HELLO_OK.")
        connection.send_hello_ok(msg.get("msg_id"))
        if peer_table:
            peer_table.register_connection(connection)

    elif msg_type == "HELLO_OK":
        logger.info(f"[Router] HELLO_OK recebido de {remote_peer_id}. Sessão confirmada.")
        if peer_table:
            peer_table.register_connection(connection)


def handle_keep_alive_message(msg: dict, connection, keep_alive=None):
    "Encaminha PING e PONG para o KeepAliveManager."
    if not keep_alive:
        logger.debug("[Router] KeepAliveManager não configurado para tratar PING/PONG.")
        return

    msg_type = msg.get("type")
    if msg_type == "PING":
        keep_alive.handle_ping_received(msg, connection)
    elif msg_type == "PONG":
        keep_alive.handle_pong_received(msg, connection)


def handle_lobby_message(msg: dict, connection, game_manager=None):
    "Encaminha mensagens de descoberta de partidas e entrada em salas para o game_manager."
    if not game_manager:
        logger.debug(f"[Router] Mensagem de lobby {msg.get('type')} recebida, mas game_manager não configurado.")
        return

    if hasattr(game_manager, "handle_lobby_message"):
        game_manager.handle_lobby_message(msg, connection)
    elif hasattr(game_manager, "handle_message"):
        game_manager.handle_message(msg, connection)


def handle_game_message(msg: dict, connection, game_manager=None):
    "Encaminha mensagens do fluxo do Jogo da Forca para o game_manager."
    if not game_manager:
        logger.debug(f"[Router] Mensagem de jogo {msg.get('type')} recebida, mas game_manager não configurado.")
        return

    if hasattr(game_manager, "handle_game_message"):
        game_manager.handle_game_message(msg, connection)
    elif hasattr(game_manager, "handle_message"):
        game_manager.handle_message(msg, connection)