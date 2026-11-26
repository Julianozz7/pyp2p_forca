import re
import uuid
from datetime import datetime, timedelta

from p2p_forca.hangman import Hangman

CONNECTION_TYPES = {"HELLO", "HELLO_OK", "PING", "PONG"}
SESSION_TYPES = CONNECTION_TYPES | {"BYE", "BYE_OK", "ERROR"}
JOIN_REASONS = {"unknown_game", "game_full", "game_running", "game_finished", "already_joined"}
GUESS_REASONS = {"not_your_turn", "letter_already_used", "invalid_letter", "invalid_word", "game_not_running"}

def valid_peer_id(value):
    if not isinstance(value, str) or value.count("@") != 1:
        return False
    return all(1 <= len(part) <= 64 and part.isprintable() for part in value.split("@"))


def _uuid4(value):
    if not isinstance(value, str) or len(value) not in (32, 36):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and value.lower() in (str(parsed), parsed.hex)


def _utc_timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return parsed.utcoffset() == timedelta(0)


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)

def _players(value):
    return _strings(value) and all(valid_peer_id(item) for item in value) and len(set(value)) == len(value)


def _integer(value, minimum=1):
    return type(value) is int and value >= minimum

def _choice(value, options):
    return isinstance(value, str) and value in options

def _game_id(value):
    if not isinstance(value, str):
        return False
    host, sep, suffix = value.rpartition("/")
    return bool(sep and valid_peer_id(host) and re.fullmatch("[0-9a-fA-F]{8}", suffix))

def _lobby_game(game, peer_id):
    return (
        isinstance(game, dict) and _game_id(game.get("game_id"))
        and game["game_id"].rpartition("/")[0] == peer_id == game.get("host")
        and _choice(game.get("status"), {"OPEN", "RUNNING", "FINISHED"})
        and _players(game.get("players")) and _integer(game.get("max_players"))
        and _integer(game.get("lives"), 0) and _word_length(game.get("word_length"))
    )

def _word_length(value):
    return _integer(value, 3) and value <= 20

def _last_guess(value):
    if value is None:
        return True
    if not isinstance(value, dict) or not valid_peer_id(value.get("player")):
        return False
    if value.get("timeout") is True:
        return True
    kind = "letter" if "letter" in value else "word"
    return (("letter" in value) != ("word" in value)
            and isinstance(value.get(kind), str) and type(value.get("correct")) is bool)

def _fields_valid(kind, message):
    get = message.get
    if kind in ("HELLO", "HELLO_OK"):
        return valid_peer_id(get("peer_id")) and isinstance(get("version"), str) and _strings(get("features"))
    if kind in ("PING", "PONG"):
        return _utc_timestamp(get("timestamp"))
    if kind == "BYE":
        return isinstance(get("reason"), str)
    if kind == "ERROR":
        return (isinstance(get("code"), str) and isinstance(get("message"), str)
                and ("ref_msg_id" not in message or isinstance(get("ref_msg_id"), str)))
    if kind in ("GAME_LIST", "BYE_OK"):
        return True
    if kind == "GAME_LIST_OK":
        games = get("games")
        return (isinstance(games, list) and len(games) <= 1
                and all(_lobby_game(game, get("src")) for game in games))

    if not _game_id(get("game_id")):
        return False
    if kind == "JOIN_OK":
        return _players(get("players")) and type(get("rejoin")) is bool
    if kind == "JOIN_ERR":
        return _choice(get("reason"), JOIN_REASONS)
    if kind == "GAME_START":
        return (_players(get("players")) and _integer(get("lives"))
                and _word_length(get("word_length")) and _integer(get("turn_timeout"))
                and isinstance(get("word_hash"), str)
                and re.fullmatch("[0-9a-f]{64}", get("word_hash")) is not None)
    if kind == "STATE":
        masked = get("masked")
        players = get("players")
        return (_integer(get("seq")) and isinstance(masked, str)
                and re.fullmatch("[a-z_]{3,20}", masked) is not None
                and _strings(get("wrong_letters")) and _integer(get("lives"), 0)
                and _players(players) and get("turn") in players
                and "last_guess" in message and _last_guess(get("last_guess")))
    if kind == "GUESS":
        return (("letter" in message) != ("word" in message)
                and isinstance(get("letter") if "letter" in message else get("word"), str))
    if kind == "GUESS_RESULT":
        accepted = get("accepted")
        return (type(accepted) is bool and (type(get("correct")) is bool if accepted
                                          else _choice(get("reason"), GUESS_REASONS)))
    if kind == "GAME_OVER":
        result = get("result")
        scores = get("scores")
        return (
            _choice(result, {"WON", "LOST", "ABORTED"}) and Hangman.valid_word(get("word"))
            and isinstance(scores, dict)
            and all(valid_peer_id(peer_id) and _integer(score, 0) for peer_id, score in scores.items())
            and "winner" in message
            and (valid_peer_id(get("winner")) if result == "WON" else get("winner") is None)
            and (result != "ABORTED" or _choice(get("reason"), {"host_shutdown", "no_players"}))
        )
    return True


class MessageRouter:
    """Valida o envelope e os campos antes de despachar."""

    def __init__(self, peer_id, send_error):
        self.peer_id = peer_id
        self.send_error = send_error
        self.handlers = {}

    def register_handler(self, message_type, handler):
        if not callable(handler):
            raise TypeError("O handler precisa ser uma função.")
        self.handlers[message_type] = handler

    def unregister_handler(self, message_type):
        return self.handlers.pop(message_type, None)

    def route(self, connection, message):
        if not isinstance(message, dict):
            return False
        kind = message.get("type")
        ttl = message.get("ttl")
        # TTL de outro valor é ignorado sem resposta.
        if type(ttl) is int and ttl != 1:
            return False

        error = self._validate(connection, message, kind, ttl)
        if error:
            if kind != "ERROR":
                code, detail = error
                self.send_error(connection, code, detail, message)
            return False

        self.handlers[kind](connection, message)
        return True

    def _validate(self, connection, message, kind, ttl):
        if not isinstance(kind, str) or type(ttl) is not int or not _uuid4(message.get("msg_id")):
            return "bad_request", "Envelope inválido."
        if "timestamp" in message and not _utc_timestamp(message["timestamp"]):
            return "bad_request", "timestamp inválido."
        if kind not in CONNECTION_TYPES:
            src, dst = message.get("src"), message.get("dst")
            if not isinstance(src, str) or not isinstance(dst, str):
                return "bad_request", "src e dst são obrigatórios."
            if dst != self.peer_id:
                return "not_for_me", "O destinatário difere deste peer."
            if connection.peer_id is not None and src != connection.peer_id:
                return "bad_request", "src difere da identidade da sessão."
        if kind not in self.handlers:
            return "unknown_type", f"Tipo desconhecido: {kind[:100]}."
        if not connection.established and kind not in ("HELLO", "HELLO_OK", "ERROR"):
            return "invalid_state", "A sessão ainda não completou o HELLO."
        if connection.established and kind in ("HELLO", "HELLO_OK"):
            return "invalid_state", "A sessão já completou o HELLO."
        if kind not in SESSION_TYPES and "hangman" not in connection.features:
            return "invalid_state", "A sessão não anunciou a capacidade hangman."
        if not _fields_valid(kind, message):
            return "bad_request", f"Campos inválidos para {kind}."
        return None