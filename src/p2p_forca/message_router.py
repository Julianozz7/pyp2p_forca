import json
import logging

# Configura o sistema de logs para ajudar a debugar o que está chegando
logger = logging.getLogger("MessageRouter")

def route_message(message_str: str, connection, peer_table=None, game_manager=None):
    """
    Função principal que recebe a linha de texto JSON recebida via TCP,
    analisa o seu conteúdo e decide para onde encaminhar.
    """
    if not message_str or not message_str.strip():
        logger.warning("Recebida uma linha vazia.")
        return

    try:
        # Passo 1: Converte a string JSON em um dicionário Python
        msg = json.loads(message_str.strip())
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar JSON: {e} | Conteúdo: {message_str}")
        # Se o JSON for inválido, a especificação pede para retornar um erro ou ignorar
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
        handle_session_message(msg, connection)

    elif msg_type in ["PING", "PONG"]:
        # Mensagens de keep-alive para manter a conexão ativa e medir RTT
        handle_keep_alive_message(msg, connection)

    elif msg_type in ["GAME_LIST", "GAME_LIST_OK", "JOIN", "JOIN_OK", "JOIN_ERR", "LEAVE", "LEAVE_OK"]:
        # Mensagens de lobby / gerenciamento de partidas
        handle_lobby_message(msg, connection)

    elif msg_type in ["GAME_START", "STATE", "GUESS", "GUESS_RESULT", "GAME_OVER"]:
        # Mensagens exclusivas de andamento da partida (jogo da forca)
        handle_game_message(msg, connection)

    else:
        logger.warning(f"Tipo de mensagem desconhecido: {msg_type}")
        # Opcional: Enviar uma mensagem de erro do tipo 'ERROR' de volta pela conexão


def handle_session_message(msg, connection):
    """Trata o handshake HELLO e HELLO_OK."""
    msg_type = msg.get("type")
    if msg_type == "HELLO":
        print(f"[Router] HELLO recebido do peer: {msg.get('peer_id')}")
        # Aqui depois chamaremos a lógica para validar a versão, feature 'hangman' e responder com HELLO_OK
    elif msg_type == "HELLO_OK":
        print(f"[Router] HELLO_OK recebido de: {msg.get('peer_id')}")


def handle_keep_alive_message(msg, connection):
    """Trata PING e PONG."""
    msg_type = msg.get("type")
    if msg_type == "PING":
        # Quando recebe um PING, deve responder imediatamente com um PONG repetindo o timestamp
        pass
    elif msg_type == "PONG":
        # Atualiza a tabela de peers com o tempo de resposta (RTT)
        pass


def handle_lobby_message(msg, connection):
    """Trata mensagens de descoberta de partidas e entrada em salas."""
    pass


def handle_game_message(msg, connection):
    """Trata mensagens do fluxo do Jogo da Forca (estado, palpites, fim de jogo)."""
    pass