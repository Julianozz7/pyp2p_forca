# PyP2P-Forca

Jogo cooperativo da forca para a disciplina de Redes de Computadores. O rendezvous anuncia os participantes. Cada partida usa as conexões TCP existentes entre o host e os jogadores. O host guarda a palavra, arbitra os turnos e publica o estado da partida.

## Executar

Use Python 3.10 ou superior. O projeto usa somente a biblioteca padrão. Execute os comandos a partir da pasta que contém `config.json`.

No PowerShell, cada terminal de jogador começa com:

```powershell
$env:PYTHONPATH = "src"
```

Em Linux ou macOS, use `export PYTHONPATH=src` e troque `py` por `python3` nos exemplos.

### Rendezvous público

Os padrões usam `pyp2p.mfcaetano.cc` na porta 8080. Escolham um namespace em comum e nomes próprios para o grupo.

```powershell
py -m p2p_forca.main --namespace GrupoForca --name alice --listen-port 8081 --log-file alice.log
```

Em redes com NAT ou firewall, a porta de escuta precisa estar acessível aos outros peers. Para uma demonstração no mesmo computador ou na rede local, use o servidor local.

### Rodar três peers no mesmo computador

No primeiro terminal, inicie o rendezvous fornecido com o trabalho.

```powershell
py src/rendezvous/main.py --host 127.0.0.1 --port 8080
```

Abra um terminal para cada jogador. Defina `PYTHONPATH` em cada terminal e execute uma destas linhas por terminal.

```powershell
py -m p2p_forca.main --rdv-host 127.0.0.1 --namespace DemoForca --name alice --listen-port 8081
py -m p2p_forca.main --rdv-host 127.0.0.1 --namespace DemoForca --name bob --listen-port 8082
py -m p2p_forca.main --rdv-host 127.0.0.1 --namespace DemoForca --name carol --listen-port 8083
```

1. Rode `/reconnect` para antecipar a descoberta e confirme duas conexões por terminal com `/conn`.
2. No terminal de Alice, digite `/create banana 2`. Use `/create --random 2` para sortear uma palavra.
3. Nos terminais de Bob e Carol, consulte `/games` e entre com `/join alice@DemoForca`, primeiro Bob e depois Carol.
4. Alice usa `/start`. Bob usa `/guess a`, Carol usa `/guess n` e Bob usa `/word banana`.
5. Use `/status` durante o jogo para ver a máscara, as vidas, as letras erradas, o jogador da vez e o tempo estimado. Use `/quit` para encerrar.

Todos os campos de `config.json` podem ser sobrescritos pelas opções de linha de comando. Veja `py -m p2p_forca.main --help` para as opções e `/help` para os comandos do jogo. Caminhos relativos de `words_file` são resolvidos junto ao arquivo escolhido em `--config`.

Uma partida encerrada aparece como `FINISHED` por 60 segundos para consultas do protocolo. Se todos os jogadores saírem, o host volta a `IDLE` imediatamente. Em caso de queda, o jogador volta a `IDLE` e tenta retornar à partida se a conexão com o mesmo host for restabelecida. Se o processo tiver sido reiniciado, use `/join` novamente. O hash é verificado quando o jogador recebeu o `GAME_START`. O fluxo de reentrada do protocolo envia o estado corrente após `JOIN_OK`, o que permite continuar mesmo em um processo recém-iniciado.

## Testes

```powershell
$env:PYTHONPATH = "src"
py -B -m unittest discover -s tests -v
```

A suíte inicia o rendezvous local em portas temporárias e usa sockets TCP para validar a malha, o lobby, os turnos, o resultado, as falhas de conexão e o encerramento. Também inclui um interlocutor de teste independente que envia e valida mensagens JSON nos papéis de host e jogador.

Para testar com os recursos de referência da disciplina, use o namespace `FORCA` no rendezvous público e procure `forca-bot` com `/games`. No papel de host, abra uma partida no mesmo namespace que a instância de referência `pyp2p-forca` e entre nela por esse cliente. Os executáveis de referência não estão neste pacote.

- [Especificação do jogo](docs/PyP2p_Forca%20-%20Especificacao%20Trabalho.md)
- [Protocolo do rendezvous no ZIP](docs/PyP2p%20-%20Rendesvouz.md)
- [Repositório de referência do rendezvous](https://github.com/mfcaetano/pyp2p-rdv)