import logging
import math
import shlex
import time

class CLI:
    """Interface de comandos do jogo."""

    def __init__(self, client):
        self.client = client
        self.commands = {
            "/peers": (self._peers, 0, 1, "/peers [* | #namespace]"),
            "/conn": (self._conn, 0, 0, "/conn"),
            "/rtt": (self._rtt, 0, 0, "/rtt"),
            "/reconnect": (self._reconnect, 0, 0, "/reconnect"),
            "/games": (self._games, 0, 0, "/games"),
            "/create": (self._create, 1, 2, "/create <palavra | --random> [max_players]"),
            "/start": (client.start_game, 0, 0, "/start"),
            "/cancel": (client.cancel_game, 0, 0, "/cancel"),
            "/join": (client.join_game, 1, 1, "/join <host_peer_id>"),
            "/leave": (client.leave_game, 0, 0, "/leave"),
            "/guess": (self._guess, 1, 1, "/guess <letra>"),
            "/word": (self._word, 1, 1, "/word <palavra>"),
            "/status": (self._status, 0, 0, "/status"),
            "/log": (self._log, 1, 1, "/log <DEBUG | INFO | WARNING | ERROR>"),
            "/help": (self.show_help, 0, 0, "/help"),
            "/quit": (None, 0, 0, "/quit"),
        }

    def show_help(self):
        print("[CLI] Comandos")
        for _, _, _, usage in self.commands.values():
            print(f"  {usage}")

    def execute(self, command):
        try:
            parts = shlex.split(command)
        except ValueError as error:
            print(f"[CLI] Comando inválido ({error}).")
            return True
        if not parts:
            return True
        action, *args = parts
        entry = self.commands.get(action.lower())
        if entry is None:
            print("[CLI] Comando desconhecido. Digite /help.")
            return True
        handler, minimum, maximum, usage = entry
        if not minimum <= len(args) <= maximum:
            print(f"[CLI] Uso: {usage}")
        elif handler is None:
            return False
        else:
            try:
                handler(*args)
            except (ValueError, OSError) as error:
                print(f"[CLI] {error}")
        return True

    def _peers(self, scope=None):
        if scope is None:
            namespace = self.client.namespace
        elif scope == "*":
            namespace = None
        elif scope.startswith("#") and 1 <= len(scope[1:]) <= 64:
            namespace = scope[1:]
        else:
            raise ValueError("Informe * ou #namespace.")

        peers = self.client.discover_peers(namespace)
        if peers is None:
            raise ValueError("Não foi possível consultar o rendezvous.")
        if not peers:
            print("[CLI] Nenhum outro peer encontrado.")
        for peer_id, (ip, port) in sorted(peers.items()):
            print(f"[CLI] {peer_id}  {ip}:{port}")

    def _reconnect(self):
        peers = self.client.discover_peers(self.client.namespace, force=True)
        if peers is None:
            raise ValueError("Não foi possível reconciliar com o rendezvous.")
        print(f"[CLI] Tabela atualizada com {len(peers)} peer(s) no namespace.")

    @staticmethod
    def _format_rtt(rtt):
        return f"{rtt:.1f} ms" if rtt is not None else "aguardando PONG"

    def _conn(self):
        connections = sorted(self.client.connection_status())
        if not connections:
            print("[CLI] Nenhuma conexão ativa.")
        for peer_id, direction, status, rtt in connections:
            print(f"[CLI] {peer_id}  {direction}  {status}  RTT {self._format_rtt(rtt)}")

    def _rtt(self):
        connections = sorted(self.client.connection_status())
        if not connections:
            print("[CLI] Nenhuma conexão ativa.")
        for peer_id, _, _, rtt in connections:
            print(f"[CLI] {peer_id}  RTT médio: {self._format_rtt(rtt)}")

    def _games(self):
        games = self.client.list_games()
        if not games:
            print("[CLI] Nenhuma partida aberta.")
        for game in games:
            print(f"[CLI] {game['game_id']}  host={game['host']}  "
                  f"{len(game['players'])}/{game['max_players']} jogadores  "
                  f"{game['word_length']} letras  {game['lives']} vidas")

    def _create(self, word, capacity=None):
        try:
            count = int(capacity) if capacity is not None else None
        except ValueError as error:
            raise ValueError("O número máximo de jogadores deve ser um inteiro positivo.") from error
        game_id = self.client.create_game(word, count)
        print(f"[Game] Partida {game_id} aberta.")

    def _guess(self, value):
        self.client.send_guess("letter", value.lower())

    def _word(self, value):
        self.client.send_guess("word", value.lower())

    def _status(self):
        state = self.client.game_status()
        if state is None:
            print("[Game] Nenhuma partida ativa.")
            return
        mask = " ".join(state["masked"]) or "aguardando início"
        lives = state["lives"] if state["lives"] is not None else "—"
        initial = state["initial_lives"]
        if initial is not None:
            lives = f"{lives} / {initial}"
        wrong = ", ".join(state["wrong_letters"]) or "—"
        players = ", ".join(state["players"]) or "—"
        turn = state["turn"] or "—"
        if turn == self.client.peer_id:
            turn += " (você)"
        if state["deadline"] is not None:
            remaining = max(0, math.ceil(state["deadline"] - time.monotonic()))
            turn += f" — {remaining} s restantes"
        print(f"[Game] {state['game_id']}  (host: {state['host']})  status: {state['status']}")
        print(f"[Game] Palavra: {mask}     Vidas: {lives}     Erradas: {wrong}")
        print(f"[Game] Jogadores: {players}   Vez de: {turn}")

    @staticmethod
    def _log(level):
        level = level.upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("Escolha DEBUG, INFO, WARNING ou ERROR.")
        for handler in logging.getLogger().handlers:
            if getattr(handler, "forca_console", False):
                handler.setLevel(level)
        print(f"[CLI] Log no terminal: {level}.")

    def run(self):
        self.show_help()
        try:
            while self.execute(input("forca> ")):
                pass
        except (KeyboardInterrupt, EOFError):
            print()
        finally:
            self.client.stop()