# Contributing to Tuya IP Hub

[English](#english) | [Português](#português)

## English

Thank you for helping. This document is short on purpose: the rules below are the ones the CI enforces, so reading them first saves a round trip on your pull request. The decisions behind them are in [CLAUDE.md](CLAUDE.md), which the code obeys.

### 1. The CLA comes first

Tuya IP Hub is licensed under AGPL-3.0-only, and Quero Automação Ltda also offers a commercial license for the same code. That dual licensing only works if the company holds the right to relicense every line in the repository. A single contribution merged without that right would make the commercial license impossible, and there is no way to undo it afterwards.

So every third-party contribution requires the Contributor License Agreement in [CLA.md](CLA.md). You keep the copyright of what you write; you grant the company a license to use it, including under other licenses. A bot checks the signature on every pull request. To sign, comment on your first pull request with exactly:

```
I have read the CLA Document and I hereby sign the CLA
```

Signing once covers all your future contributions under that version of the CLA.

### 2. Two ways to add a device

Everything the panel, the discovery, the scenes and the DP-bus know about a device comes from the driver's manifest (`Manifesto`). There are two driver engines, and only two:

- **Declarative driver (JSON)**: the main path for the community, and the one to try first. A JSON file describes the transport (a line of text on a TCP port, a simple HTTP request, or UDP), the commands, and how to read state back (a regex or a JSON path). It is data, not a program: no conditionals, no loops, no expressions, no arithmetic. The whole format is section 7 of [CLAUDE.md](CLAUDE.md), and the image carries one worked example of each transport in `core/iphub/drivers/catalogo_json/`: an HDMI matrix over TCP, a relay board over HTTP and an audio amplifier over UDP. The daemon validates the file when it is saved, answering an error code per field, and loads it without restarting the hub; a file that does not validate is refused and logged, and never costs the boot. It fits any device that takes a line of text on a TCP port or a simple HTTP request.
- **Native driver (Python)**: for protocols that need a library (pairing with a popup on the TV, multiroom grouping, and so on). A Python class implementing the `Driver` contract, with the same `Manifesto` as every other driver, compiled into the image. `core/iphub/drivers/nativos/pjlink.py` is the one that ships today.

A declarative driver lives in one of three places, and they are not the same thing:

- **In the panel**, in the drivers section: it opens a starting template per transport, validates what you typed and saves it. This is how an integrator adds a device to one installation.
- **By hand, in the data volume**: the same file dropped into the `drivers` directory beside `config.json`, loaded at the next boot of the container. A file here wins over an embedded driver that claims the same `tipo`.
- **In this repository**, as a pull request that adds the file to `core/iphub/drivers/catalogo_json/`: this is how a driver reaches everybody. Send the JSON file, a test against the simulated device in `core/tests/drivers/`, and a row in [docs/MATRIZ.md](docs/MATRIZ.md) naming the brand and the model it was run against. None of it needs Python.

Rules for both: no code is loaded at runtime (no plugin download, no `exec`, no embedded scripting language); the device identity is a UUID, MAC or serial, never the IP; `volume` is always 0 to 100 and the driver converts the real scale; `estado()` returns the typed `Estado` dataclass, never a loose dict; every text the panel shows comes from the manifest, in both pt and en.

To request support for a device, open an issue with the device template (brand, model, protocol facts). Do not attach manufacturer manuals or PDFs: they cannot be redistributed. Links only.

### 3. Every feature is born with a test

`pytest` runs in the CI on every pull request, and a pull request without tests for what it changes is not merged.

- Drivers are tested against a **simulated device** (a fake TCP or HTTP server, `simulado.py`), fed with recordings of real traffic whenever they exist. No test requires hardware.
- Validation on real hardware is a row in [docs/MATRIZ.md](docs/MATRIZ.md), not a merge gate.
- Test layers in `core/tests`: `puros` (config, auth, regex, manifests, generated discovery), `api` (aiohttp test client over every route, with driver doubles), `drivers` (each driver against the simulator), `seguranca` (each security item attacked by a test).
- Every fixed defect becomes a test in the cheapest layer that would have caught it.

### 4. Dependency licenses

- Runtime dependencies may be MIT, BSD, ISC or Apache-2.0, plus the Python Software Foundation License (the license of Python itself and of the aiohttp helpers aiohappyeyeballs and typing_extensions). `scripts/licencas.sh` runs pip-licenses in the CI and fails on anything outside that list.
- LGPL only when installed via pip, never vendored, and listed in [NOTICE](NOTICE).
- Third-party GPL or AGPL code never enters the repository while the commercial offer exists.
- Non-commercial or research-only clauses never.
- Do not add a dependency that is not needed: the runtime list is deliberately short.

### 5. Writing rules

The CI has a test for most of these, so a pull request that ignores them fails before review.

- **Never an em dash or an en dash**, anywhere: code, comments, docs, JSON, YAML, commit messages. Use a comma, period, colon, parentheses or a plain hyphen. Straight quotes only.
- **English first, then Brazilian Portuguese** in comments, docstrings and docs. The README, this file and the panel are bilingual with the same content in both languages; the pt/en parity of the panel texts and of the driver manifests is tested.
- **Comments explain why, never what**, and they are few. No history in comments ("this used to be...").
- **SPDX header** in the first three lines of every `.py`, `.ts`, `.tsx`, `.sh`, `.css`, `.yml`, `.yaml`, `.toml` and `.html` file, and of `Dockerfile`, `.gitignore`, `.dockerignore` and `.editorconfig`. JSON and Markdown are exempt, because JSON carries no comment and Markdown files are documents. The comment form follows the file type: `#` in Python, shell, YAML, TOML and the dotfiles; `//` in TypeScript; `/* */` in CSS; an HTML comment in `.html`. In the `#` form:

  ```
  # SPDX-License-Identifier: AGPL-3.0-only
  # Copyright (C) 2026 Quero Automação Ltda
  ```

- **The API returns codes, not phrases**: every response is `{"ok": bool, "code": str|null, ...}`, the code is stable (`nao_encontrado`, `host_nao_permitido`, `painel_ausente`, ...) and the panel translates it. Log lines are in English.
- **Identifiers follow the contract in CLAUDE.md**, in Portuguese (`versao`, `portao`, `saude`, `painel`, `Manifesto`, `Estado`). Do not rename them.
- **A module does one thing**, and there is no line limit. A big file is a problem when it does two things; then it is split.
- Do not cite other automation projects or products in code, comments or docs; describe what the code does. Naming the controlled brands (Denon, Samsung, LG, Sony, Onkyo, Yamaha, Roku, Sonos, LinkPlay, Tuya) is fine.
- No real network addresses (examples use `192.0.2.x`), no prices, suppliers, customers or people's names.

### 6. Commits

Format: `tipo(escopo): resumo`, with `tipo` one of `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `ci`, `build`. The scope is optional. The body says why. The CI checks every commit subject in the pull request against that format and rejects dashes (see above). Every commit must pass the CI on its own.

### 7. Running locally

Daemon (Python 3.12):

```
python3.12 -m venv core/.venv
source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core
cd core && pytest
```

Panel (Node 22.18 or newer), with the daemon running on port 8080 for the proxy:

```
cd painel
npm install
npm run dev
```

Before opening the pull request: `npm run build` in `painel/`, and `scripts/fumaca.sh` from the repository root (add `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml` on Docker Desktop). `scripts/licencas.sh` checks the runtime dependency licenses.

### 8. Pull request checklist

- Tests added or updated for what changed.
- No em dash or en dash anywhere.
- SPDX header on every new source file.
- pt/en parity in every text the panel shows and in the docs.
- CLA signed.
- What was tested: the simulated device, or real hardware (which brand and model).

### 9. Security

Do not report vulnerabilities in a public issue or pull request. Read [SECURITY.md](SECURITY.md).

## Português

Obrigado por ajudar. Este documento é curto de propósito: as regras abaixo são as que o CI impõe, então lê-las antes poupa uma volta no seu pull request. As decisões por trás delas estão em [CLAUDE.md](CLAUDE.md), que o código obedece.

### 1. O CLA vem primeiro

O Tuya IP Hub é licenciado sob AGPL-3.0-only, e a Quero Automação Ltda também oferece uma licença comercial para o mesmo código. Esse licenciamento duplo só funciona se a empresa tiver o direito de relicenciar cada linha do repositório. Uma única contribuição integrada sem esse direito tornaria a licença comercial impossível, e não há como desfazer isso depois.

Por isso toda contribuição de terceiro exige o Acordo de Licença de Contribuidor em [CLA.md](CLA.md). Você mantém os direitos autorais do que escreve; concede à empresa uma licença para usar, inclusive sob outras licenças. Um bot confere a assinatura em todo pull request. Para assinar, comente no seu primeiro pull request exatamente esta frase, em inglês:

```
I have read the CLA Document and I hereby sign the CLA
```

Assinar uma vez cobre todas as suas contribuições futuras sob aquela versão do CLA.

### 2. Dois caminhos para acrescentar um aparelho

Tudo que o painel, a descoberta, as cenas e o DP-bus sabem de um aparelho vem do manifesto do driver (`Manifesto`). Há dois motores de driver, e só dois:

- **Driver declarativo (JSON)**: o caminho principal da comunidade, e o primeiro a tentar. Um arquivo JSON descreve o transporte (uma linha de texto numa porta TCP, uma requisição HTTP simples ou UDP), os comandos e como ler o estado de volta (uma regex ou um caminho JSON). É dado, não programa: sem condicional, sem laço, sem expressão, sem aritmética. O formato inteiro é a seção 7 do [CLAUDE.md](CLAUDE.md), e a imagem carrega um exemplo pronto de cada transporte em `core/iphub/drivers/catalogo_json/`: uma matriz HDMI por TCP, uma placa de relés por HTTP e um amplificador de áudio por UDP. O daemon valida o arquivo ao salvar, respondendo um código de erro por campo, e o carrega sem reiniciar o hub; um arquivo que não valida é recusado e registrado, e nunca custa o boot. Cabe em qualquer aparelho que aceita uma linha de texto numa porta TCP ou uma requisição HTTP simples.
- **Driver nativo (Python)**: para protocolos que exigem biblioteca (pareamento com popup na TV, agrupamento multiroom, etc.). Uma classe Python que implementa o contrato `Driver`, com o mesmo `Manifesto` de todos os outros drivers, compilada na imagem. O `core/iphub/drivers/nativos/pjlink.py` é o que embarca hoje.

Um driver declarativo mora num de três lugares, e eles não são a mesma coisa:

- **No painel**, na seção de drivers: ele abre um modelo de partida por transporte, valida o que você digitou e salva. É assim que um integrador acrescenta um aparelho numa instalação.
- **À mão, no volume de dados**: o mesmo arquivo colocado na pasta `drivers`, ao lado do `config.json`, carregado no boot seguinte do container. Um arquivo daqui vence um driver embarcado que reivindica o mesmo `tipo`.
- **Neste repositório**, como um pull request que acrescenta o arquivo em `core/iphub/drivers/catalogo_json/`: é assim que um driver chega para todo mundo. Mande o arquivo JSON, um teste contra o aparelho simulado em `core/tests/drivers/`, e uma linha em [docs/MATRIZ.md](docs/MATRIZ.md) nomeando a marca e o modelo contra o qual ele rodou. Nada disso precisa de Python.

Regras para os dois: nenhum código carrega em runtime (sem download de plugin, sem `exec`, sem linguagem de script embutida); a identidade do aparelho é UUID, MAC ou serial, nunca o IP; `volume` é sempre 0 a 100 e o driver converte a escala real; `estado()` devolve o dataclass tipado `Estado`, nunca um dict solto; todo texto que o painel mostra vem do manifesto, em pt e en.

Para pedir suporte a um aparelho, abra uma issue com o template de aparelho (marca, modelo, fatos do protocolo). Não anexe manuais nem PDFs de fabricante: não podem ser redistribuídos. Só links.

### 3. Toda funcionalidade nasce com teste

O `pytest` roda no CI a cada pull request, e um pull request sem teste para o que muda não é integrado.

- Drivers são testados contra **aparelho simulado** (um servidor TCP ou HTTP falso, `simulado.py`), alimentado por gravações de tráfego real sempre que existirem. Nenhum teste exige hardware.
- Validação em hardware real é uma linha em [docs/MATRIZ.md](docs/MATRIZ.md), não portão de integração.
- Camadas de teste em `core/tests`: `puros` (config, auth, regex, manifestos, descoberta gerada), `api` (cliente de teste do aiohttp sobre toda rota, com dublês de driver), `drivers` (cada driver contra o simulado), `seguranca` (cada item de segurança atacado por um teste).
- Todo defeito corrigido vira teste na camada mais barata que o teria pegado.

### 4. Licenças de dependências

- Dependências de execução podem ser MIT, BSD, ISC ou Apache-2.0, mais a Python Software Foundation License (a licença do próprio Python e das auxiliares do aiohttp aiohappyeyeballs e typing_extensions). `scripts/licencas.sh` roda o pip-licenses no CI e falha em qualquer coisa fora dessa lista.
- LGPL só instalada via pip, nunca vendorizada, e listada no [NOTICE](NOTICE).
- Código GPL ou AGPL de terceiro nunca entra no repositório enquanto existir a oferta comercial.
- Cláusula não comercial ou só para pesquisa, nunca.
- Não acrescente dependência que não é necessária: a lista de execução é curta de propósito.

### 5. Regras de escrita

O CI tem teste para a maioria destas, então um pull request que as ignora falha antes da revisão.

- **Nunca travessão nem meia-risca**, em lugar nenhum: código, comentário, docs, JSON, YAML, mensagem de commit. Use vírgula, ponto, dois pontos, parênteses ou hífen simples. Só aspas retas.
- **Inglês primeiro, depois Português do Brasil** em comentários, docstrings e docs. O README, este arquivo e o painel são bilíngues com o mesmo conteúdo nas duas línguas; a paridade pt/en dos textos do painel e dos manifestos de driver é testada.
- **Comentário explica o porquê, nunca o quê**, e são poucos. Sem histórico em comentário ("antes era assim...").
- **Cabeçalho SPDX** nas três primeiras linhas de todo arquivo `.py`, `.ts`, `.tsx`, `.sh`, `.css`, `.yml`, `.yaml`, `.toml` e `.html`, e do `Dockerfile`, do `.gitignore`, do `.dockerignore` e do `.editorconfig`. JSON e Markdown estão fora, porque JSON não aceita comentário e arquivos Markdown são documentos. A forma do comentário segue o tipo do arquivo: `#` em Python, shell, YAML, TOML e nos dotfiles; `//` em TypeScript; `/* */` em CSS; comentário HTML em `.html`. Na forma `#`:

  ```
  # SPDX-License-Identifier: AGPL-3.0-only
  # Copyright (C) 2026 Quero Automação Ltda
  ```

- **A API devolve códigos, não frases**: toda resposta é `{"ok": bool, "code": str|null, ...}`, o código é estável (`nao_encontrado`, `host_nao_permitido`, `painel_ausente`, ...) e o painel traduz. Linhas de log são em inglês.
- **Identificadores seguem o contrato do CLAUDE.md**, em português (`versao`, `portao`, `saude`, `painel`, `Manifesto`, `Estado`). Não os renomeie.
- **Um módulo faz uma coisa só**, e não há limite de linhas. Um arquivo grande é problema quando faz duas coisas; aí ele é dividido.
- Não cite outros projetos ou produtos de automação em código, comentário ou doc; descreva o que o código faz. Citar as marcas controladas (Denon, Samsung, LG, Sony, Onkyo, Yamaha, Roku, Sonos, LinkPlay, Tuya) é permitido.
- Sem endereço de rede real (exemplos usam `192.0.2.x`), sem preço, fornecedor, cliente nem nome de pessoa.

### 6. Commits

Formato: `tipo(escopo): resumo`, com `tipo` entre `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `ci`, `build`. O escopo é opcional. O corpo diz por quê. O CI confere cada assunto de commit do pull request contra esse formato e rejeita travessões (veja acima). Todo commit precisa passar no CI sozinho.

### 7. Rodando localmente

Daemon (Python 3.12):

```
python3.12 -m venv core/.venv
source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core
cd core && pytest
```

Painel (Node 22.18 ou mais novo), com o daemon rodando na porta 8080 para o proxy:

```
cd painel
npm install
npm run dev
```

Antes de abrir o pull request: `npm run build` em `painel/`, e `scripts/fumaca.sh` a partir da raiz do repositório (acrescente `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml` no Docker Desktop). `scripts/licencas.sh` confere as licenças das dependências de execução.

### 8. Checklist do pull request

- Testes acrescentados ou atualizados para o que mudou.
- Nenhum travessão nem meia-risca em lugar nenhum.
- Cabeçalho SPDX em todo arquivo fonte novo.
- Paridade pt/en em todo texto que o painel mostra e nos docs.
- CLA assinado.
- O que foi testado: o aparelho simulado, ou hardware real (qual marca e modelo).

### 9. Segurança

Não reporte vulnerabilidade em issue ou pull request público. Leia [SECURITY.md](SECURITY.md).
