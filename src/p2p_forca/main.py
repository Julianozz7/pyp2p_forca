import argparse
import logging

from p2p_forca.p2p_client import P2PClient
from p2p_forca.cli import CLI


def configure_logging(config):
    """Configura o sistema de logs."""

    log_file = config.get("log_file")

    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    if not log_file:
        logging.getLogger().addHandler(logging.StreamHandler())


def main():
    parser = argparse.ArgumentParser(
        description="Jogo da Forca P2P"
    )

    parser.add_argument(
        "--config",
        default="config.json",
        help="Caminho do arquivo de configuração"
    )

    args = parser.parse_args()

    try:
        client = P2PClient(args.config)

        configure_logging(client.config)

        if not client.start():
            print("Não foi possível iniciar o cliente P2P.")
            return

        print(f"Cliente iniciado: {client.peer_id}")

        interface = CLI(client)
        interface.run()

    except Exception as error:
        logging.exception("Erro ao iniciar o jogo.")
        print(f"Erro: {error}")


if __name__ == "__main__":
    main()
