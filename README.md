# Tuya IP Hub

[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg)](LICENSE)
[![CI](https://github.com/queroautomacao/tuya-ip-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/queroautomacao/tuya-ip-hub/actions/workflows/ci.yml)

[English](#english) | [Português](#português)

## English

Tuya IP Hub connects equipment that only speaks IP on the local network (TVs, receivers, soundbars, multiroom speakers, projectors, air conditioners) to the Tuya platform. It runs as one Docker container: a Python daemon talks to each device, a web panel is where the integrator registers and controls them, and a local WebSocket (the DP-bus) hands everything to a Tuya device acting as the bridge. Nothing leaves the local network, except in the drivers that use a manufacturer cloud.

### Run

```
git clone https://github.com/queroautomacao/tuya-ip-hub.git
cd tuya-ip-hub
docker compose up -d --wait
```

Open `http://iphub.local:8080` (or `http://<ip-of-the-server>:8080`). The hub answers by `iphub.local` on mDNS, and by a name of its own, `iphub-xxxx.local`, for a network with more than one; both are printed in the log at boot and on the account screen. The first access asks for a password (12 characters or more), and whoever sets it owns the hub, so do it right after the first boot; that screen also restores a backup of another box. On Docker Desktop use `docker compose -f docker-compose.yml -f docker-compose.desktop.yml up -d` and open `http://localhost:8080`.

The panel is plain HTTP for the local network: do not expose port 8080 to the internet (the remote access is HTTPS up to the relay). A hostname other than an IP, `localhost` or the names of the hub is refused with `421`; add it to `hosts_permitidos` in `config.json` and restart the container.

### Configuration

Environment variables, all optional: `IPHUB_PORTA` (8080), `IPHUB_NOME` (`iphub`, the mDNS name), `IPHUB_MDNS` (`1`; `0` stops answering on mDNS), `IPHUB_BIND` (0.0.0.0), `IPHUB_DATA` (/data), `IPHUB_PAINEL` (/app/painel) and `IPHUB_REMOTO_RELAY`, the address of the relay of the remote access, which is the same on every hub of one operation and wins over whatever config.json holds. With compose, set them in a `.env` beside the compose file or in a `docker-compose.override.yml` under `services.iphub.environment`.

The data volume holds `config.json` (configuration and password hash), `api-token.txt` (DP-bus credential, never handed to the panel), `sessoes.json` (panel sessions, hashed) and the vault files, all created with mode `0600`. The key of every licence, the credential of every equipment, the secret of the authenticator and the DP-bus credential are encrypted at rest with a key derived from the serial of the board, so the card alone, in another machine, opens none of them; on a machine with no readable serial the key is a file beside the data, and the log says so. Erasing the volume erases the password.

### Backup, schedules and the volume ceiling

The account screen exports the whole installation (equipment with credentials, licences with keys, numbers, scenes, schedules, the owner password) as one `.iphub` file encrypted with a password of its own, and restores one; a box with no owner yet restores from its first screen, and the owner of the old box becomes the owner of the new one with the same password. The scenes screen schedules a scene at a time of day on the days of the week, in the time zone of the installation, run by the hub itself with no internet. Each equipment may carry a volume ceiling, applied to whatever asks: the panel, the app, a scene or the bus.

### Temporary remote access

The panel is a local network thing. When the integrator needs to reach it from outside, the owner opens a window of **24 hours**, from the panel or from the app of the customer, and it closes on its own. Three locks, and all three have to open:

1. an outbound socket to the relay of the maker (the service under `relay/`, run on a server of your own), open only inside the window. Nothing is forwarded on the router and nothing listens on the public address of the house; the relay answers with an address for that socket, and the hub publishes it to the app of the customer;
2. the code of the window, drawn when it is armed and shown in that same app, so whoever gets in from outside is whoever armed it there;
3. the login of the hub, with the password and the authenticator code.

The relay decides nothing about any customer: the address is the secret, and the window, the window code, the password and the second factor are all checked by the hub at the other end of the socket. What the relay does check is that the installation is one the maker registered: the hub tells the relay the home of the app it belongs to (written to the hub by the maker's miniApp, DP 184), and the relay asks the maker's own registry, a Directus, whether that home is there (`relay/LEIA-ME.md`). Until the miniApp has written the home the panel does not even show the door. So a hub is sold, plugged in and works, with no step at the factory and no ceiling on how many there are. Point a fleet at one relay with `IPHUB_REMOTO_RELAY`.

The claim of ownership and the DP-bus never answer through the relay, and nothing answers while the window is closed: outside it the address of the relay is not even in the allowlist of the Host check.

### Forgotten password

There is no recovery over the network. On the host that runs the container:

```
docker compose exec iphub python -m iphub.esquecer
docker compose restart iphub
```

The hub returns to the first access and keeps the equipment, their numbers and the scenes.

### ARM boards without bridge network or BuildKit

```
DOCKER_BUILDKIT=0 docker build --network host -t ghcr.io/queroautomacao/tuya-ip-hub:latest .
docker compose up -d --no-build
```

`scripts/implantar.sh usuario@host` does the same over ssh.

### Supported devices

The native drivers shipped in the image are listed in [docs/MATRIZ.md](docs/MATRIZ.md). A new driver is a Python module in `core/iphub/drivers/nativos/` with a test against a simulated device: see [CONTRIBUTING.md](CONTRIBUTING.md).

### Development

```
python3.12 -m venv core/.venv && source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core && (cd core && pytest)
cd painel && npm install && npm run dev
```

`scripts/fumaca.sh` builds the image and runs the bench smoke test (on Docker Desktop, prefix it with `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml`). `scripts/licencas.sh` checks the runtime dependency licenses.

### License

AGPL-3.0-only. A commercial license is available from Quero Automação Ltda; product registration on the Tuya platform and per-installation licensing are its commercial service (https://queroautomacao.com.br). See [LICENSE](LICENSE), [NOTICE](NOTICE) and [SECURITY.md](SECURITY.md). "Tuya IP Hub" and "Quero Automação" are not licensed; Tuya is a trademark of Tuya Inc., used nominatively.

## Português

O Tuya IP Hub conecta à plataforma Tuya os equipamentos que só falam IP na rede local (TVs, receivers, soundbars, caixas multiroom, projetores, ar condicionado). Roda como um container Docker: um daemon Python fala com cada aparelho, um painel web é onde o integrador cadastra e controla, e um WebSocket local (o DP-bus) entrega tudo a um dispositivo Tuya que faz a ponte. Nada sai da rede local, exceto nos drivers que usam a nuvem do fabricante.

### Rodar

```
git clone https://github.com/queroautomacao/tuya-ip-hub.git
cd tuya-ip-hub
docker compose up -d --wait
```

Abra `http://iphub.local:8080` (ou `http://<ip-do-servidor>:8080`). O hub atende por `iphub.local` via mDNS, e por um nome só dele, `iphub-xxxx.local`, para uma rede com mais de um; os dois aparecem no log ao subir e na tela da conta. O primeiro acesso pede uma senha (12 caracteres ou mais), e quem a define vira o dono do hub, então faça isso logo após o primeiro boot; essa tela também restaura o backup de outra caixa. No Docker Desktop use `docker compose -f docker-compose.yml -f docker-compose.desktop.yml up -d` e abra `http://localhost:8080`.

O painel é HTTP puro para a rede local: não exponha a porta 8080 na internet (o acesso remoto é HTTPS até o relay). Um nome de host que não seja IP, `localhost` ou os nomes do hub é recusado com `421`; acrescente-o em `hosts_permitidos` no `config.json` e reinicie o container.

### Configuração

Variáveis de ambiente, todas opcionais: `IPHUB_PORTA` (8080), `IPHUB_NOME` (`iphub`, o nome mDNS), `IPHUB_MDNS` (`1`; `0` para de responder no mDNS), `IPHUB_BIND` (0.0.0.0), `IPHUB_DATA` (/data), `IPHUB_PAINEL` (/app/painel) e `IPHUB_REMOTO_RELAY`, o endereço do relay do acesso remoto, que é o mesmo em todo hub de uma operação e vale acima do que o config.json guardar. Com o compose, defina-as num `.env` ao lado do compose ou num `docker-compose.override.yml` em `services.iphub.environment`.

O volume de dados guarda `config.json` (configuração e hash da senha), `api-token.txt` (credencial do DP-bus, nunca entregue ao painel), `sessoes.json` (sessões do painel, por hash) e os arquivos do cofre, todos criados com modo `0600`. A chave de cada licença, a credencial de cada equipamento, o segredo do autenticador e a credencial do DP-bus ficam cifradas em repouso com uma chave derivada do número de série da placa, então o cartão sozinho, em outra máquina, não abre nenhuma delas; numa máquina sem número de série legível a chave é um arquivo ao lado dos dados, e o log diz isso. Apagar o volume apaga a senha.

### Backup, agendamentos e teto de volume

A tela da conta exporta a instalação inteira (equipamentos com credenciais, licenças com chaves, números, cenas, agendamentos, a senha do dono) num arquivo `.iphub` cifrado com uma senha só dele, e restaura um; uma caixa ainda sem dono restaura pela primeira tela, e o dono da caixa antiga vira o dono da nova com a mesma senha. A tela de cenas agenda uma cena numa hora do dia nos dias da semana, no fuso da instalação, rodada pelo próprio hub sem internet. Cada equipamento pode ter um teto de volume, aplicado a quem pedir: painel, app, cena ou barramento.

### Acesso remoto temporário

O painel é coisa de rede local. Quando o integrador precisa alcançá-lo de fora, o dono abre uma janela de **24 horas**, pelo painel ou pelo aplicativo do cliente, e ela fecha sozinha. São três fechaduras, e as três precisam abrir:

1. um socket de saída para o relay do fabricante (o serviço em `relay/`, rodando num servidor seu), aberto só dentro da janela. Nada é encaminhado no roteador e nada escuta no endereço público da casa; o relay responde com um endereço para esse socket, e o hub publica esse endereço no app do cliente;
2. o código da janela, sorteado quando ela é armada e mostrado nesse mesmo app, então quem entra de fora é quem armou lá;
3. o login do hub, com a senha e o código do autenticador.

O relay não decide nada sobre cliente nenhum: o endereço é o segredo, e a janela, o código da janela, a senha e o segundo fator são todos conferidos pelo hub, do outro lado do socket. O que o relay confere é que a instalação é uma que o fabricante cadastrou: o hub diz ao relay o Home do aplicativo a que pertence (gravado no hub pelo miniApp do fabricante, DP 184), e o relay pergunta ao cadastro do próprio fabricante, um Directus, se aquele Home está lá (`relay/LEIA-ME.md`). Enquanto o miniApp não gravar o Home, o painel nem mostra a porta. Assim um hub é vendido, ligado e funciona, sem passo de fábrica e sem teto de quantidade. Aponte uma frota para um relay com `IPHUB_REMOTO_RELAY`.

A tomada de posse e o barramento DP nunca atendem pelo relay, e nada atende enquanto a janela estiver fechada: fora dela o endereço do relay nem sequer está na lista de hosts permitidos.

### Senha esquecida

Não há recuperação pela rede. No host que roda o container:

```
docker compose exec iphub python -m iphub.esquecer
docker compose restart iphub
```

O hub volta ao primeiro acesso e mantém os equipamentos, os números e as cenas.

### Placas ARM sem rede bridge nem BuildKit

```
DOCKER_BUILDKIT=0 docker build --network host -t ghcr.io/queroautomacao/tuya-ip-hub:latest .
docker compose up -d --no-build
```

`scripts/implantar.sh usuario@host` faz o mesmo por ssh.

### Aparelhos suportados

Os drivers nativos que embarcam na imagem estão em [docs/MATRIZ.md](docs/MATRIZ.md). Um driver novo é um módulo Python em `core/iphub/drivers/nativos/` com teste contra um aparelho simulado: veja o [CONTRIBUTING.md](CONTRIBUTING.md).

### Desenvolvimento

```
python3.12 -m venv core/.venv && source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core && (cd core && pytest)
cd painel && npm install && npm run dev
```

`scripts/fumaca.sh` constrói a imagem e roda a fumaça de bancada (no Docker Desktop, prefixe com `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml`). `scripts/licencas.sh` confere as licenças das dependências de execução.

### Licença

AGPL-3.0-only. Uma licença comercial está disponível com a Quero Automação Ltda; o cadastro do produto na plataforma Tuya e o licenciamento por instalação são o seu serviço comercial (https://queroautomacao.com.br). Veja [LICENSE](LICENSE), [NOTICE](NOTICE) e [SECURITY.md](SECURITY.md). "Tuya IP Hub" e "Quero Automação" não são licenciados; Tuya é marca da Tuya Inc., usada de forma nominativa.
