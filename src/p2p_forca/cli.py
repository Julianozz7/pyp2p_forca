import logging, random
from pathlib import Path


class CLI:
    def __init__(self,client):self.client=client
    def show_help(self):
        print("""========== P2P FORCA ==========
/peers [*|#namespace]  Lista peers descobertos
/conn                  Conexões ativas
/rtt                   RTT médio por peer
/reconnect             Reconcilia com o Rendezvous agora
/games                 Lista partidas anunciadas pelos vizinhos
/create <palavra> [max_players]
/create --random [max_players]
/start                 Inicia a partida hospedada
/cancel                Cancela a partida hospedada
/join <host_peer_id>   Entra na partida do host
/leave                 Sai da partida atual
/guess <letra>         Envia uma letra
/word <palavra>        Envia um palpite de palavra
/status                Estado da partida
/log <DEBUG|INFO|WARNING|ERROR>
/help                  Ajuda
/quit                  Encerra ordenadamente
================================""")

    def execute(self,command):
        parts=command.strip().split()
        if not parts:return True
        action=parts[0].lower();args=parts[1:]
        try:
            if action=="/help":self.show_help()
            elif action=="/peers":
                ns=None
                if args and args[0].startswith("#"):ns=args[0][1:]
                elif args and args[0]=="*":ns="*"
                peers=self.client.discover_peers(ns if ns is not None else None)
                if peers is None:print("Falha no DISCOVER.")
                for p in peers or []:print(f'{p["name"]}@{p["namespace"]} {p["ip"]}:{p["port"]} TTL={p.get("expires_in","?")}s')
            elif action=="/conn":
                for p in self.client.peer_table.get_connected_peers():
                    rtt=p.get("rtt")
                    print(f'{p["peer_id"]} {p.get("direction")} {p.get("status")} RTT={rtt*1000:.1f} ms' if isinstance(rtt,(int,float)) else f'{p["peer_id"]} {p.get("direction")} {p.get("status")} RTT=—')
            elif action=="/rtt":
                for p in self.client.peer_table.get_all_peers():
                    rtt=p.get("rtt")
                    print(f'{p["peer_id"]}: {rtt*1000:.1f} ms' if isinstance(rtt,(int,float)) else f'{p["peer_id"]}: sem amostras')
            elif action=="/reconnect":
                self.client.discover_peers();print("Reconciliação solicitada.")
            elif action=="/games":
                games=self.client.list_games()
                if not games:print("Nenhuma partida encontrada.")
                for g in games:
                    print(f'{g["game_id"]} host={g["host"]} {len(g.get("players",[]))}/{g.get("max_players")} jogadores {g.get("word_length")} letras {g.get("lives")} vidas status={g.get("status")}')
            elif action=="/create":
                if not args:print("Uso: /create <palavra> [max_players] ou /create --random [max_players]")
                else:
                    if args[0]=="--random":
                        path=Path(self.client.config["words_file"])
                        if not path.is_absolute():path=self.client.config_path.parent/path
                        words=[w.strip() for w in path.read_text(encoding="utf8").splitlines() if w.strip()]
                        if not words:raise ValueError("words.txt não contém palavras.")
                        word=random.choice(words);rest=args[1:]
                    else:word=args[0];rest=args[1:]
                    maximum=int(rest[0]) if rest else self.client.config["max_players"]
                    if self.client.create_game(word,maximum):print(f"Partida criada: {self.client.host.game_id}")
                    else:print("Não foi possível criar a partida (já existe uma ativa?).")
            elif action=="/start":
                print("Partida iniciada." if self.client.start_game() else "Não foi possível iniciar: crie uma partida e aguarde ao menos um jogador.")
            elif action=="/cancel":print("Partida cancelada." if self.client.cancel_game() else "Não há partida para cancelar.")
            elif action=="/join":
                if not args:print("Uso: /join <host_peer_id>")
                else:
                    ok,reason=self.client.join_game(args[0])
                    print("Entrada na partida confirmada." if ok else f"Não foi possível entrar: {reason}")
            elif action=="/leave":print("Você saiu da partida." if self.client.leave_game() else "Você não está em uma partida.")
            elif action=="/guess":
                if len(args)!=1:print("Uso: /guess <letra>")
                else:print("Palpite enviado." if self.client.guess(letter=args[0]) else "Não foi possível enviar (não é sua vez ou não está jogando).")
            elif action=="/word":
                if len(args)!=1:print("Uso: /word <palavra>")
                else:print("Palpite enviado." if self.client.guess(word=args[0]) else "Não foi possível enviar (não é sua vez ou não está jogando).")
            elif action=="/status":print(self.client.status_text())
            elif action=="/log":
                if not args or args[0].upper() not in ("DEBUG","INFO","WARNING","ERROR"):print("Uso: /log DEBUG|INFO|WARNING|ERROR")
                else:
                    level=getattr(logging,args[0].upper())
                    root_logger=logging.getLogger()
                    root_logger.setLevel(logging.DEBUG)
                    for handler in root_logger.handlers:
                        if isinstance(handler,logging.StreamHandler) and not isinstance(handler,logging.FileHandler):
                            handler.setLevel(level)
                    print("Nível de log atualizado.")
            elif action=="/quit":return False
            else:print("Comando desconhecido. Digite /help.")
        except (ValueError,OSError) as exc:print(f"Erro: {exc}")
        return True

    def run(self):
        self.show_help()
        while True:
            try:
                if not self.execute(input("forca> ")):break
            except (KeyboardInterrupt,EOFError):break
        self.client.stop()
