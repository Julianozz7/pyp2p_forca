import hashlib


class GamePlayer:
    """Gerencia a participação de um jogador em uma partida."""

    def __init__(self, peer_id):
        self.peer_id = peer_id
        self.host_id = None
        self.game_id = None
        self.status = "IDLE"
        self.players = []
        self.lives = 0
        self.masked = ""
        self.turn = None
        self.seq = 0
        self.word_hash = None
        self.last_state = None

    def join_game(self, host_id, game_id):
        if self.status != "IDLE":
            return False

        self.host_id = host_id
        self.game_id = game_id
        self.status = "JOINED"

        return True

    def leave_game(self):
        self.host_id = None
        self.game_id = None
        self.status = "IDLE"
        self.players = []
        self.lives = 0
        self.masked = ""
        self.turn = None
        self.seq = 0
        self.word_hash = None
        self.last_state = None

    def start_game(self, message):
        if message.get("game_id") != self.game_id:
            return False

        self.players = message.get("players", [])
        self.lives = message.get("lives", 6)
        self.word_hash = message.get("word_hash")
        self.status = "PLAYING"

        return True

    def apply_state(self, message):
        if message.get("game_id") != self.game_id:
            return False

        new_seq = message.get("seq", 0)

        if new_seq <= self.seq:
            return False

        self.seq = new_seq
        self.masked = message.get("word", message.get("masked", ""))
        self.lives = message.get("lives", self.lives)
        self.turn = message.get("turn")
        self.players = message.get("players", self.players)
        self.last_state = message

        return True

    def is_my_turn(self):
        return (
            self.status == "PLAYING"
            and self.turn == self.peer_id
        )

    def verify_game_over(self, message):
        if message.get("game_id") != self.game_id:
            return False

        word = message.get("word")

        if not word or not self.word_hash:
            return False

        received_hash = hashlib.sha256(
            word.encode("utf-8")
        ).hexdigest()

        if received_hash != self.word_hash:
            return False

        self.status = "FINISHED"

        return True

    def get_status(self):
        return {
            "peer_id": self.peer_id,
            "host_id": self.host_id,
            "game_id": self.game_id,
            "status": self.status,
            "masked": self.masked,
            "lives": self.lives,
            "turn": self.turn,
            "seq": self.seq
        }
