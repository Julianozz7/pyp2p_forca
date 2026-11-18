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
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )


def create_argument_parser():
    """Define os argumentos aceitos pela linha de comando."""

    parser = argparse.ArgumentParser(
        description="Cliente P2P - Jogo da Forca"
    )

    parser.add_argument("--config", default="config.json")

    # Identidade e rede
    parser.add_argument("--name")
    parser.add_argument("--namespace")
    parser.add_argument("--listen-port", type=int)
    parser.add_argument("--rdv-host")
    parser.add_argument("--rdv-port", type=int)

    # Rendezvous e descoberta
    parser.add_argument("--register-ttl", type=int)
    parser.add_argument("--discover-interval", type=int)

    # KeepAlive
    parser.add_argument("--ping-interval", type=int)
    parser.add_argument("--ping-miss-limit", type=int)

    # Reconexão
    parser.add_argument("--max-reconnect-attempts", type=int)
    parser.add_argument("--base-backoff", type=int)
    parser.add_argument("--max-backoff", type=int)

    # Jogo
    parser.add_argument("--lives", type=int)
    parser.add_argument("--max-players", type=int)
    parser.add_argument("--turn-timeout", type=int)
    parser.add_argument("--words-file")

    # Logs
    parser.add_argument("--log-file")

    return parser

def main():
    parser = create_argument_parser()
    args = parser.parse_args()

    # Apenas os argumentos informados serão sobrescritos.
    overrides = {
        key: value
        for key, value in vars(args).items()
        if key != "config" and value is not None
    }

    try:
        client = P2PClient(
            config_path=args.config,
            overrides=overrides
        )

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