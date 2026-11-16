import hashlib
import time
import uuid

from p2p_forca.hangman import Hangman


class GameHost:
    def __init__(self, peer_id, lives=6, max_players=4, turn_timeout=30):
        self.peer_id = peer_id
        self.lives = lives
        self.max_players = max_players
        self.turn_timeout = turn_timeout

        self.game_id = None
        self.players = []
        self.scores = {}
        self.game = None
        self.status = "IDLE"
        self.turn = 0
        self.seq = 0
        self.last_activity = time.time()
        self.word_hash = None

    def create_game(self, word):
        if self.status not in ("IDLE", "FINISHED"):
            return False

        self.game = Hangman(word, self.lives)
        self.game_id = str(uuid.uuid4())
        self.word_hash = hashlib.sha256(
            self.game.word.encode("utf-8")
        ).hexdigest()

        self.players = []
        self.scores = {}
        self.turn = 0
        self.seq = 0
        self.status = "OPEN"
        self.last_activity = time.time()

        return True

    def add_player(self, peer_id):
        if self.status != "OPEN":
            return False

        if peer_id == self.peer_id:
            return False

        if peer_id in self.players:
            return True

        if len(self.players) >= self.max_players:
            return False

        self.players.append(peer_id)
        self.scores[peer_id] = 0
        return True

    def remove_player(self, peer_id):
        if peer_id not in self.players:
            return False

        index = self.players.index(peer_id)
        self.players.remove(peer_id)
        self.scores.pop(peer_id, None)

        if self.players:
            if index < self.turn:
                self.turn -= 1
            self.turn %= len(self.players)
        else:
            self.turn = 0

        if self.status == "RUNNING" and not self.players:
            self.status = "FINISHED"

        return True

    def start_game(self):
        if self.status != "OPEN" or not self.players:
            return None

        self.status = "RUNNING"
        self.turn = 0
        self.last_activity = time.time()

        return {
            "type": "GAME_START",
            "game_id": self.game_id,
            "players": self.players.copy(),
            "lives": self.game.lives,
            "word_hash": self.word_hash,
        }

    def get_current_player(self):
        if not self.players:
            return None
        return self.players[self.turn]

    def _next_turn(self):
        if self.players:
            self.turn = (self.turn + 1) % len(self.players)
        self.last_activity = time.time()

    def guess_letter(self, peer_id, letter):
        if self.status != "RUNNING":
            return {"accepted": False, "message": "Partida não está em andamento."}

        if peer_id != self.get_current_player():
            return {"accepted": False, "message": "Não é sua vez."}

        result = self.game.guess_letter(letter)

        if not result["valid"]:
            return {"accepted": False, "message": result["message"]}

        if result["correct"]:
            self.scores[peer_id] += self.game.word.count(
                self.game.normalize(letter)
            )

        self._next_turn()

        if self.game.is_finished():
            return self.finish_game()

        return {
            "accepted": True,
            "correct": result["correct"],
            "state": self.get_state(),
        }

    def guess_word(self, peer_id, word):
        if self.status != "RUNNING":
            return {"accepted": False, "message": "Partida não está em andamento."}

        if peer_id != self.get_current_player():
            return {"accepted": False, "message": "Não é sua vez."}

        normalized = self.game.normalize(word)
        hidden = sum(
            1 for letter in self.game.word
            if letter not in self.game.guessed_letters
        )

        result = self.game.guess_word(normalized)

        if not result["valid"]:
            return {"accepted": False, "message": result["message"]}

        if result["correct"]:
            self.scores[peer_id] += hidden

        self._next_turn()

        if self.game.is_finished():
            return self.finish_game()

        return {
            "accepted": True,
            "correct": result["correct"],
            "state": self.get_state(),
        }

    def get_state(self):
        self.seq += 1

        return {
            "type": "STATE",
            "game_id": self.game_id,
            "seq": self.seq,
            "word": self.game.get_hidden_word(),
            "wrong_letters": sorted(self.game.wrong_letters),
            "lives": self.game.lives,
            "turn": self.get_current_player(),
            "players": self.players.copy(),
            "scores": self.scores.copy(),
        }

    def finish_game(self):
        self.status = "FINISHED"

        result = "WON" if self.game.is_won() else "LOST"
        winner = None

        if result == "WON" and self.scores:
            winner = max(self.scores, key=self.scores.get)

        return {
            "type": "GAME_OVER",
            "game_id": self.game_id,
            "result": result,
            "word": self.game.word,
            "word_hash": self.word_hash,
            "winner": winner,
            "scores": self.scores.copy(),
        }

    def cancel_game(self):
        if self.status not in ("OPEN", "RUNNING"):
            return False

        self.status = "FINISHED"
        return {
            "type": "GAME_OVER",
            "game_id": self.game_id,
            "result": "ABORTED",
        }
