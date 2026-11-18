# PyP2P Forca

Implementação do jogo da forca cooperativo sobre uma malha P2P TCP com servidor Rendezvous.

## Requisitos

- Python 3.10 ou superior
- Acesso de rede ao servidor Rendezvous configurado
- Porta TCP de escuta acessível pelos demais peers (encaminhamento/NAT pode ser necessário)

## Executar

Na raiz do repositório:

```bash
python -m pip install -r requirements.txt
PYTHONPATH=src python -m p2p_forca.main --config config.json
```

Ajuste `name`, `namespace` e `listen_port` no `config.json` para cada instância. Cada peer deve usar uma porta disponível.

## Comandos

`/peers`, `/peers *`, `/peers #namespace`, `/conn`, `/rtt`, `/reconnect`, `/games`,
`/create <palavra> [max_players]`, `/create --random [max_players]`, `/start`,
`/cancel`, `/join <host_peer_id>`, `/leave`, `/guess <letra>`, `/word <palavra>`,
`/status`, `/log <nível>`, `/help`, `/quit`.

O host não participa como jogador. É necessário ao menos um jogador para `/start`.

## Testes

```bash
python -m pip install pytest
PYTHONPATH=src python -m pytest -q
```

Os testes unitários cobrem regras puras da forca, estados de partida, conexões,
keep-alive, roteamento e cliente Rendezvous.

## Rendezvous local (opcional)

O diretório `src/rendezvous` contém a implementação do servidor fornecida no projeto anterior. Para executá-la localmente:

```bash
cd src/rendezvous
python main.py --host 0.0.0.0 --port 8080
```

Depois, configure `rdv_host` e `rdv_port` no `config.json` dos peers. Por padrão, o cliente utiliza o servidor público informado na especificação.

## Observações operacionais

- Cada comando Rendezvous usa uma conexão TCP independente.
- O peer mantém registro com refresh periódico do TTL e consulta peers no intervalo configurado.
- `HELLO/HELLO_OK` negocia a feature `hangman`; conexões duplicadas são desempatadas pelo `peer_id`.
- O servidor público não garante conectividade de entrada atrás de NAT/CGNAT. Para testes entre máquinas, libere/encaminhe a porta TCP de escuta ou use peers na mesma rede acessível.
