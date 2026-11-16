class CLI:
    """Interface de comandos do jogo."""

    def __init__(self, client):
        self.client = client

    def show_help(self):
        print("""
========== P2P FORCA ==========
/help              Mostra os comandos
/peers             Lista os jogadores conhecidos
/conn              Lista conexões ativas
/games             Lista partidas disponíveis
/create PALAVRA    Cria uma partida
/join PEER_ID      Entra em uma partida
/start             Inicia a partida
/guess LETRA       Tenta uma letra
/word PALAVRA      Tenta adivinhar a palavra
/status            Mostra o estado da partida
/leave             Sai da partida
/quit              Encerra o programa
===============================
        """)

    def execute(self, command):
        parts = command.strip().split()

        if not parts:
            return True

        action = parts[0].lower()

        if action == "/help":
            self.show_help()

        elif action == "/peers":
            for peer in self.client.peer_table.get_all_peers():
                print(peer)

        elif action == "/conn":
            for peer in self.client.peer_table.get_connected_peers():
                print(peer["peer_id"])

        elif action == "/games":
            print("A descoberta de partidas será integrada à rede.")

        elif action == "/create":
            if len(parts) < 2:
                print("Uso: /create PALAVRA")
            else:
                print("Criação de partida:", parts[1])

        elif action == "/join":
            if len(parts) < 2:
                print("Uso: /join PEER_ID")
            else:
                print("Solicitando entrada em:", parts[1])

        elif action == "/start":
            print("Solicitação de início de partida.")

        elif action == "/guess":
            if len(parts) != 2:
                print("Uso: /guess LETRA")
            else:
                print("Tentativa:", parts[1])

        elif action == "/word":
            if len(parts) < 2:
                print("Uso: /word PALAVRA")
            else:
                print("Tentativa da palavra:", parts[1])

        elif action == "/status":
            print("Peer:", self.client.peer_id)
            print("Conexões:", self.client.peer_table.count())

        elif action == "/leave":
            print("Solicitação para sair da partida.")

        elif action == "/quit":
            return False

        else:
            print("Comando desconhecido. Digite /help.")

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

        self.client.stop()
