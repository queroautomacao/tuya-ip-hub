# Tuya IP Hub

[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg)](LICENSE)
[![CI](https://github.com/queroautomacao/tuya-ip-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/queroautomacao/tuya-ip-hub/actions/workflows/ci.yml)

[English](#english) | [Português](#português)

## English

### What it is

The Tuya platform reaches Zigbee, Wi-Fi and Bluetooth devices through its own firmware. TVs, receivers, soundbars, multiroom speakers, projectors, HDMI matrices and relays only speak IP on the local network, and the platform cannot reach them by itself.

Tuya IP Hub is the bridge. It runs on a small Linux box with Docker (a Raspberry Pi, an ARM board or any Linux host), talks each device's protocol on the LAN and exposes everything as data points. A Tuya device acting as the bridge consumes those data points over a local WebSocket, the DP-bus, and from there the equipment appears in the Tuya app like any other device.

Between the Tuya device and the equipment everything stays on the local network: the DP-bus is a local WebSocket, and no server of Quero Automação sits in the control path.

```
Tuya app  -->  Tuya cloud  -->  Tuya device (bridge)
                                        |
                                        | DP-bus (local WebSocket)
                                        v
                                   Tuya IP Hub
                          (Docker, on the local network)
                                        |
        +--------------+----------------+---------------+--------------+
        |              |                |               |              |
        v              v                v               v              v
   TVs and        soundbars       multiroom        projectors       relays
   receivers                      speakers         and HDMI
                                                   matrices
```

When complete, the hub is three parts, and only three:

1. A daemon in Python (asyncio + aiohttp): talks to the devices, keeps the configuration, exposes a local REST API and the DP-bus WebSocket.
2. A web panel (React + Vite) for the integrator: first-run assistant, audio zones, equipment, drivers, scenes.
3. A single Docker image, published on GHCR, with the panel built inside.

### Project status

Milestone 1: the hub has an owner. What exists today is the daemon answering `GET /health` and the setup API, the panel with the first access assistant, sign in and sign out, the Docker image and the CI. No device is controlled yet: equipment, drivers and scenes arrive in the milestones below, one at a time, each one closed with a green CI.

| # | Milestone | Exit gate |
|---|---|---|
| 0 | Skeleton: structure, pytest, CI on PR, Dockerfile, compose, README that runs | `docker compose up` brings up `/health` |
| 1 | `config`, `auth`, `portao` (host gate), `api/setup`, minimal panel (ownership code, password, login, logout) | every security test green |
| 2 | Driver contract, catalog, generated discovery, simulated device, equipment panel | example driver against the simulator, discovery under test |
| 3 | Declarative engine (JSON), embedded catalog, editor in the panel | three example JSON drivers (TCP, HTTP, UDP) green against the simulator |
| 4 | LinkPlay as the multiroom driver, complete DP-bus, scenes | smoke test with a real speaker recorded in the device matrix |
| 5 | Native drivers, one at a time, each with its simulator: Denon, Onkyo, Yamaha, Samsung, LG webOS, Roku, Sony, Sonos, HEOS, Android TV | each one tested; explicit pairing where the device requires it |
| 6 | Release: tag, image on GHCR (arm64 and amd64), new-version notice in the panel, generated `API.md` | a stranger installs from the README without help |
| 7 | Public beta | open device matrix, issue and PR templates, CLA bot |

The device matrix lives in [docs/MATRIZ.md](docs/MATRIZ.md).

### What is public and what is commercial

- The code is open source under AGPL-3.0-only. The data point schema (the DP-bus contract) will be public and is enough for anyone to build their own bridge; the device side can be built with Tuya's own open source device framework, TuyaOpen.
- Product registration on the Tuya platform and per-installation licensing are the commercial service of Quero Automação and are not documented here (https://queroautomacao.com.br).

### Requirements

- Linux with Docker and Docker Compose v2. A Raspberry Pi or a similar ARM board is enough.
- For development: Docker Desktop (macOS or Windows), Python 3.12 and Node 22.18 or newer.

### Run

```
git clone https://github.com/queroautomacao/tuya-ip-hub.git
cd tuya-ip-hub
docker compose up -d --wait
```

Then open `http://<ip-of-the-server>:8080` (for example `http://192.0.2.10:8080`). The first `up` builds the image locally, which takes a few minutes on an ARM board; a published image on GHCR arrives with milestone 6. On an appliance without a bridge network or BuildKit, read the ARM section below before the first `up`.

On the first access the panel asks for the ownership code and for a new password. The code is generated on the first boot, printed in the container log (`docker compose logs iphub`) and kept in `codigo-de-posse.txt` inside the data volume, so only whoever reaches the machine sets the password. The password has a minimum of 8 characters. From then on the panel asks for the password alone, and the ownership code is no longer printed.

The panel is opened by IP address or as `localhost`. Any other hostname (a `.local` name or the name of a reverse proxy, for example) is answered with `421` and the code `host_nao_permitido`. To have such a name accepted, add it to the `hosts_permitidos` list in `config.json` inside the data volume. Three things about that file: it is written by the daemon, so it only exists once the first access has been completed, and a hand written one that the daemon does not recognise makes it refuse to boot; keep every key that is already there, `schema_version` included, and change only that list; and the configuration is read at boot, so there is no reload and the container has to be restarted for the new name to be accepted:

```
docker compose restart iphub
```

On Docker Desktop (macOS or Windows) host networking is not exposed, so add the desktop override and open `http://localhost:8080`:

```
docker compose -f docker-compose.yml -f docker-compose.desktop.yml up -d
```

The panel is plain HTTP on the LAN: there is no TLS in the beta. Do not expose port 8080 to the internet.

Configuration comes from environment variables, all optional. The table below describes the daemon process and the values baked into the image:

| Variable | Default | Meaning |
|---|---|---|
| `IPHUB_PORTA` | `8080` | port the daemon listens on |
| `IPHUB_BIND` | `0.0.0.0` | address the daemon binds to |
| `IPHUB_DATA` | `/data` | configuration and secrets (a named volume in compose) |
| `IPHUB_PAINEL` | `/app/painel` | built panel files |

The data directory (`IPHUB_DATA`, a named volume in compose) holds the configuration and the secrets. Every file below is created with mode `0600`:

| File | Content |
|---|---|
| `config.json` | configuration, schema version and the password hash |
| `codigo-de-posse.txt` | ownership code, generated on the first boot |
| `api-token.txt` | machine credential used by the DP-bus, never handed to the panel |
| `sessoes.json` | panel sessions, with the tokens kept hashed |

Erasing the volume erases the password with it: the hub goes back to the first access, with a new ownership code in the log.

The compose file passes no environment of its own, so `IPHUB_PORTA=9090 docker compose up` alone changes nothing. With compose, a value is changed in an override file that gives the service `iphub` an `environment:` block (compose merges `docker-compose.override.yml` on its own):

```
services:
  iphub:
    environment:
      IPHUB_PORTA: "9090"
```

On Docker Desktop, where the port is published instead of shared with the host, the same override also carries the matching entry under `ports:`, `"9090:9090"`, and it is passed with `-f` after `docker-compose.desktop.yml`.

### ARM boards without bridge network or BuildKit

Some ARM appliances run Docker without a bridge network (so `-p` does not work) and without BuildKit. The repository is built for them: `docker-compose.yml` uses `network_mode: host` and never publishes ports, and the Dockerfile has no BuildKit-only syntax. On such a board, build with the legacy builder and bring the container up without rebuilding:

```
DOCKER_BUILDKIT=0 docker build --network host -t ghcr.io/queroautomacao/tuya-ip-hub:latest .
docker compose up -d --no-build
```

Or deploy from your workstation over ssh. The script copies the repository with rsync, builds with the legacy builder on the remote box and waits for `/health`:

```
scripts/implantar.sh usuario@host
```

### Development

Daemon (Python 3.12):

```
python3.12 -m venv core/.venv
source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core
cd core && pytest
```

Start the daemon locally with `cd core && python -m iphub`. Without a built panel, `GET /` answers `503` with the code `painel_ausente` and `GET /health` keeps working; in development the panel is served by Vite instead.

Panel (Node 22.18 or newer):

```
cd painel
npm install
npm run dev
```

Vite proxies `/health` and `/api` to the daemon on port 8080, so start the daemon first. `npm run build` produces `painel/dist`, which is what the Docker image ships. `npm test` runs the panel's unit tests with the test runner built into Node.

Bench smoke test, from the repository root. It builds the image with compose, brings the container up, checks `/health`, the security headers, the Host rule, the panel and the non-root user, then tears everything down:

```
scripts/fumaca.sh
```

On Docker Desktop, add the override through `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml` before the script. Runtime dependency licenses are checked by `scripts/licencas.sh`, which the CI also runs.

### Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). A Contributor License Agreement ([CLA.md](CLA.md)) is required for every third-party contribution and is checked by a bot on the pull request.

### Security

Read [SECURITY.md](SECURITY.md). Report vulnerabilities privately through the GitHub Security tab, never in a public issue.

### License

AGPL-3.0-only (GNU Affero General Public License, version 3 only). A commercial license is available from Quero Automação Ltda for those who cannot or do not want to comply with the AGPL. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Trademarks are not licensed: the "Tuya IP Hub" name and "Quero Automação" stay with their owners, and a fork adopts its own name. Tuya is a trademark of Tuya Inc., used here nominatively to describe what the software connects to.

## Português

### O que é

A plataforma Tuya alcança dispositivos Zigbee, Wi-Fi e Bluetooth com o próprio firmware. TVs, receivers, soundbars, caixas de som multiroom, projetores, matrizes HDMI e relés só falam IP na rede local, e a plataforma não chega neles sozinha.

O Tuya IP Hub é a ponte. Roda num servidor Linux pequeno com Docker (um Raspberry Pi, uma placa ARM ou qualquer Linux), fala o protocolo de cada aparelho na LAN e expõe tudo como data points. Um dispositivo Tuya que faz a ponte consome esses data points por um WebSocket local, o DP-bus, e dali o equipamento aparece no app Tuya como qualquer outro dispositivo.

Entre o dispositivo Tuya e o equipamento, tudo fica na rede local: o DP-bus é um WebSocket local e nenhum servidor da Quero Automação está no caminho de controle.

```
App Tuya  -->  Nuvem Tuya  -->  Dispositivo Tuya (ponte)
                                          |
                                          | DP-bus (WebSocket local)
                                          v
                                     Tuya IP Hub
                                (Docker, na rede local)
                                          |
        +--------------+------------------+---------------+--------------+
        |              |                  |               |              |
        v              v                  v               v              v
   TVs e          soundbars         caixas de som    projetores e     relés
   receivers                        multiroom        matrizes HDMI
```

Quando completo, o hub são três peças, e só três:

1. Um daemon em Python (asyncio + aiohttp): fala com os aparelhos, guarda a configuração, expõe uma API REST local e o WebSocket do DP-bus.
2. Um painel web (React + Vite) para o integrador: assistente de primeiro uso, zonas de áudio, equipamentos, drivers, cenas.
3. Uma imagem Docker única, publicada no GHCR, com o painel construído dentro.

### Estado do projeto

Marco 1: o hub tem dono. O que existe hoje é o daemon respondendo `GET /health` e a API de configuração, o painel com o assistente de primeiro acesso, entrar e sair, a imagem Docker e o CI. Nenhum aparelho é controlado ainda: equipamentos, drivers e cenas chegam nos marcos abaixo, um por vez, cada um fechado com CI verde.

| # | Marco | Portão de saída |
|---|---|---|
| 0 | Esqueleto: estrutura, pytest, CI em PR, Dockerfile, compose, README que roda | `docker compose up` sobe um `/health` |
| 1 | `config`, `auth`, `portao` (portão de host), `api/setup`, painel mínimo (código de posse, senha, login, sair) | todos os testes de segurança verdes |
| 2 | Contrato de driver, catálogo, descoberta gerada, aparelho simulado, painel de equipamentos | driver de exemplo contra o simulado, descoberta com teste |
| 3 | Motor declarativo (JSON), catálogo embarcado, editor no painel | três JSON de exemplo (TCP, HTTP, UDP) verdes contra o simulado |
| 4 | LinkPlay como driver multiroom, DP-bus completo, cenas | fumaça com caixa real registrada na matriz de aparelhos |
| 5 | Drivers nativos, um por vez, cada um com simulado: Denon, Onkyo, Yamaha, Samsung, LG webOS, Roku, Sony, Sonos, HEOS, Android TV | cada um com teste; pareamento explícito onde o aparelho exige |
| 6 | Release: tag, imagem no GHCR (arm64 e amd64), aviso de versão nova no painel, `API.md` gerado | um estranho instala pelo README sem ajuda |
| 7 | Beta público | matriz de aparelhos aberta, templates de issue e PR, CLA no bot |

A matriz de aparelhos fica em [docs/MATRIZ.md](docs/MATRIZ.md).

### O que é público e o que é comercial

- O código é aberto sob AGPL-3.0-only. O esquema de data points (o contrato do DP-bus) será público e basta para quem quiser fazer a própria ponte; o lado do dispositivo pode ser construído com o framework de dispositivo de código aberto da própria Tuya, o TuyaOpen.
- O cadastro do produto na plataforma Tuya e o licenciamento por instalação são o serviço comercial da Quero Automação e não são documentados aqui (https://queroautomacao.com.br).

### Requisitos

- Linux com Docker e Docker Compose v2. Um Raspberry Pi ou uma placa ARM parecida basta.
- Para desenvolvimento: Docker Desktop (macOS ou Windows), Python 3.12 e Node 22.18 ou mais novo.

### Rodar

```
git clone https://github.com/queroautomacao/tuya-ip-hub.git
cd tuya-ip-hub
docker compose up -d --wait
```

Depois abra `http://<ip-do-servidor>:8080` (por exemplo `http://192.0.2.10:8080`). O primeiro `up` constrói a imagem localmente, o que leva alguns minutos numa placa ARM; a imagem publicada no GHCR chega com o marco 6. Num appliance sem rede bridge nem BuildKit, leia a seção sobre placas ARM abaixo antes do primeiro `up`.

No primeiro acesso o painel pede o código de posse e uma senha nova. O código é gerado no primeiro boot, impresso no log do container (`docker compose logs iphub`) e guardado em `codigo-de-posse.txt`, dentro do volume de dados, então só quem alcança a máquina define a senha. A senha tem mínimo de 8 caracteres. Dali em diante o painel pede só a senha, e o código de posse não é mais impresso.

O painel é aberto por endereço IP ou como `localhost`. Qualquer outro nome de host (um nome `.local` ou o nome de um proxy reverso, por exemplo) recebe `421` e o código `host_nao_permitido`. Para esse nome ser aceito, acrescente-o na lista `hosts_permitidos` do `config.json`, dentro do volume de dados. Três coisas sobre esse arquivo: ele é escrito pelo daemon, então só existe depois do primeiro acesso concluído, e um arquivo escrito à mão que o daemon não reconhece faz ele recusar o boot; mantenha todas as chaves que já estão lá, `schema_version` inclusive, e mude só essa lista; e a configuração é lida no boot, então não existe recarga e o container precisa ser reiniciado para o nome novo ser aceito:

```
docker compose restart iphub
```

No Docker Desktop (macOS ou Windows) a rede do host não é exposta, então acrescente o override de desktop e abra `http://localhost:8080`:

```
docker compose -f docker-compose.yml -f docker-compose.desktop.yml up -d
```

O painel é HTTP puro na LAN: não há TLS no beta. Não exponha a porta 8080 na internet.

A configuração vem de variáveis de ambiente, todas opcionais. A tabela abaixo descreve o processo do daemon e os valores embutidos na imagem:

| Variável | Padrão | Significado |
|---|---|---|
| `IPHUB_PORTA` | `8080` | porta em que o daemon escuta |
| `IPHUB_BIND` | `0.0.0.0` | endereço em que o daemon escuta |
| `IPHUB_DATA` | `/data` | configuração e segredos (volume nomeado no compose) |
| `IPHUB_PAINEL` | `/app/painel` | arquivos do painel construído |

O diretório de dados (`IPHUB_DATA`, um volume nomeado no compose) guarda a configuração e os segredos. Todo arquivo abaixo nasce com modo `0600`:

| Arquivo | Conteúdo |
|---|---|
| `config.json` | configuração, versão do esquema e o hash da senha |
| `codigo-de-posse.txt` | código de posse, gerado no primeiro boot |
| `api-token.txt` | credencial de máquina usada pelo DP-bus, nunca entregue ao painel |
| `sessoes.json` | sessões do painel, com os tokens guardados por hash |

Apagar o volume apaga a senha junto: o hub volta ao primeiro acesso, com um código de posse novo no log.

O arquivo do compose não passa ambiente nenhum, então `IPHUB_PORTA=9090 docker compose up` sozinho não muda nada. Com o compose, um valor é mudado num arquivo de override que dá ao serviço `iphub` um bloco `environment:` (o compose junta o `docker-compose.override.yml` sozinho):

```
services:
  iphub:
    environment:
      IPHUB_PORTA: "9090"
```

No Docker Desktop, onde a porta é publicada em vez de compartilhada com o host, o mesmo override leva também a entrada correspondente em `ports:`, `"9090:9090"`, e é passado com `-f` depois do `docker-compose.desktop.yml`.

### Placas ARM sem rede bridge nem BuildKit

Alguns appliances ARM rodam Docker sem rede bridge (então `-p` não funciona) e sem BuildKit. O repositório foi feito para eles: o `docker-compose.yml` usa `network_mode: host` e nunca publica porta, e o Dockerfile não tem sintaxe exclusiva do BuildKit. Nessa placa, construa com o builder legado e suba o container sem reconstruir:

```
DOCKER_BUILDKIT=0 docker build --network host -t ghcr.io/queroautomacao/tuya-ip-hub:latest .
docker compose up -d --no-build
```

Ou implante da sua estação por ssh. O script copia o repositório com rsync, constrói com o builder legado na máquina remota e espera o `/health`:

```
scripts/implantar.sh usuario@host
```

### Desenvolvimento

Daemon (Python 3.12):

```
python3.12 -m venv core/.venv
source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core
cd core && pytest
```

Suba o daemon localmente com `cd core && python -m iphub`. Sem painel construído, `GET /` responde `503` com o código `painel_ausente` e `GET /health` continua funcionando; em desenvolvimento o painel é servido pelo Vite.

Painel (Node 22.18 ou mais novo):

```
cd painel
npm install
npm run dev
```

O Vite encaminha `/health` e `/api` para o daemon na porta 8080, então suba o daemon antes. `npm run build` produz `painel/dist`, que é o que a imagem Docker carrega. `npm test` roda os testes unitários do painel com o executor de testes embutido no Node.

Fumaça de bancada, a partir da raiz do repositório. Constrói a imagem com o compose, sobe o container, confere o `/health`, os cabeçalhos de segurança, a regra de Host, o painel e o usuário não-root, e derruba tudo no fim:

```
scripts/fumaca.sh
```

No Docker Desktop, acrescente o override com `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml` antes do script. As licenças das dependências de execução são conferidas por `scripts/licencas.sh`, que o CI também roda.

### Contribuir

Leia [CONTRIBUTING.md](CONTRIBUTING.md). Um Acordo de Licença de Contribuidor ([CLA.md](CLA.md)) é obrigatório em toda contribuição de terceiro e é conferido por um bot no pull request.

### Segurança

Leia [SECURITY.md](SECURITY.md). Reporte vulnerabilidades em privado pela aba Security do GitHub, nunca em issue pública.

### Licença

AGPL-3.0-only (GNU Affero General Public License, versão 3 apenas). Uma licença comercial está disponível com a Quero Automação Ltda para quem não pode ou não quer cumprir a AGPL. Veja [LICENSE](LICENSE) e [NOTICE](NOTICE).

As marcas não são licenciadas: o nome "Tuya IP Hub" e "Quero Automação" ficam com seus donos, e um fork adota nome próprio. Tuya é marca da Tuya Inc., usada aqui de forma nominativa para descrever aquilo a que o software se conecta.
