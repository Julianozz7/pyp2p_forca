"""Interface de linha de comando do jogo."""

import logging

HELP_TEXT = """
=============== P2P FORCA ===============
/peers [* | #namespace]   Descobre e lista peers (padrão: namespace local)
/conn                     Lista conexões (direção, estado, RTT)
/rtt                      RTT médio por peer
/reconnect                Força reconciliação com o Rendezvous
/games                    Lista partidas dos vizinhos (GAME_LIST)
/create PALAVRA [max]     Cria partida (você vira host)
/create --random [max]    Cria partida com palavra sorteada
/start                    Inicia a partida hospedada (>= 1 jogador)
/cancel                   Cancela a partida hospedada
/join HOST_PEER_ID        Entra na partida de um host (ex.: alice@CIC)
/leave                    Sai da partida atual
/guess LETRA              Palpite de letra
/word PALAVRA             Palpite da palavra inteira
/status                   Estado local da partida
/log NIVEL                DEBUG | INFO | WARNING | ERROR
/help                     Mostra esta ajuda
/quit                     Encerra o programa
=========================================
"""

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class CLI:
    """Interface de comandos do jogo."""

    def __init__(self, client):
        self.client = client

    # ------------------------------------------------------------------
    # saída
    # ------------------------------------------------------------------
    @staticmethod
    def out(text):
        print(f"[CLI] {text}")

    def show_help(self):
        print(HELP_TEXT)

    # ------------------------------------------------------------------
    # comandos
    # ------------------------------------------------------------------
    def cmd_peers(self, args):
        scope = args[0] if args else None

        if scope and scope != "*" and not scope.startswith("#"):
            self.out("Uso: /peers [* | #namespace]")
            return

        peers = self.client.list_peers(scope)

        if peers is None:
            self.out("Não foi possível consultar o Rendezvous.")
            return

        if not peers:
            self.out("Nenhum outro peer encontrado.")
            return

        self.out(f"{len(peers)} peer(s):")

        for peer in peers:
            entry = self.client.peer_table.get_peer(peer["peer_id"])
            state = entry["status"] if entry else "-"

            print(
                f"  {peer['peer_id']:<24} {peer['ip']}:{peer['port']:<6} "
                f"expira em {peer.get('expires_in', '?')} s   [{state}]"
            )

    def cmd_conn(self, args):
        rows = self.client.list_connections()

        if not rows:
            self.out("Nenhum peer conhecido.")
            return

        print(
            f"  {'PEER':<24} {'DIREÇÃO':<9} {'ESTADO':<11} "
            f"{'RTT(ms)':<8} ENDEREÇO"
        )

        for row in rows:
            rtt = f"{row['rtt']:.1f}" if row["rtt"] is not None else "-"
            address = row["address"]
            address = f"{address[0]}:{address[1]}" if address else "-"

            print(
                f"  {row['peer_id']:<24} {row['direction']:<9} "
                f"{row['status']:<11} {rtt:<8} {address}"
            )

    def cmd_rtt(self, args):
        shown = False

        for row in self.client.list_connections():
            if row["rtt"] is not None:
                print(f"  {row['peer_id']:<24} RTT médio = {row['rtt']:.1f} ms")
                shown = True

        if not shown:
            self.out("Ainda não há medições de RTT.")

    def cmd_reconnect(self, args):
        ok = self.client.force_reconcile()
        self.out("Reconciliação concluída." if ok
                 else "Falha ao consultar o Rendezvous.")

    def cmd_games(self, args):
        games = self.client.list_games()

        if not games:
            self.out("Nenhuma partida encontrada.")
            return

        self.out("Partidas:")

        for game in games:
            players = game.get("players", [])

            print(
                f"  {game.get('game_id')}  host={game.get('host')}  "
                f"{len(players)}/{game.get('max_players')} jogadores  "
                f"{game.get('word_length')} letras  "
                f"{game.get('lives')} vidas  [{game.get('status')}]"
            )

    def cmd_create(self, args):
        if not args:
            self.out("Uso: /create <palavra> [max_players]  |  "
                     "/create --random [max_players]")
            return

        word = None if args[0] == "--random" else args[0]
        max_players = None

        if len(args) > 1:
            try:
                max_players = int(args[1])
            except ValueError:
                self.out("max_players deve ser um número inteiro.")
                return

        ok, text = self.client.create_game(word, max_players)
        self.out(text)

    def cmd_start(self, args):
        ok, text = self.client.start_game()

        if not ok:
            self.out(text)

    def cmd_cancel(self, args):
        ok, text = self.client.cancel_game()
        self.out(text)

    def cmd_join(self, args):
        if len(args) != 1:
            self.out("Uso: /join <host_peer_id>")
            return

        ok, text = self.client.join_game(args[0])
        self.out(text)

    def cmd_leave(self, args):
        ok, text = self.client.leave_game()
        self.out(text)

    def cmd_guess(self, args):
        if len(args) != 1 or len(args[0]) != 1:
            self.out("Uso: /guess <letra>")
            return

        ok, text = self.client.game_player.guess_letter(args[0])

        if not ok:
            self.out(text)

    def cmd_word(self, args):
        if len(args) != 1:
            self.out("Uso: /word <palavra>")
            return

        ok, text = self.client.game_player.guess_word(args[0])

        if not ok:
            self.out(text)

    def cmd_status(self, args):
        for line in self.client.status_lines():
            print(line)

    def cmd_log(self, args):
        if len(args) != 1 or args[0].upper() not in LOG_LEVELS:
            self.out("Uso: /log <DEBUG|INFO|WARNING|ERROR>")
            return

        level = getattr(logging, args[0].upper())

        for handler in logging.getLogger().handlers:
            if handler.get_name() == "console":
                handler.setLevel(level)

        self.out(f"Nível de log da tela: {args[0].upper()}")

    # ------------------------------------------------------------------
    # laço
    # ------------------------------------------------------------------
    def execute(self, command):
        parts = command.strip().split()

        if not parts:
            return True

        action = parts[0].lower()
        args = parts[1:]

        if action == "/quit":
            return False

        handlers = {
            "/help": lambda a: self.show_help(),
            "/peers": self.cmd_peers,
            "/conn": self.cmd_conn,
            "/rtt": self.cmd_rtt,
            "/reconnect": self.cmd_reconnect,
            "/games": self.cmd_games,
            "/create": self.cmd_create,
            "/start": self.cmd_start,
            "/cancel": self.cmd_cancel,
            "/join": self.cmd_join,
            "/leave": self.cmd_leave,
            "/guess": self.cmd_guess,
            "/word": self.cmd_word,
            "/status": self.cmd_status,
            "/log": self.cmd_log,
        }

        handler = handlers.get(action)

        if handler is None:
            self.out("Comando desconhecido. Digite /help.")
            return True

        try:
            handler(args)
        except Exception as error:
            logging.getLogger("CLI").exception("command failed")
            self.out(f"Erro ao executar {action}: {error}")

        return True

    def run(self):
        self.show_help()

        while True:
            try:
                command = input("forca> ")

                if not self.execute(command):
                    break

            except (KeyboardInterrupt, EOFError):
                break

        self.out("Encerrando...")
        self.client.stop()