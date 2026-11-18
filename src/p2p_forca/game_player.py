"""Máquina de estados do jogador (IDLE → JOINED → PLAYING → IDLE).

O jogador só conversa com o host da partida; o estado autoritativo vem
sempre dos ``STATE`` enviados por ele. A comunicação é feita por
``send(peer_id, mensagem)``, injetada pelo P2PClient.
"""

import hashlib
import logging
import threading
import time
import uuid

log = logging.getLogger("Player")

REJECT_TEXT = {
    "not_your_turn": "não é a sua vez",
    "letter_already_used": "essa letra já foi usada",
    "invalid_letter": "letra inválida (use uma letra de a a z)",
    "invalid_word": "palavra inválida (3 a 20 letras, a-z)",
    "game_not_running": "a partida não está em andamento",
}

JOIN_ERR_TEXT = {
    "unknown_game": "partida desconhecida",
    "game_full": "partida cheia",
    "game_running": "partida já em andamento",
    "game_finished": "partida já terminou",
    "already_joined": "você já está nessa partida",
}


class GamePlayer:
    """Gerencia a participação de um jogador em uma partida."""

    def __init__(self, peer_id, send=None, notify=None):
        self.peer_id = peer_id
        self._send_func = send
        self._notify_func = notify or print

        self.lock = threading.RLock()
        self.join_event = threading.Event()
        self.leave_event = threading.Event()
        self.join_result = None          # ("OK", rejoin) | ("ERR", reason)

        self.last_result = None
        self._reset()

    # ------------------------------------------------------------------
    # utilitários
    # ------------------------------------------------------------------
    def _reset(self):
        self.host_id = None
        self.game_id = None
        self.status = "IDLE"
        self.players = []
        self.lives = 0
        self.max_lives = 0
        self.word_length = 0
        self.masked = ""
        self.wrong_letters = []
        self.turn = None
        self.seq = 0
        self.word_hash = None
        self.turn_timeout = 30
        self.turn_started = None
        self.last_state = None
        self.last_guess = None

    def _notify(self, text):
        try:
            self._notify_func(text)
        except Exception:
            pass

    def _send(self, message):
        """Envia ao host da partida atual."""
        message["src"] = self.peer_id
        message["dst"] = self.host_id
        message["ttl"] = 1
        message.setdefault("msg_id", str(uuid.uuid4()))

        if self._send_func is None:
            return None

        try:
            ok = self._send_func(self.host_id, message)
        except Exception:
            log.exception("[Player] send failed")
            return None

        return message["msg_id"] if ok else None

    def is_in_game(self):
        return self.status != "IDLE"

    # ------------------------------------------------------------------
    # entrada / saída
    # ------------------------------------------------------------------
    def request_join(self, host_id, game_id):
        """Envia JOIN ao host. Devolve False se já há partida em curso."""
        with self.lock:
            if self.status != "IDLE":
                return False

            self.host_id = host_id
            self.game_id = game_id
            self.status = "JOINING"
            self.join_result = None
            self.join_event.clear()

            if self._send({"type": "JOIN", "game_id": game_id}) is None:
                self._reset()
                return False

            return True

    def wait_join(self, timeout=5):
        """Aguarda JOIN_OK/JOIN_ERR. Devolve ``join_result`` (ou None)."""
        if not self.join_event.wait(timeout):
            with self.lock:
                if self.status == "JOINING":
                    self._reset()
            return None

        return self.join_result

    def join_game(self, host_id, game_id):
        """Marca localmente a entrada na partida (após JOIN_OK)."""
        with self.lock:
            if self.status not in ("IDLE", "JOINING"):
                return False

            self.host_id = host_id
            self.game_id = game_id
            self.status = "JOINED"

            return True

    def leave(self):
        """/leave: envia LEAVE ao host. Devolve False se não há partida."""
        with self.lock:
            if self.status not in ("JOINED", "PLAYING"):
                return False

            self.leave_event.clear()

            return self._send(
                {"type": "LEAVE", "game_id": self.game_id}
            ) is not None

    def wait_leave(self, timeout=2):
        """Aguarda LEAVE_OK e volta a IDLE (mesmo sem resposta)."""
        self.leave_event.wait(timeout)
        self.leave_game()

    def leave_game(self):
        """Esquece a partida localmente."""
        with self.lock:
            self._reset()

    def on_peer_lost(self, peer_id):
        """Conexão com ``peer_id`` caiu. Se for o host, aborta a partida."""
        with self.lock:
            if self.status == "IDLE" or peer_id != self.host_id:
                return False

            log.warning(
                "[Player] Host %s unreachable - game aborted", peer_id
            )
            self._notify(
                f"[Game] Host {peer_id} unreachable — game aborted"
            )

            self._reset()
            self.join_event.set()
            self.leave_event.set()

            return True

    # ------------------------------------------------------------------
    # jogadas
    # ------------------------------------------------------------------
    def guess_letter(self, letter):
        """/guess: devolve (ok, texto)."""
        letter = str(letter).strip().lower()

        with self.lock:
            if self.status != "PLAYING":
                return False, "Você não está em uma partida em andamento."

            if self._send({
                "type": "GUESS",
                "game_id": self.game_id,
                "letter": letter,
            }) is None:
                return False, "Falha ao enviar a jogada ao host."

            return True, "Jogada enviada."

    def guess_word(self, word):
        """/word: devolve (ok, texto)."""
        with self.lock:
            if self.status != "PLAYING":
                return False, "Você não está em uma partida em andamento."

            if self._send({
                "type": "GUESS",
                "game_id": self.game_id,
                "word": str(word).strip().lower(),
            }) is None:
                return False, "Falha ao enviar a jogada ao host."

            return True, "Palpite enviado."

    def is_my_turn(self):
        return self.status == "PLAYING" and self.turn == self.peer_id

    # ------------------------------------------------------------------
    # handlers de mensagem
    # ------------------------------------------------------------------
    def _from_host(self, message):
        return (
            message.get("src") == self.host_id
            and message.get("game_id") == self.game_id
        )

    def handle_join_ok(self, connection, message):
        with self.lock:
            if self.status != "JOINING" or not self._from_host(message):
                log.debug("[Player] unexpected JOIN_OK ignored")
                return

            self.join_game(message["src"], message["game_id"])
            self.players = list(message.get("players", []))
            rejoin = bool(message.get("rejoin", False))
            self.join_result = ("OK", rejoin)

            log.info(
                "[Player] joined %s (rejoin=%s, players=%s)",
                self.game_id, rejoin, self.players
            )
            self._notify(
                f"[Game] Você entrou na partida {self.game_id} "
                f"(jogadores: {', '.join(self.players)})."
            )

        self.join_event.set()

    def handle_join_err(self, connection, message):
        with self.lock:
            if self.status != "JOINING":
                return

            reason = message.get("reason", "?")
            self.join_result = ("ERR", reason)

            log.info("[Player] JOIN refused: %s", reason)
            self._notify(
                "[Game] Não foi possível entrar: "
                + JOIN_ERR_TEXT.get(reason, str(reason))
            )

            self._reset()

        self.join_event.set()

    def handle_leave_ok(self, connection, message):
        with self.lock:
            if message.get("src") != self.host_id:
                return

            self._notify("[Game] Você saiu da partida.")
            self._reset()

        self.leave_event.set()

    def handle_game_start(self, connection, message):
        with self.lock:
            if self.status != "JOINED" or not self._from_host(message):
                log.debug("[Player] unexpected GAME_START ignored")
                return

            self.players = list(message.get("players", []))
            self.lives = message.get("lives", 6)
            self.max_lives = self.lives
            self.word_length = message.get("word_length", 0)
            self.word_hash = message.get("word_hash")
            self.turn_timeout = message.get("turn_timeout", 30)
            self.masked = "_" * self.word_length
            self.seq = 0
            self.status = "PLAYING"

            log.info(
                "[Player] GAME_START %s players=%s lives=%s word_length=%s",
                self.game_id, self.players, self.lives, self.word_length
            )
            self._notify(
                f"[Game] Partida iniciada! Palavra com "
                f"{self.word_length} letras, {self.lives} vidas."
            )

    def apply_state(self, message):
        """Aplica um STATE; devolve False se descartado."""
        with self.lock:
            if not self._from_host(message):
                return False

            new_seq = message.get("seq")

            if not isinstance(new_seq, int):
                return False

            if new_seq <= self.seq:
                log.debug(
                    "[Player] STATE seq=%s ignored (already at seq=%s)",
                    new_seq, self.seq
                )
                return False

            self.seq = new_seq
            self.masked = message.get("masked", message.get("word", ""))
            self.wrong_letters = list(message.get("wrong_letters", []))
            self.lives = message.get("lives", self.lives)
            self.turn = message.get("turn")
            self.players = list(message.get("players", self.players))
            self.last_guess = message.get("last_guess")
            self.last_state = message
            self.turn_started = time.monotonic()

            if self.status == "JOINED":
                # Reentrada: não houve GAME_START para este processo.
                self.status = "PLAYING"
                self.word_length = len(self.masked)
                self.max_lives = max(self.max_lives, self.lives)

            return True

    def handle_state(self, connection, message):
        if self.status == "IDLE":
            return

        if not self.apply_state(message):
            return

        with self.lock:
            log.info(
                "[Player] STATE seq=%d applied: %s lives=%s turn=%s",
                self.seq, self.masked, self.lives, self.turn
            )

            self._notify(self._describe_last_guess())
            self._notify(self._summary_line())

    def handle_guess_result(self, connection, message):
        with self.lock:
            if message.get("game_id") != self.game_id:
                return

            if message.get("accepted"):
                text = "acertou!" if message.get("correct") else "errou."
                self._notify(f"[Game] Sua jogada foi aceita — {text}")
            else:
                reason = message.get("reason", "?")
                self._notify(
                    "[Game] Jogada rejeitada: "
                    + REJECT_TEXT.get(reason, str(reason))
                )

    def verify_game_over(self, message):
        """True se sha256(word) == word_hash recebido no GAME_START."""
        word = message.get("word")

        if not isinstance(word, str) or not self.word_hash:
            return False

        received = hashlib.sha256(word.encode("utf-8")).hexdigest()

        return received == self.word_hash

    def handle_game_over(self, connection, message):
        with self.lock:
            if self.status == "IDLE" or not self._from_host(message):
                log.debug("[Player] unexpected GAME_OVER ignored")
                return

            result = message.get("result")
            word = message.get("word")
            winner = message.get("winner")

            if not self.word_hash:
                hash_text = "hash unverified"
            elif self.verify_game_over(message):
                hash_text = "hash OK"
            else:
                hash_text = "hash MISMATCH"
                log.warning("[Player] host cheated: hash mismatch")

            log.info(
                "[Player] GAME_OVER %s word=%s winner=%s - %s",
                result, word, winner, hash_text
            )

            lines = [
                f"[Game] FIM DE JOGO: {result} — palavra: {word}"
                + (f" — vencedor: {winner}" if winner else "")
                + f" ({hash_text})"
            ]

            if message.get("reason"):
                lines.append(f"[Game] Motivo: {message['reason']}")

            scores = message.get("scores") or {}

            if isinstance(scores, dict) and scores:
                ranking = sorted(
                    scores.items(), key=lambda item: item[1], reverse=True
                )
                lines.append(
                    "[Game] Pontuação: "
                    + ", ".join(f"{p}={s}" for p, s in ranking)
                )

            for line in lines:
                self._notify(line)

            self.last_result = message
            self._reset()

    # ------------------------------------------------------------------
    # exibição
    # ------------------------------------------------------------------
    def _describe_last_guess(self):
        guess = self.last_guess

        if not guess:
            return "[Game] Nova rodada."

        player = guess.get("player")

        if guess.get("timeout"):
            return f"[Game] Tempo esgotado para {player}."

        if "letter" in guess:
            what = f"a letra '{guess['letter']}'"
        else:
            what = f"a palavra '{guess.get('word')}'"

        verdict = "acertou" if guess.get("correct") else "errou"

        return f"[Game] {player} tentou {what} — {verdict}."

    def _turn_label(self):
        if self.turn == self.peer_id:
            return f"{self.turn} (você)"

        return str(self.turn)

    def _remaining(self):
        if self.turn_started is None:
            return None

        left = self.turn_timeout - (time.monotonic() - self.turn_started)

        return max(0, int(round(left)))

    def _summary_line(self):
        wrong = ", ".join(self.wrong_letters) or "-"

        return (
            f"[Game] Palavra: {' '.join(self.masked)}     "
            f"Vidas: {self.lives} / {self.max_lives}     "
            f"Erradas: {wrong}     Vez de: {self._turn_label()}"
        )

    def status_lines(self):
        """Linhas do /status para o jogador."""
        with self.lock:
            if self.status == "IDLE":
                return ["[Game] Você não está em nenhuma partida."]

            lines = [
                f"[Game] {self.game_id}  (host: {self.host_id})  "
                f"status: {'RUNNING' if self.status == 'PLAYING' else 'OPEN'}"
            ]

            if self.status == "PLAYING":
                wrong = ", ".join(self.wrong_letters) or "-"
                lines.append(
                    f"[Game] Palavra: {' '.join(self.masked)}     "
                    f"Vidas: {self.lives} / {self.max_lives}     "
                    f"Erradas: {wrong}"
                )

                line = f"[Game] Jogadores: {', '.join(self.players)}"
                left = self._remaining()

                line += f"   Vez de: {self._turn_label()}"

                if left is not None:
                    line += f"  — {left} s restantes"

                lines.append(line)
            else:
                lines.append(
                    f"[Game] Jogadores: {', '.join(self.players)} "
                    "(aguardando o host iniciar)"
                )

            return lines

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