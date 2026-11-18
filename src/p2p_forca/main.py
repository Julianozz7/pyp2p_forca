"""Ponto de entrada: argumentos, config.json, logging e inicialização."""

import argparse
import logging
import sys

from p2p_forca.cli import CLI
from p2p_forca.p2p_client import P2PClient

FILE_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s"
CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"


def configure_logging(log_file=None, console_level="INFO"):
    """Tela: INFO (ajustável com /log). Arquivo: tudo, inclusive DEBUG."""

    root = logging.getLogger()

    for handler in list(root.handlers):
        root.removeHandler(handler)

    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.set_name("console")
    console.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console.setFormatter(
        logging.Formatter(CONSOLE_FORMAT, datefmt="%H:%M:%S")
    )
    root.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(
            log_file, mode="a", encoding="utf-8"
        )
        file_handler.set_name("file")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
        )
        root.addHandler(file_handler)


def build_parser():
    parser = argparse.ArgumentParser(description="Jogo da Forca P2P")

    parser.add_argument("--config", default="config.json",
                        help="Caminho do arquivo de configuração")
    parser.add_argument("--name", help="Nome do peer")
    parser.add_argument("--namespace", help="Namespace (sala)")
    parser.add_argument("--port", type=int, dest="listen_port",
                        help="Porta TCP de escuta")
    parser.add_argument("--rdv-host", dest="rdv_host",
                        help="Host do servidor Rendezvous")
    parser.add_argument("--rdv-port", type=int, dest="rdv_port",
                        help="Porta do servidor Rendezvous")
    parser.add_argument("--lives", type=int, help="Vidas da forca")
    parser.add_argument("--max-players", type=int, dest="max_players",
                        help="Máximo de jogadores por partida")
    parser.add_argument("--turn-timeout", type=int, dest="turn_timeout",
                        help="Segundos por turno")
    parser.add_argument("--words-file", dest="words_file",
                        help="Arquivo de palavras (para /create --random)")
    parser.add_argument("--log-file", dest="log_file",
                        help="Arquivo de log (inclui DEBUG)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Nível de log exibido na tela")

    return parser


def main():
    args = build_parser().parse_args()

    overrides = {
        key: getattr(args, key)
        for key in ("name", "namespace", "listen_port", "rdv_host",
                    "rdv_port", "lives", "max_players", "turn_timeout",
                    "words_file", "log_file")
    }

    client = None

    try:
        client = P2PClient(args.config, overrides=overrides)

        configure_logging(client.config.get("log_file"), args.log_level)

        if not client.start():
            print("Não foi possível iniciar o cliente P2P.")
            return 1

        print(f"Cliente iniciado: {client.peer_id}")

        CLI(client).run()

    except Exception as error:
        logging.exception("Erro ao iniciar o jogo.")
        print(f"Erro: {error}")

        if client is not None:
            client.stop()

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())