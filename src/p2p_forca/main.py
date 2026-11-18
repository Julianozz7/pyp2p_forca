import argparse, logging
from p2p_forca.p2p_client import P2PClient
from p2p_forca.cli import CLI


def configure_logging(config):
    root_logger=logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)
    formatter=logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    console=logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root_logger.addHandler(console)
    log_file=config.get("log_file")
    if log_file:
        file_handler=logging.FileHandler(log_file,encoding="utf8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def main():
    parser=argparse.ArgumentParser(description="Jogo da Forca P2P")
    parser.add_argument("--config",default="config.json")
    options={
        "rdv-host":str,"rdv-port":int,"namespace":str,"name":str,"listen-port":int,
        "register-ttl":int,"discover-interval":int,"ping-interval":int,"ping-miss-limit":int,
        "max-reconnect-attempts":int,"base-backoff":int,"max-backoff":int,
        "lives":int,"max-players":int,"turn-timeout":int,"words-file":str,"log-file":str
    }
    for opt,kind in options.items():
        parser.add_argument("--"+opt,type=kind)
    args=parser.parse_args()
    try:
        client=P2PClient(args.config)
        key_map={
            "rdv_host":"rdv_host","rdv_port":"rdv_port","namespace":"namespace","name":"name",
            "listen_port":"listen_port","register_ttl":"register_ttl","discover_interval":"discover_interval",
            "ping_interval":"ping_interval","ping_miss_limit":"ping_miss_limit",
            "max_reconnect_attempts":"max_reconnect_attempts","base_backoff":"base_backoff",
            "max_backoff":"max_backoff","lives":"lives","max_players":"max_players",
            "turn_timeout":"turn_timeout","words_file":"words_file","log_file":"log_file"
        }
        for config_key,arg_key in key_map.items():
            # argparse converts hyphens to underscores in attribute names.
            value=getattr(args,arg_key,None)
            if value is not None:client.config[config_key]=value
        client.name=client.config["name"];client.namespace=client.config["namespace"]
        client.peer_id=f"{client.name}@{client.namespace}"
        client.rendezvous.host=client.config["rdv_host"];client.rendezvous.port=int(client.config["rdv_port"])
        client.rendezvous.namespace=client.namespace;client.rendezvous.peer_id=client.peer_id
        client.rendezvous.name=client.name
        client.host.peer_id=client.peer_id
        if not 1 <= int(client.config["max_players"]) <= 4:
            raise ValueError("--max-players deve estar entre 1 e 4.")
        if int(client.config["lives"]) < 1 or int(client.config["turn_timeout"]) < 1:
            raise ValueError("lives e turn_timeout devem ser positivos.")
        client.host.lives=int(client.config["lives"])
        client.host.max_players=int(client.config["max_players"])
        client.host.turn_timeout=int(client.config["turn_timeout"])
        client.player.peer_id=client.peer_id
        client.router.local_peer_id=client.peer_id
        client.keep_alive.ping_interval=int(client.config["ping_interval"])
        client.keep_alive.miss_limit=int(client.config["ping_miss_limit"])
        client.listen_port=int(client.config["listen_port"])
        configure_logging(client.config)
        if not client.start():print("Não foi possível registrar no Rendezvous.");return 1
        print(f"Cliente iniciado: {client.peer_id} (IP público observado: {client.public_ip})")
        CLI(client).run()
    except Exception as exc:
        logging.exception("Erro ao iniciar o jogo.");print(f"Erro: {exc}");return 1
    return 0


if __name__=="__main__":raise SystemExit(main())
