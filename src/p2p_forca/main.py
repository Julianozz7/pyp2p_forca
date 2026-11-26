import argparse
import logging

from p2p_forca.cli import CLI
from p2p_forca.p2p_client import P2PClient

def configure_logging(config):
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    console.forca_console = True
    handlers = [console]
    if config["log_file"]:
        log_file = logging.FileHandler(config["log_file"], encoding="utf-8")
        log_file.setFormatter(formatter)
        log_file.setLevel(logging.DEBUG)
        handlers.append(log_file)
    logging.basicConfig(level=logging.DEBUG, handlers=handlers, force=True)


def main():
    parser = argparse.ArgumentParser(description="Jogo da Forca P2P")
    parser.add_argument("--config", default="config.json", help="Caminho do arquivo de configuração.")
    options = {
        str: ("rdv_host", "namespace", "name", "words_file", "log_file"),
        int: ("rdv_port", "listen_port", "register_ttl", "ping_miss_limit",
              "max_reconnect_attempts", "lives", "max_players", "turn_timeout"),
        float: ("discover_interval", "ping_interval", "base_backoff", "max_backoff"),
    }
    for cast, keys in options.items():
        for key in keys:
            parser.add_argument("--" + key.replace("_", "-"), type=cast,
                                default=argparse.SUPPRESS, help=f"Sobrescreve {key} do JSON.")

    overrides = vars(parser.parse_args())
    config_path = overrides.pop("config")
    try:
        client = P2PClient(config_path, overrides)
        configure_logging(client.config)
        if not client.start():
            print("[CLI] Não foi possível registrar o cliente no rendezvous.")
            return 1
        CLI(client).run()
        return 0
    except (OSError, ValueError) as error:
        print(f"[CLI] {error}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())