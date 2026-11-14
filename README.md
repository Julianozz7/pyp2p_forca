```text
pyp2p_forca/
├── README.md
├── .gitignore
├── LICENSE
├── requirements.md
├── config.json                  # Nosso desenvolvimento
├── docs/
│   ├── PyP2p_Chat - Especificacao Trabalho.md
│   ├── PyP2p - Rendesvouz.md
│   └── PyP2p_Forca - Especificacao Trabalho.md
├── src/
│   ├── rendezvous/              # Implementado pelo Professor
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── peer_db.py
│   │   ├── protocol_parser.py
│   │   ├── rendezvous.py
│   │   └── request_handler.py
│   ├── tools/                   # Implementado pelo Professor
│   │   ├── rc_tester.py
│   │   ├── test_seq.json
│   │   ├── test_seq_extra.json
│   │   ├── test_seq_registers.json
│   │   └── test_seq_v2.json
│   └── p2p_forca/               # Nosso desenvolvimento
│       ├── __init__.py
│       ├── main.py
│       ├── p2p_client.py
│       ├── rendezvous_connection.py
│       ├── peer_connection.py
│       ├── peer_table.py
│       ├── keep_alive.py
│       ├── message_router.py
│       ├── game_host.py
│       ├── game_player.py
│       ├── hangman.py
│       └── cli.py
├── tests/                       # Testes unitários existentes no projeto. # Nosso desenvolvimento.
│   ├── test_hangman.py
│   ├── test_peer_table.py
│   └── test_protocol.py
└── .venv/                       # Ambiente Python local (ignorado pelo Git)
```
