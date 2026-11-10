```text
pyp2p_forca/
├── README.md
├── .gitignore
├── config.json                  # Nosso desenvolvimento
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
├── docs/                      
├── tests/                       # Opcional. Testes hangman.
└── .venv/                       # Ambiente Python. Opcional (nosso).
```
