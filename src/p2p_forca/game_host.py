"""Máquina de estados do host de uma partida (IDLE → OPEN → RUNNING → FINISHED).

O host conhece a palavra, valida jogadas, controla turnos e difunde o estado.
Ele não joga. A comunicação é feita pela função ``send(peer_id, mensagem)``
injetada pelo P2PClient, o que mantém esta classe testável sem rede.
"""

import hashlib
import logging
import random
import threading
import time
import uuid
from pathlib import Path

from p2p_forca.hangman import Hangman

log = logging.getLogger("Host")

FINISHED_TTL = 60   # segundos respondendo GAME_LIST_OK com FINISHED

# Usada apenas se o words.txt não puder ser lido.
FALLBACK_WORDS = [
    "banana", "computador", "rede", "protocolo", "servidor", "cliente",
    "soquete", "mensagem", "pacote", "roteador", "internet", "programa",
    "python", "forca", "jogador", "partida", "conexao", "descoberta",
]


def load_words(path):
    """Lê o arquivo de palavras (uma por linha) e devolve só as válidas."""
    words = []

    try:
        with Path(path).open("r", encoding="utf-8") as file:
            for line in file:
                word = Hangman.normalize(line)

                if Hangman.is_valid_word(word):
                    words.append(word)

    except OSError as error:
        log.warning("[Host] could not read %s: %s", path, error)

    return words


def random_word(path=None):
    """Sorteia uma palavra de ``path`` (ou da lista interna)."""
    words = load_words(path) if path else []

    if not words:
        log.warning("[Host] using built-in word list")
        words = FALLBACK_WORDS

    return random.choice(words)


class GameHost:
    """Estado autoritativo de uma partida hospedada localmente."""

    def __init__(self, peer_id, lives=6, max_players=4, turn_timeout=30,
                 send=None, notify=None):
        self.peer_id = peer_id
        self.lives = lives
        self.max_players = max_players
        self.turn_timeout = turn_timeout

        self._send_func = send
        self._notify_func = notify or print

        self.lock = threading.RLock()
        self._reset()

    # ------------------------------------------------------------------
    # utilitários internos
    # ------------------------------------------------------------------
    def _reset(self):
        self.game_id = None
        self.game = None
        self.status = "IDLE"
        self.players = []            # jogadores ativos, na ordem dos turnos
        self.known_players = []      # todos que já participaram (rejoin)
        self.scores = {}
        self.game_max_players = self.max_players
        self.turn = 0                # índice em self.players
        self.seq = 0
        self.word_hash = None
        self.last_guess = None
        self.winner = None
        self.finished_at = None
        self.turn_started = None
        self.turn_token = 0
        self._cancel_timer()

    def _cancel_timer(self):
        timer = getattr(self, "_timer", None)

        if timer is not None:
            timer.cancel()

        self._timer = None

    def _notify(self, text):
        try:
            self._notify_func(text)
        except Exception:
            pass

    def _send(self, dst, message):
        """Preenche src/dst/ttl e entrega ao P2PClient."""
        message["src"] = self.peer_id
        message["dst"] = dst
        message["ttl"] = 1
        message.setdefault("msg_id", str(uuid.uuid4()))

        if self._send_func is None:
            return False

        try:
            return bool(self._send_func(dst, message))
        except Exception:
            log.exception("[Host] send to %s failed", dst)
            return False

    def _send_error(self, dst, ref, code, text):
        self._send(dst, {
            "type": "ERROR",
            "ref_msg_id": ref,
            "code": code,
            "message": text,
        })

    def refresh(self):
        """FINISHED vira IDLE depois de 60 s."""
        with self.lock:
            if (
                self.status == "FINISHED"
                and self.finished_at is not None
                and time.monotonic() - self.finished_at >= FINISHED_TTL
            ):
                log.info("[Host] game %s expired - back to IDLE", self.game_id)
                self._reset()

    # ------------------------------------------------------------------
    # consultas
    # ------------------------------------------------------------------
    def is_active(self):
        """True se há partida OPEN ou RUNNING."""
        self.refresh()
        return self.status in ("OPEN", "RUNNING")

    def get_current_player(self):
        with self.lock:
            if not self.players:
                return None

            return self.players[self.turn % len(self.players)]

    def remaining_turn_time(self):
        with self.lock:
            if self.status != "RUNNING" or self.turn_started is None:
                return None

            left = self.turn_timeout - (time.monotonic() - self.turn_started)

            return max(0, int(round(left)))

    def get_game_info(self):
        """Entrada ``games[]`` do GAME_LIST_OK (ou None sem partida)."""
        self.refresh()

        with self.lock:
            if self.status == "IDLE" or self.game is None:
                return None

            return {
                "game_id": self.game_id,
                "host": self.peer_id,
                "status": self.status,
                "players": list(self.players),
                "max_players": self.game_max_players,
                "lives": self.lives,
                "word_length": len(self.game.word),
            }

    def status_lines(self):
        """Linhas do /status para o host."""
        info = self.get_game_info()

        if info is None:
            return ["[Game] Nenhuma partida hospedada."]

        with self.lock:
            masked = self.game.get_hidden_word()
            wrong = ", ".join(self.game.wrong_order) or "-"
            lines = [
                f"[Game] {self.game_id}  (host: {self.peer_id})  "
                f"status: {self.status}",
                f"[Game] Palavra: {masked}     "
                f"Vidas: {self.game.lives} / {self.lives}     "
                f"Erradas: {wrong}",
                f"[Game] Segredo (só o host vê): {self.game.word}",
            ]

            players = ", ".join(self.players) or "-"
            line = f"[Game] Jogadores: {players}"

            if self.status == "RUNNING":
                current = self.get_current_player()
                left = self.remaining_turn_time()
                line += f"   Vez de: {current}  — {left} s restantes"

            lines.append(line)

            if self.scores:
                scores = ", ".join(
                    f"{peer}={points}" for peer, points in self.scores.items()
                )
                lines.append(f"[Game] Pontos: {scores}")

            return lines

    # ------------------------------------------------------------------
    # ciclo de vida (comandos locais)
    # ------------------------------------------------------------------
    def create_game(self, word, max_players=None):
        """/create: devolve o game_id ou None se já há partida ativa.

        Lança ValueError se a palavra for inválida.
        """
        self.refresh()

        with self.lock:
            if self.status not in ("IDLE", "FINISHED"):
                return None

            game = Hangman(word, self.lives)     # pode lançar ValueError

            if max_players is None:
                max_players = self.max_players

            if max_players < 1:
                raise ValueError("max_players deve ser pelo menos 1.")

            self._reset()

            self.game = game
            self.game_id = f"{self.peer_id}/{uuid.uuid4().hex[:8]}"
            self.word_hash = hashlib.sha256(
                game.word.encode("utf-8")
            ).hexdigest()
            self.game_max_players = max_players
            self.status = "OPEN"

            log.info(
                "[Host] Game %s created (word_length=%d, lives=%d)",
                self.game_id, len(game.word), self.lives
            )

            return self.game_id

    def start_game(self):
        """/start: OPEN → RUNNING. Devolve (ok, mensagem)."""
        with self.lock:
            if self.status != "OPEN":
                return False, "Não há partida aberta para iniciar."

            if not self.players:
                return False, "É preciso pelo menos 1 jogador para iniciar."

            self.status = "RUNNING"
            self.turn = 0
            self.seq = 0

            log.info(
                "[Host] Game %s started with %d player(s)",
                self.game_id, len(self.players)
            )

            for player in list(self.players):
                self._send(player, {
                    "type": "GAME_START",
                    "game_id": self.game_id,
                    "players": list(self.players),
                    "lives": self.game.lives,
                    "word_length": len(self.game.word),
                    "word_hash": self.word_hash,
                    "turn_timeout": self.turn_timeout,
                })

            self._notify(
                f"[Game] Partida iniciada com {len(self.players)} jogador(es)."
            )

            self._broadcast_state(None, new_turn=True)

            return True, "Partida iniciada."

    def cancel_game(self, reason="host_shutdown"):
        """/cancel e /quit: encerra com GAME_OVER ABORTED."""
        with self.lock:
            if self.status not in ("OPEN", "RUNNING"):
                return False

            self._finish("ABORTED", None, reason=reason)

            return True

    shutdown = cancel_game

    # ------------------------------------------------------------------
    # difusão de STATE e turnos
    # ------------------------------------------------------------------
    def _state_message(self, last_guess):
        return {
            "type": "STATE",
            "game_id": self.game_id,
            "seq": self.seq,
            "masked": self.game.get_masked(),
            "wrong_letters": list(self.game.wrong_order),
            "lives": self.game.lives,
            "turn": self.get_current_player(),
            "players": list(self.players),
            "last_guess": last_guess,
        }

    def _broadcast_state(self, last_guess, new_turn):
        """Incrementa seq e envia STATE a todos os jogadores."""
        self.seq += 1
        self.last_guess = last_guess

        if new_turn:
            self._start_turn_timer()

        for player in list(self.players):
            self._send(player, self._state_message(last_guess))

        log.info(
            "[Host] STATE seq=%d masked=%s lives=%d turn=%s",
            self.seq, self.game.get_masked(), self.game.lives,
            self.get_current_player()
        )

    def _start_turn_timer(self):
        self._cancel_timer()

        self.turn_token += 1
        self.turn_started = time.monotonic()

        if not self.players or self.status != "RUNNING":
            return

        log.info(
            "[Host] Turn: %s (timeout %d s)",
            self.get_current_player(), self.turn_timeout
        )

        self._timer = threading.Timer(
            self.turn_timeout,
            self._on_turn_timeout,
            args=(self.turn_token,)
        )
        self._timer.daemon = True
        self._timer.start()

    def _advance_turn(self):
        if self.players:
            self.turn = (self.turn + 1) % len(self.players)

    def _on_turn_timeout(self, token):
        with self.lock:
            if self.status != "RUNNING" or token != self.turn_token:
                return

            player = self.get_current_player()

            log.info("[Host] Turn timeout for %s - skipping", player)
            self._notify(f"[Game] Tempo esgotado para {player}.")

            self._advance_turn()
            self._broadcast_state(
                {"player": player, "timeout": True},
                new_turn=True
            )

    # ------------------------------------------------------------------
    # término
    # ------------------------------------------------------------------
    def _finish(self, result, winner, reason=None):
        self._cancel_timer()
        self.turn_token += 1

        self.status = "FINISHED"
        self.finished_at = time.monotonic()
        self.winner = winner

        scores = {
            peer: self.scores.get(peer, 0) for peer in self.known_players
        }

        message = {
            "type": "GAME_OVER",
            "game_id": self.game_id,
            "result": result,
            "word": self.game.word,
            "winner": winner,
            "scores": scores,
        }

        if result == "ABORTED":
            message["reason"] = reason or "host_shutdown"

        for player in list(self.players):
            self._send(player, dict(message))

        log.info(
            "[Host] Game %s finished: %s%s",
            self.game_id, result,
            f" by {winner}" if winner else ""
        )
        self._notify(
            f"[Game] Partida encerrada: {result}"
            + (f" (vencedor: {winner})" if winner else "")
            + f" — palavra: {self.game.word}"
        )

    # ------------------------------------------------------------------
    # jogadores
    # ------------------------------------------------------------------
    def _remove_player(self, peer_id):
        """Remove um jogador e atualiza turno/difusão. True se removeu."""
        if peer_id not in self.players:
            return False

        index = self.players.index(peer_id)
        was_current = (
            self.status == "RUNNING"
            and index == self.turn % len(self.players)
        )

        self.players.remove(peer_id)

        if self.status == "OPEN":
            self.scores.pop(peer_id, None)
            if peer_id in self.known_players:
                self.known_players.remove(peer_id)
            return True

        if self.status != "RUNNING":
            return True

        if not self.players:
            log.info("[Host] No players left - game aborted")
            self._finish("ABORTED", None, reason="no_players")
            return True

        if index < self.turn:
            self.turn -= 1

        self.turn %= len(self.players)

        self._broadcast_state(None, new_turn=was_current)

        return True

    def player_lost(self, peer_id, reason="STALE"):
        """Jogador caiu (STALE ou socket fechado)."""
        with self.lock:
            if peer_id not in self.players:
                return False

            log.info(
                "[Host] Player %s %s - removed from game", peer_id, reason
            )
            self._notify(f"[Game] {peer_id} saiu da partida ({reason}).")

            return self._remove_player(peer_id)

    # ------------------------------------------------------------------
    # handlers de mensagem (chamados pelo MessageRouter)
    # ------------------------------------------------------------------
    def handle_game_list(self, connection, message):
        info = self.get_game_info()

        self._send(message["src"], {
            "type": "GAME_LIST_OK",
            "msg_id": message["msg_id"],
            "games": [info] if info else [],
        })

    def _join_err(self, dst, message, reason):
        self._send(dst, {
            "type": "JOIN_ERR",
            "msg_id": message["msg_id"],
            "game_id": message.get("game_id"),
            "reason": reason,
        })

    def handle_join(self, connection, message):
        src = message["src"]
        game_id = message.get("game_id")

        if not isinstance(game_id, str):
            self._send_error(
                src, message["msg_id"], "bad_request", "game_id required"
            )
            return

        self.refresh()

        with self.lock:
            if (
                self.game is None
                or game_id != self.game_id
                or src == self.peer_id
            ):
                self._join_err(src, message, "unknown_game")
                return

            if self.status == "FINISHED":
                self._join_err(src, message, "game_finished")
                return

            if self.status == "OPEN":
                if src in self.players:
                    self._join_err(src, message, "already_joined")
                    return

                if len(self.players) >= self.game_max_players:
                    self._join_err(src, message, "game_full")
                    return

                self.players.append(src)
                self.scores[src] = 0

                if src not in self.known_players:
                    self.known_players.append(src)

                self._send(src, {
                    "type": "JOIN_OK",
                    "msg_id": message["msg_id"],
                    "game_id": self.game_id,
                    "players": list(self.players),
                    "rejoin": False,
                })

                log.info(
                    "[Host] %s joined (%d/%d)",
                    src, len(self.players), self.game_max_players
                )
                self._notify(
                    f"[Game] {src} entrou "
                    f"({len(self.players)}/{self.game_max_players})."
                )
                return

            # RUNNING
            if src in self.players:
                # Ainda constava como ativo: reenvia o STATE corrente.
                self._send(src, {
                    "type": "JOIN_OK",
                    "msg_id": message["msg_id"],
                    "game_id": self.game_id,
                    "players": list(self.players),
                    "rejoin": True,
                })
                self._send(src, self._state_message(self.last_guess))
                return

            if src in self.known_players:
                self.players.append(src)     # volta ao fim do rodízio

                self._send(src, {
                    "type": "JOIN_OK",
                    "msg_id": message["msg_id"],
                    "game_id": self.game_id,
                    "players": list(self.players),
                    "rejoin": True,
                })

                log.info("[Host] %s rejoined the game", src)
                self._notify(f"[Game] {src} voltou à partida.")

                # A lista de jogadores mudou: STATE para todos.
                self._broadcast_state(None, new_turn=False)
                return

            self._join_err(src, message, "game_running")

    def handle_leave(self, connection, message):
        src = message["src"]

        with self.lock:
            if (
                self.game is None
                or message.get("game_id") != self.game_id
                or src not in self.players
            ):
                self._send_error(
                    src, message["msg_id"], "invalid_state",
                    "LEAVE received but not in a game"
                )
                return

            self._send(src, {
                "type": "LEAVE_OK",
                "msg_id": message["msg_id"],
                "game_id": self.game_id,
            })

            log.info("[Host] %s left the game", src)
            self._notify(f"[Game] {src} saiu da partida.")

            self._remove_player(src)

    def handle_guess(self, connection, message):
        src = message["src"]
        msg_id = message["msg_id"]

        has_letter = "letter" in message
        has_word = "word" in message

        if has_letter == has_word:
            self._send_error(
                src, msg_id, "bad_request",
                "exactly one of letter/word is required"
            )
            return

        value = message["letter"] if has_letter else message["word"]

        if not isinstance(value, str) or not isinstance(
            message.get("game_id"), str
        ):
            self._send_error(
                src, msg_id, "bad_request", "invalid GUESS fields"
            )
            return

        self.refresh()

        with self.lock:
            if (
                self.game is None
                or self.status == "IDLE"
                or message["game_id"] != self.game_id
            ):
                self._send_error(
                    src, msg_id, "invalid_state",
                    "GUESS received but no game running"
                )
                return

            def reject(reason):
                self._send(src, {
                    "type": "GUESS_RESULT",
                    "msg_id": msg_id,
                    "game_id": self.game_id,
                    "accepted": False,
                    "reason": reason,
                })

            if self.status != "RUNNING":
                reject("game_not_running")
                return

            if src != self.get_current_player():
                reject("not_your_turn")
                return

            if has_letter:
                result = self.game.guess_letter(value)
            else:
                result = self.game.guess_word(value)

            if not result["valid"]:
                reject(result["reason"])
                return

            correct = result["correct"]

            if correct:
                self.scores[src] = self.scores.get(src, 0) + result["count"]

            if has_letter:
                last_guess = {
                    "player": src,
                    "letter": self.game.normalize(value),
                    "correct": correct,
                }
                what = f"letter={last_guess['letter']}"
            else:
                last_guess = {
                    "player": src,
                    "word": self.game.normalize(value),
                    "correct": correct,
                }
                what = f"word={last_guess['word']}"

            self._send(src, {
                "type": "GUESS_RESULT",
                "msg_id": msg_id,
                "game_id": self.game_id,
                "accepted": True,
                "correct": correct,
            })

            log.info(
                "[Host] GUESS %s %s -> %s, masked=%s",
                src, what, "correct" if correct else "wrong",
                self.game.get_masked()
            )

            if self.game.is_won():
                self._finish("WON", src)
            elif self.game.is_lost():
                self._finish("LOST", None)
            else:
                self._advance_turn()
                self._broadcast_state(last_guess, new_turn=True)