import hashlib
import logging
import time

log = logging.getLogger("Player")

class GamePlayer:
    """Mantém a última visão válida recebida do host."""

    def __init__(self, peer_id):
        self.peer_id = peer_id
        self.leave_game()

    def join_game(self, host_id, game_id, players, rejoin):
        self.host_id = host_id
        self.game_id = game_id
        self.players = players
        self.status = "PLAYING" if rejoin else "JOINED"

    def leave_game(self):
        self.host_id = None
        self.game_id = None
        self.status = "IDLE"
        self.players = []
        self.masked = ""
        self.word_length = None
        self.wrong_letters = []
        self.lives = None
        self.initial_lives = None
        self.turn = None
        self.turn_timeout = None
        self.deadline = None
        self.seq = 0
        self.word_hash = None
        self.last_state = None

    def start_game(self, message):
        self.players = message["players"]
        self.initial_lives = self.lives = message["lives"]
        self.word_length = message["word_length"]
        self.masked = "_" * self.word_length
        self.word_hash = message["word_hash"]
        self.turn_timeout = message["turn_timeout"]
        self.status = "PLAYING"

    def apply_state(self, message):
        new_seq = message["seq"]
        if new_seq <= self.seq:
            log.debug("STATE seq=%d ignored (already at seq=%d)", new_seq, self.seq)
            return False

        last_guess = message["last_guess"]
        new_turn = (self.deadline is None or message["turn"] != self.turn
                    or message["players"] == self.players
                    or last_guess != (self.last_state or {}).get("last_guess"))
        if self.turn_timeout is not None and new_turn:
            self.deadline = time.monotonic() + self.turn_timeout
        self.seq = new_seq
        self.masked = message["masked"]
        self.wrong_letters = message["wrong_letters"]
        self.lives = message["lives"]
        self.turn = message["turn"]
        self.players = message["players"]
        self.last_state = message
        log.info("STATE seq=%d applied: %s lives=%d turn=%s%s", self.seq, self.masked,
                 self.lives, self.turn, " (você)" if self.turn == self.peer_id else "")
        return True

    def verify_game_over(self, message):
        if self.word_hash is None:
            return None
        actual = hashlib.sha256(message["word"].encode("utf-8")).hexdigest()
        if actual != self.word_hash:
            log.warning("host cheated: hash mismatch (host=%s, game=%s)", self.host_id, self.game_id)
            return False
        return True