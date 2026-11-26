# PyP2P-Forca

Jogo multiplayer de forca baseado em arquitetura Peer-to-Peer (P2P), desenvolvido para a disciplina de Redes de Computadores.

O sistema utiliza um servidor **Rendezvous** para registrar e descobrir peers. Após a descoberta, os participantes estabelecem conexões TCP diretamente entre si para trocar mensagens e participar das partidas.

O peer responsável pela criação da partida atua como host e mantém o estado autoritativo do jogo.

## 1. Requisitos

- Python 3.10 ou superior.
- Conexão de rede entre o Rendezvous e os peers.
- Portas TCP do Rendezvous e dos peers acessíveis.
- Arquivos do projeto, incluindo `config.json` e `words.txt`.

O projeto utiliza somente a biblioteca padrão do Python. Não existem dependências externas que precisem ser instaladas com `pip`.

Todos os comandos devem ser executados a partir da raiz do projeto, onde estão `config.json` e `words.txt`.

## 2. Configuração do ambiente

O projeto utiliza módulos Python localizados na pasta `src`. Por isso, é necessário informar ao Python onde encontrar esses módulos por meio da variável `PYTHONPATH`.

Existem duas maneiras de fazer isso.

**Opção 1 — Configurar uma vez por terminal**

Linux/macOS:

```bash
export PYTHONPATH=src
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
```

Depois de configurar, os comandos executados naquele terminal poderão importar os módulos do projeto. É necessário repetir a configuração em cada novo terminal.

**Opção 2 — Informar junto ao comando**

Linux/macOS:

```bash
PYTHONPATH=src python3 -m p2p_forca.main
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"; py -m p2p_forca.main
```

Nos exemplos seguintes, será utilizada a primeira opção para simplificar os comandos.

## 3. Iniciar o servidor Rendezvous

O servidor Rendezvous deve ser iniciado antes dos peers.

Na máquina responsável pelo servidor, abra um terminal na raiz do projeto e configure o `PYTHONPATH`.

Linux/macOS:

```bash
export PYTHONPATH=src
python3 src/rendezvous/main.py --host 0.0.0.0 --port 8080
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
py src/rendezvous/main.py --host 0.0.0.0 --port 8080
```

O servidor ficará em execução no terminal e aguardará as solicitações de registro e descoberta dos peers.

O endereço `0.0.0.0` permite que o servidor aceite conexões pelas interfaces de rede da máquina.

### Logs do Rendezvous

Por padrão, os registros são exibidos no próprio terminal. Caso seja necessário salvar os registros em arquivo:

```bash
python3 src/rendezvous/main.py --host 0.0.0.0 --port 8080 --log-mode both --log-file rendezvous.log
```

As opções disponíveis são:

- `console`: exibe os logs no terminal.
- `file`: salva os logs em arquivo.
- `both`: exibe no terminal e salva em arquivo.

O arquivo de log é opcional.

## 4. Iniciar os peers

Com o servidor Rendezvous em execução, cada participante deve iniciar seu próprio peer em um terminal separado.

Todos os participantes do grupo devem utilizar o mesmo namespace:

```text
GRUPO03
```

Cada peer deve possuir um nome e uma porta de escuta diferentes.

### Comando de inicialização

Linux/macOS:

```bash
export PYTHONPATH=src

python3 -m p2p_forca.main \
  --rdv-host IP_TAILSCALE \
  --rdv-port 8080 \
  --namespace GRUPO03 \
  --name aluno1 \
  --listen-port 8081
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"

py -m p2p_forca.main `
  --rdv-host IP_TAILSCALE `
  --rdv-port 8080 `
  --namespace GRUPO03 `
  --name aluno1 `
  --listen-port 8081
```

Substitua `IP_TAILSCALE`.

**Configuração dos parâmetros:**

| Parâmetro | Descrição |
|---|---|
| `--rdv-host` | IP Tailscale ou hostname do computador que executa o Rendezvous |
| `--rdv-port` | Porta TCP do servidor Rendezvous |
| `--namespace` | Namespace compartilhado pelos peers do grupo |
| `--name` | Identificação individual do peer |
| `--listen-port` | Porta TCP utilizada pelo peer para receber conexões |

**Importante:**

- O `--rdv-port` deve corresponder à porta em que o Rendezvous está executando. Neste exemplo, é `8080` para todos os peers.
- O `--listen-port` deve ser diferente para cada peer, pois representa a porta de escuta de cada participante.
- Todos os peers devem utilizar o mesmo `--rdv-host`, `--rdv-port` e `--namespace`.
- Cada participante deve utilizar um `--name` exclusivo.

Exemplo de configuração:

| Peer | Namespace | Nome | Porta de escuta |
|---|---|---|---:|
| Primeiro participante | GRUPO03 | terminal1 | 8081 |
| Segundo participante | GRUPO03 | terminal2 | 8082 |
| Terceiro participante | GRUPO03 | terminal3 | 8083 |

Para iniciar outro peer, basta executar o mesmo comando alterando o nome e a porta de escuta. A porta do Rendezvous permanece a mesma.

Os peers devem utilizar o IP Tailscale do computador onde o Rendezvous está executando como `--rdv-host`.

## 5. Logs dos peers

O salvamento de logs em arquivo é opcional. Por padrão, os registros são exibidos no terminal.

Para salvar os logs de um peer, acrescente `--log-file` ao comando de inicialização:

```bash
python3 -m p2p_forca.main \
  --rdv-host IP_TAILSCALE \
  --rdv-port 8080 \
  --namespace GRUPO03 \
  --name aluno1 \
  --listen-port 8081 \
  --log-file aluno1.log
```

Cada peer pode utilizar um arquivo diferente para facilitar o diagnóstico.

Os níveis de log disponíveis são:

| Nível | Descrição |
|---|---|
| `DEBUG` | Informações detalhadas para diagnóstico |
| `INFO` | Eventos normais da execução |
| `WARNING` | Situações inesperadas que não interrompem necessariamente o programa |
| `ERROR` | Erros que impedem alguma operação |

Durante a execução, o nível de log exibido no terminal pode ser alterado pelo comando:

```text
/log DEBUG
/log INFO
/log WARNING
/log ERROR
```

O nível padrão exibido no terminal é `INFO`. Quando o arquivo de log está habilitado, ele recebe registros em nível `DEBUG`, independentemente do nível selecionado para o terminal.

## 6. Comandos do jogo

Após iniciar o peer, a interface de terminal disponibiliza os seguintes comandos:

| Comando | Descrição |
|---|---|
| `/help` | Exibe a lista de comandos disponíveis |
| `/peers` | Lista os peers descobertos no namespace atual |
| `/peers *` | Consulta peers de todos os namespaces |
| `/peers #namespace` | Consulta peers de um namespace específico |
| `/conn` | Exibe as conexões ativas |
| `/rtt` | Exibe o RTT médio das conexões |
| `/reconnect` | Atualiza a descoberta de peers |
| `/games` | Lista as partidas disponíveis |
| `/create <palavra> [max_players]` | Cria uma partida com uma palavra definida |
| `/create --random [max_players]` | Cria uma partida com palavra aleatória |
| `/start` | Inicia a partida criada pelo host |
| `/join <host_peer_id>` | Entra em uma partida |
| `/guess <letra>` | Envia um palpite de letra |
| `/word <palavra>` | Tenta adivinhar a palavra completa |
| `/status` | Exibe o estado atual da partida |
| `/leave` | Sai da partida |
| `/cancel` | Cancela uma partida aberta pelo host |
| `/log <nível>` | Altera o nível de log exibido no terminal |
| `/quit` | Encerra o programa |


## 7. Testes automatizados

A suíte de testes automatizados utiliza o módulo `unittest` da biblioteca padrão do Python.

Os testes iniciam instâncias locais do Rendezvous em portas temporárias e utilizam sockets TCP e participantes de teste para verificar os diferentes componentes do sistema.

**Não é necessário iniciar manualmente o servidor Rendezvous nem os peers para executar a suíte de testes.**

Na raiz do projeto, execute:

Linux/macOS:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
py -B -m unittest discover -s tests -v
```

Os testes abrangem:

- Regras e validações do jogo da forca.
- Comunicação TCP e troca de mensagens.
- Registro e descoberta no Rendezvous.
- Gerenciamento de peers e conexões.
- PING, PONG e monitoramento de conexões.
- Roteamento e validação de mensagens.
- Criação, entrada e encerramento de partidas.
- Turnos, palpites, vitória e derrota.
- Reconexão e recuperação de partidas.

## 8. Configurações adicionais

As configurações padrão estão no arquivo `config.json`.

Todos os parâmetros podem ser sobrescritos por argumentos de linha de comando, sem necessidade de editar o arquivo.

Para consultar as opções disponíveis:

```bash
PYTHONPATH=src python3 -m p2p_forca.main --help
```

Parâmetros adicionais importantes:

| Parâmetro | Descrição |
|---|---|
| `--register-ttl` | Tempo de validade do registro, em segundos |
| `--discover-interval` | Intervalo entre consultas de descoberta |
| `--ping-interval` | Intervalo de envio de mensagens PING |
| `--lives` | Quantidade inicial de vidas |
| `--max-players` | Número máximo de jogadores |
| `--turn-timeout` | Tempo máximo de cada turno, em segundos |
| `--log-file` | Caminho do arquivo de log |

O TTL padrão de registro é de 7200 segundos (2 horas). O cliente renova o registro automaticamente antes de sua expiração.