import hashlib
import logging
import time
import uuid

from p2p_forca.hangman import Hangman

log = logging.getLogger("Host")

class GameHost:
    """Estado autoritativo. O cliente serializa as operações e publica as mudanças."""

    def __init__(self, peer_id, lives=6, max_players=4, turn_timeout=30):
        self.peer_id = peer_id
        self.lives = lives
        self.default_capacity = max_players
        self.turn_timeout = turn_timeout
        self.reset()

    def reset(self):
        self.status = "IDLE"
        self.game = None
        self.game_id = None
        self.players = []
        self.disconnected = set()
        self.scores = {}
        self.max_players = self.default_capacity
        self.turn = 0
        self.deadline = None
        self.finished_at = None
        self.last_guess = None
        self.seq = 0
        self.word_hash = None

    def create_game(self, word, max_players=None):
        if self.status != "IDLE":
            return False
        self.game = Hangman(word, self.lives)
        self.game_id = f"{self.peer_id}/{uuid.uuid4().hex[:8]}"
        self.word_hash = hashlib.sha256(self.game.word.encode("utf-8")).hexdigest()
        self.max_players = self.default_capacity if max_players is None else max_players
        self.status = "OPEN"
        log.info("Game %s created (word_length=%d, lives=%d)",
                 self.game_id, len(self.game.word), self.lives)
        return True

    def get_listing(self):
        if self.status == "IDLE":
            return []
        return [{
            "game_id": self.game_id,
            "host": self.peer_id,
            "status": self.status,
            "players": self.players.copy(),
            "max_players": self.max_players,
            "lives": self.game.lives,
            "word_length": len(self.game.word),
        }]

    def join(self, peer_id, game_id):
        if game_id != self.game_id or self.status == "IDLE":
            return "unknown_game", False
        if self.status == "FINISHED":
            return "game_finished", False
        if peer_id == self.peer_id or peer_id in self.players:
            return "already_joined", False
        rejoin = self.status == "RUNNING" and peer_id in self.disconnected
        if self.status == "RUNNING" and not rejoin:
            return "game_running", False
        if len(self.players) >= self.max_players:
            return "game_full", False

        self.players.append(peer_id)
        self.disconnected.discard(peer_id)
        self.scores.setdefault(peer_id, 0)
        log.info("%s %s (%d/%d)", peer_id, "rejoined" if rejoin else "joined",
                 len(self.players), self.max_players)
        return None, rejoin

    def remove_player(self, peer_id, disconnected=False):
        if peer_id not in self.players or self.status not in ("OPEN", "RUNNING"):
            return False
        index = self.players.index(peer_id)
        self.players.pop(index)
        if self.status == "OPEN":
            self.scores.pop(peer_id, None)
        elif disconnected:
            self.disconnected.add(peer_id)
        log.info("Player %s %s", peer_id, "disconnected and removed" if disconnected else "left")
        if not self.players:
            self.turn = 0
        else:
            current_removed = index == self.turn
            self.turn = (self.turn - (index < self.turn)) % len(self.players)
            if self.status == "RUNNING" and current_removed:
                self._set_deadline()
        return True

    def start_game(self):
        if self.status != "OPEN" or not self.players:
            return None
        self.status = "RUNNING"
        self._set_deadline()
        return {
            "type": "GAME_START",
            "game_id": self.game_id,
            "players": self.players.copy(),
            "lives": self.lives,
            "word_length": len(self.game.word),
            "word_hash": self.word_hash,
            "turn_timeout": self.turn_timeout,
        }

    def get_current_player(self):
        return self.players[self.turn] if self.players else None

    def _set_deadline(self):
        self.deadline = time.monotonic() + self.turn_timeout
        log.info("Turn: %s (timeout %d s)", self.get_current_player(), self.turn_timeout)

    def _next_turn(self):
        self.turn = (self.turn + 1) % len(self.players)
        self._set_deadline()

    def expire_turn(self):
        if self.status != "RUNNING" or time.monotonic() < self.deadline:
            return False
        player = self.get_current_player()
        log.info("Turn timeout for %s", player)
        self.last_guess = {"player": player, "timeout": True}
        self._next_turn()
        return True

    def guess(self, peer_id, kind, value):
        if self.status != "RUNNING":
            return {"accepted": False, "reason": "game_not_running"}
        if peer_id != self.get_current_player():
            return {"accepted": False, "reason": "not_your_turn"}

        hidden = self.game.get_hidden_word().count("_")
        result = self.game.guess_letter(value) if kind == "letter" else self.game.guess_word(value)
        if not result["valid"]:
            return {"accepted": False, "reason": result["reason"]}

        correct = result["correct"]
        if correct:
            self.scores[peer_id] += hidden - self.game.get_hidden_word().count("_")
        self.last_guess = {"player": peer_id, kind: value, "correct": correct}
        log.info("GUESS %s %s=%s: %s, masked=%s", peer_id, kind, value,
                 "correct" if correct else "incorrect", self.game.get_hidden_word())
        if not self.game.is_finished():
            self._next_turn()
        return {"accepted": True, "correct": correct}

    def get_state(self):
        return {
            "type": "STATE",
            "game_id": self.game_id,
            "seq": self.seq,
            "masked": self.game.get_hidden_word(),
            "wrong_letters": self.game.wrong_letters.copy(),
            "lives": self.game.lives,
            "turn": self.get_current_player(),
            "players": self.players.copy(),
            "last_guess": self.last_guess,
        }

    def next_state(self):
        self.seq += 1
        return self.get_state()

    def finish_game(self, winner=None, reason=None):
        self.status = "FINISHED"
        self.finished_at = time.monotonic()
        self.deadline = None
        result = "ABORTED" if reason else ("WON" if winner else "LOST")
        message = {
            "type": "GAME_OVER",
            "game_id": self.game_id,
            "result": result,
            "word": self.game.word,
            "winner": winner,
            "scores": self.scores.copy(),
        }
        if reason:
            message["reason"] = reason
        log.info("Game %s finished: %s (%s)", self.game_id, result, reason or winner or "no winner")
        return message