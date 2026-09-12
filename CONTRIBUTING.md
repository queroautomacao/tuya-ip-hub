# Contributing

[English](#english) | [Português](#português)

## English

### CLA

Every third-party contribution requires the Contributor License Agreement in [CLA.md](CLA.md): you keep the copyright and grant Quero Automação Ltda a license to use the contribution, including under other licenses, which is what keeps the commercial offer possible. A bot asks on the first pull request; sign by commenting exactly:

```
I have read the CLA Document and I hereby sign the CLA
```

### Adding a driver

A driver is a Python module in `core/iphub/drivers/nativos/` implementing the `Driver` contract of `core/iphub/drivers/base.py`, with a `Manifesto` (type, label, capabilities, registration fields, texts in pt and en). A pull request adds three things: the module, a test in `core/tests/drivers/` against a simulated device (`simulado.py` has TCP, HTTP and datagram servers), and a row in [docs/MATRIZ.md](docs/MATRIZ.md).

Rules for every driver: no code loaded at runtime; the identity is a UUID, MAC or serial, never the IP; `volume` is 0 to 100 and the driver converts the real scale; `estado()` returns the `Estado` dataclass; an action answers `None` or a stable code (`nao_suportado`, `eq_offline`, `invalid_value`, `auth_pendente`, `erro_aparelho`); every request and answer is written to the log through `drivers/fio.py`, with secrets redacted; a cloud driver uses a fixed host over HTTPS, follows no redirect and never returns the credential to the panel.

To request a device, open an issue with the device template. Never attach manufacturer manuals or PDFs.

### Tests and dependencies

`pytest` runs on every pull request, and a change without tests is not merged. No test requires hardware. Runtime dependencies may be MIT, BSD, ISC, Apache-2.0 or PSF (`scripts/licencas.sh` fails on anything else); GPL, AGPL and non-commercial code never enter the repository.

### Writing rules

- No em dash or en dash anywhere, commit messages included.
- Comments are few, in English, and explain why.
- SPDX header (`AGPL-3.0-only`, `Copyright (C) 2026 Quero Automação Ltda`) in the first lines of every source file; JSON and Markdown are exempt.
- The API answers `{"ok": bool, "code": str|null, ...}` with stable codes that the panel translates. Log lines in English.
- Panel texts and driver manifests exist in pt and en (tested).
- No real network address (use `192.0.2.x`), price, supplier, customer or person's name.
- Commits: `tipo(escopo): resumo`, with `tipo` one of `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `ci`, `build`.

### Running locally

```
python3.12 -m venv core/.venv && source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core && (cd core && pytest)
cd painel && npm install && npm test && npm run build
```

Before the pull request, `scripts/fumaca.sh` from the repository root (on Docker Desktop, prefix it with `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml`).

### Security

Do not report vulnerabilities in a public issue or pull request. Read [SECURITY.md](SECURITY.md).

## Português

### CLA

Toda contribuição de terceiro exige o Acordo de Licença de Contribuidor em [CLA.md](CLA.md): você mantém os direitos autorais e concede à Quero Automação Ltda uma licença para usar a contribuição, inclusive sob outras licenças, que é o que mantém a oferta comercial possível. Um bot pede no primeiro pull request; assine comentando exatamente:

```
I have read the CLA Document and I hereby sign the CLA
```

### Acrescentando um driver

Um driver é um módulo Python em `core/iphub/drivers/nativos/` que implementa o contrato `Driver` de `core/iphub/drivers/base.py`, com um `Manifesto` (tipo, rótulo, capacidades, campos do cadastro, textos em pt e en). Um pull request acrescenta três coisas: o módulo, um teste em `core/tests/drivers/` contra um aparelho simulado (o `simulado.py` tem servidores TCP, HTTP e de datagrama), e uma linha em [docs/MATRIZ.md](docs/MATRIZ.md).

Regras para todo driver: nenhum código carregado em runtime; a identidade é UUID, MAC ou serial, nunca o IP; `volume` é 0 a 100 e o driver converte a escala real; `estado()` devolve o dataclass `Estado`; uma ação responde `None` ou um código estável (`nao_suportado`, `eq_offline`, `invalid_value`, `auth_pendente`, `erro_aparelho`); toda requisição e resposta vai ao log por `drivers/fio.py`, com segredos redigidos; um driver de nuvem usa host fixo por HTTPS, não segue redirecionamento e nunca devolve a credencial ao painel.

Para pedir um aparelho, abra uma issue com o template de aparelho. Nunca anexe manuais nem PDFs de fabricante.

### Testes e dependências

O `pytest` roda em todo pull request, e uma mudança sem teste não é integrada. Nenhum teste exige hardware. Dependências de execução podem ser MIT, BSD, ISC, Apache-2.0 ou PSF (`scripts/licencas.sh` falha em qualquer outra); código GPL, AGPL ou não comercial nunca entra no repositório.

### Regras de escrita

- Nenhum travessão nem meia-risca em lugar nenhum, mensagens de commit inclusive.
- Comentários são poucos, em inglês, e explicam o porquê.
- Cabeçalho SPDX (`AGPL-3.0-only`, `Copyright (C) 2026 Quero Automação Ltda`) nas primeiras linhas de todo arquivo fonte; JSON e Markdown estão fora.
- A API responde `{"ok": bool, "code": str|null, ...}` com códigos estáveis que o painel traduz. Linhas de log em inglês.
- Textos do painel e manifestos de driver existem em pt e en (testado).
- Sem endereço de rede real (use `192.0.2.x`), preço, fornecedor, cliente nem nome de pessoa.
- Commits: `tipo(escopo): resumo`, com `tipo` entre `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `ci`, `build`.

### Rodando localmente

```
python3.12 -m venv core/.venv && source core/.venv/bin/activate
pip install -e "core[dev]"
ruff check core && ruff format --check core && (cd core && pytest)
cd painel && npm install && npm test && npm run build
```

Antes do pull request, `scripts/fumaca.sh` a partir da raiz do repositório (no Docker Desktop, prefixe com `COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml`).

### Segurança

Não reporte vulnerabilidade em issue ou pull request público. Leia o [SECURITY.md](SECURITY.md).
