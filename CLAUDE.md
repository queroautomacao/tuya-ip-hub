# CLAUDE.md - Tuya IP Hub

> Instruções para o Claude Code construir o Tuya IP Hub do zero.
> Leia inteiro antes de escrever a primeira linha. Este documento é a fonte
> das decisões; o código obedece a ele, não o contrário. Quando uma decisão
> mudar, mude aqui primeiro.

---

## 1. O que é

Um servidor pequeno (Raspberry Pi, placa ARM ou qualquer Linux com Docker)
que dá à plataforma Tuya o controle de equipamentos que só falam **IP**: caixas
de som multiroom, TVs, receivers, soundbars, projetores, matrizes HDMI, relés.
A Tuya não alcança nada disso sozinha; o hub é a ponte.

Três peças, e só três:

1. **Daemon** em Python (asyncio + aiohttp): fala com os aparelhos, guarda a
   configuração, expõe uma API REST local e um barramento WebSocket de
   *data points* (DP-bus) que a ponte Tuya consome.
2. **Painel** web (React + Vite) para o integrador instalar e manter: assistente
   de primeiro uso, zonas de áudio, equipamentos, drivers, cenas.
3. **Imagem Docker** única, publicada no GHCR, com o painel construído dentro.

Código aberto sob **AGPL-3.0-only**, com licença comercial oferecida em
paralelo pela Quero Automação Ltda. O cadastro do produto na plataforma Tuya e
o licenciamento por instalação são o serviço comercial e **não são
documentados aqui**; o esquema de DPs (§8) é público e basta para quem quiser
fazer a própria ponte.

---

## 2. Regras de simplicidade (não negociáveis)

Estas regras existem para o projeto não crescer em complexidade.

1. **Um contrato de driver.** Todo aparelho, inclusive as caixas de som, é um
   `Driver` com o mesmo `Manifesto` (§6). Não existe "segunda arquitetura".
2. **Dois motores, uma implementação de cada peça.** Driver **nativo** (Python,
   quando o protocolo exige biblioteca) e driver **declarativo** (JSON, sem
   programar, o caminho principal da comunidade). Descoberta, textos, ações e
   estado saem do manifesto; nunca de tabela paralela, nunca do painel.
3. **Composição em tempo de build.** Driver nativo entra na imagem. Driver JSON
   é dado, validado, e carrega sem reiniciar. **Nada carrega código em
   runtime**: sem linguagem de script embutida, sem plugin Python baixado, sem `exec`. Um driver
   malicioso não pode ser mais do que bytes no fio daquele aparelho.
4. **Sem código de compatibilidade** enquanto não houver instalação em campo.
   `schema_version` existe, migração não. Quebrou o formato? Apaga o `/data`.
5. **Toda funcionalidade nasce com teste.** `pytest` no CI a cada PR. Driver
   testa contra **aparelho simulado** (servidor TCP/HTTP falso), nunca exige
   hardware. Validação em hardware real é registro na matriz, não portão.
6. **Segurança no primeiro commit**, não "depois" (§9). A lista é curta e
   conhecida; deixar para depois só produz uma auditoria longa.
7. **Módulo pequeno, responsabilidade única.** A API é um pacote com um arquivo
   por área, não um arquivo. **Não há limite de linhas** (decisão de 3/set/2026,
   que removeu o teto de 400): o critério é a responsabilidade, não o tamanho, e
   um arquivo grande só é problema quando faz duas coisas.
8. **Um repositório.** O material interno da empresa vive em `interno/`
   (repositório git próprio, ignorado aqui). Nada de preço, fornecedor,
   cliente, IP de rede real ou estratégia entra neste repositório.

As seções 3 a 5 não fazem parte deste documento. A numeração das demais é
estável porque o código, os testes e as docs públicas citam a seção pelo número.

---

## 6. O contrato de driver

Tudo que o painel, a descoberta, as cenas e o DP-bus sabem de um aparelho vem
do **manifesto**. Se uma informação precisa existir em outro lugar, o
manifesto está incompleto: corrija o manifesto.

```python
@dataclass(frozen=True)
class Manifesto:
    tipo: str                 # "receiver_denon", estável, é chave de config
    rotulo: dict              # {"pt": "Receiver Denon / Marantz", "en": ...}
    categoria: str            # audio | multiroom | tv | receiver | soundbar |
                              # projetor | matriz | rele | outro
    capacidades: tuple        # subconjunto de CAPACIDADES (abaixo)
    auth: Auth                # NENHUMA | POPUP_NO_APARELHO | CODIGO | CHAVE
    descoberta: Descoberta    # ssdp_st, ssdp_fabricantes, mdns_servicos
    config_campos: tuple      # o que o cadastro pede além de ip (ex: porta)
    textos: dict              # {"pt": {...}, "en": {...}} tudo que o painel mostra
    motor: str                # "nativo" | "declarativo"

CAPACIDADES = ("ligar", "desligar", "volume", "mudo", "fonte",
               "tocar", "pausar", "proxima", "anterior",   # transporte
               "agrupar",                                   # multiroom
               "comando_extra")
```

```python
class Driver:
    MANIFESTO: Manifesto
    def __init__(self, cfg_equipamento: dict): ...
    async def iniciar(self): ...        # abre conexão; NÃO autentica
    async def parar(self): ...
    async def autenticar(self) -> str:  # "pareado" | "aguardando" | "falhou"
        # obrigatório implementar quando auth != NENHUMA; a base RECUSA o
        # padrão herdado nesse caso, em vez de fingir sucesso
    async def atualizar(self): ...      # um poll; a base chama a cada 10 s
    def estado(self) -> Estado: ...     # dataclass tipado (abaixo), nunca dict solto
    async def executar(self, acao: str, valor=None) -> str | None:
        # devolve None ou um CÓDIGO estável: nao_suportado, eq_offline,
        # invalid_value, auth_pendente, erro_aparelho
```

```python
@dataclass
class Estado:
    online: bool
    ligado: bool | None = None
    volume: int | None = None          # SEMPRE 0-100; o driver converte a escala real
    mudo: bool | None = None
    fonte: str | None = None
    fontes: tuple = ()
    reproduzindo: bool | None = None    # o transporte esta tocando (DP 102)
    tocando: str | None = None          # o titulo do que toca (DP 105)
    detalhe: str = ""
```

Regras que o gestor impõe (e testa) para todo driver:

- Ação fora de `capacidades` volta `nao_suportado` **antes** de chegar ao
  driver. O driver nunca implementa método só para recusar.
- **`reproduzindo` e `tocando` são fatos diferentes** (decisão de 3/set/2026): o
  DP 102 é o transporte, o DP 105 é o título. Ler um do outro fazia uma caixa
  tocando por bluetooth, por entrada de linha, ou um rádio sem metadado, reportar
  **pausada**, e o app mandava play no que já tocava. Driver que não sabe dizer
  deixa `reproduzindo` em `None`.
- `estado()` é o dataclass acima. Chave nova no painel = campo novo aqui, com
  teste. Nunca "o driver X publica `modo` e o Y publica `modo_clima`".
- Identidade de aparelho é UUID, MAC ou serial; **IP nunca é chave**. O IP é
  re-resolvido por descoberta.
- Toda mensagem que o painel mostra sobre o driver vem de `textos`, nos dois
  idiomas. O teste de paridade pt/en cobre os manifestos.
- `CATALOGO` nasce de `pkgutil.iter_modules` sobre `drivers/nativos` mais os
  JSON do catálogo embarcado e de `/data/drivers`. Ninguém edita lista à mão.
- A descoberta é **gerada** dos manifestos. Dois manifestos que reivindicam a
  mesma assinatura SSDP é erro de teste, não decisão em runtime.

**As caixas LinkPlay são um driver** (`nativos/linkplay.py`, categoria
`multiroom`, capacidades de transporte e `agrupar`). O conceito de "zona" é
apenas: equipamento multiroom que ocupa um dos seis blocos de DP (§8). O hub
funciona com **zero** equipamentos cadastrados; nenhum assistente exige caixa
para seguir.

---

## 7. O motor declarativo (JSON)

É o caminho da comunidade. Cobre o aparelho que fala **uma linha de texto numa
porta TCP** ou **HTTP simples**, e devolve estado legível por regex ou por
caminho JSON. Onde não cabe, escreve-se driver nativo.

```json
{
  "manifesto": {"tipo": "matriz_exemplo", "rotulo": {"pt": "...", "en": "..."},
                "categoria": "matriz", "capacidades": ["ligar","desligar","fonte"]},
  "transporte": {"tcp": {"porta": 23, "terminador": "\r", "timeout_s": 3,
                         "intervalo_min_ms": 200}},
  "comandos": {"ligar": {"envia": "PWR ON"},
               "fonte": {"envia": "SRC {valor}", "valores": {"HDMI1": "1", "HDMI2": "2"}},
               "comando_extra": {"envia": "{valor}"}},
  "estado": {"pede": "STATUS?",
             "le": {"ligado": {"regex": "PWR (ON|OFF)", "verdadeiro": "ON"},
                    "fonte":  {"regex": "SRC (\\d)"}}},
  "descoberta": {"ssdp_fabricantes": ["exemplo"]}
}
```

O formato é **dado, não programa**: sem condicional, sem laço, sem expressão,
sem aritmética. É a linha que separa dado de programa. O que ele
tem, desde o dia um: HTTP com corpo e cabeçalhos (valor de cabeçalho vem do
cadastro, nunca do arquivo, para o JSON nunca carregar segredo), estado em
mais de uma requisição, repetição declarada (`"repete": 3`, para volume
relativo e ponte de infravermelho), sequência com intervalo, tolerância a
saudação inicial (PJLink), UDP e literal hexadecimal.

Validação na hora de salvar, com código de erro por campo: `base` HTTP só
`http(s)://{ip}`, porta 1-65535, caracteres de controle removidos de texto,
capacidades dentro do vocabulário, **e toda regex passa pela prova de fogo**
(`regex_seguro.perigosa`, que roda o padrão contra `"a"*40+"!"` num processo
com prazo). Em runtime, toda regex de leitura roda em `regex_seguro` com prazo
de 250 ms: `re.search` não solta a GIL e uma regex catastrófica congela o
daemon inteiro.

Carregamento: `drivers/catalogo_json/*.json` (embarcado, versionado, revisado)
mais `/data/drivers/*.json` (do integrador), o segundo vence em conflito de
`tipo`. Recarrega quando o painel salva, sem reiniciar.

---

## 8. Contrato de data points (DP-bus)

É o que a ponte Tuya consome, e é público. Regras herdadas da plataforma e
verificadas: o chip Tuya **nunca ecoa** um DP recebido (report só nasce de
estado real), enum customizado não é reportado, rótulo de cena vem da
plataforma, string DP até 255 bytes, enum até 10 valores.

| DP | Tipo | Sentido | Uso |
|---|---|---|---|
| 101 + 5·(n-1) | value 0-100 | R/W | volume da zona n (1..6) |
| 102 + 5·(n-1) | bool | R/W | play/pause |
| 103 + 5·(n-1) | enum cmd1..cmd8 | só envio | preset |
| 104 + 5·(n-1) | bool | report | online |
| 105 + 5·(n-1) | string | report, throttle 5 s | tocando agora |
| 131 | enum cena1..cena8 | só envio | cena |
| 132 | enum solo/grupo1..N | R/W | grupo ativo |
| 133, 134, 135 | string JSON ≤ 255 B | report | nomes de zonas, cenas, grupos |
| 141..146 | enum | R/W | entrada da zona n |

WebSocket `/dpbus`: o **primeiro frame** é `{"t":"auth","token":"<api_token>"}`
(nunca na URL; sem ele em 5 s, fecha com 4401). Depois: `{"t":"set","id":..,
"dpid":..,"v":..}` do cliente, `{"t":"ack",...}`, `{"t":"report",...}` e
`{"t":"snapshot",...}` do servidor. Comando reporta otimista e relê em ~1,5 s;
comando novo para o mesmo DP cancela a verificação pendente.

---

## 9. Segurança (primeiro commit, não "depois")

O container é exposto na LAN do cliente e fala com aparelhos de terceiros.
Tudo abaixo entra com teste na pasta `tests/seguranca` **antes** de qualquer
driver existir:

- **Primeiro acesso sem código** (decisão de 3/set/2026, substitui o código de
  posse): enquanto não existir senha, `POST /api/posse` é público e define a senha
  do dono. A consequência é aceita e precisa estar escrita onde o usuário lê: num
  hub ligado e ainda não configurado, quem alcançar o painel primeiro vira dono,
  então a instalação é configurada logo no primeiro boot. O README e o SECURITY.md
  dizem isso com todas as letras, ao lado do aviso de que não há TLS. A rota é
  serializada: duas posses simultâneas não produzem dois donos, a segunda recebe
  `ja_configurado`.
- **Senha** mínimo 8, PBKDF2-HMAC-SHA256 200 mil iterações, salt por instalação.
- **Sessão** do painel: token aleatório, guardado por **hash** em
  `/data/sessoes.json` (0600), validade 24 h renovada a cada uso, teto 30
  dias, `POST /api/sair` revoga. Trocar a senha revoga todas.
- **`api_token`**: credencial de máquina (DP-bus, ferramentas). Aleatório no
  primeiro boot, 0600, **nunca entregue ao painel**, rotacionado na troca de
  senha. Valor de exemplo no repositório é recusado no boot.
- **Limite de tentativas** de login pelo IP real (`X-Forwarded-For` só de
  proxy declarado na config): 5 falhas = 15 min, mais teto global de 60 por
  minuto (cada tentativa custa um PBKDF2 numa placa ARM).
- **Host**: só IP literal, `localhost` ou nome em `hosts_permitidos`; senão
  421. É o que fecha DNS rebinding sem o atacante estar na LAN.
- **Origin**: em `/api/*` e `/dpbus`, presente e diferente do próprio host =
  403. Fecha CSRF.
- **Cabeçalhos** em toda resposta: `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Content-Security-Policy: frame-ancestors 'none'`.
- Arquivos em `/data` que carregam segredo nascem **0600** (`os.open` com modo,
  escrita atômica). Credenciais de aparelho ficam no `config.json`.
- Sem TLS local no beta: o README diz isso ao lado da URL do painel.
- Container roda como **usuário não-root**; sem `docker.sock` montado.
- O campo `ip` de qualquer rota que fala com aparelho é validado como IP
  literal (sem nome, sem URL), para o hub não virar proxy da LAN.
- Toda resposta que a aplicação produz passa pelo portão, inclusive 500 (código
  `erro_interno`, sem traceback no corpo) e 417. A única exceção é o 400 que o
  parser HTTP do aiohttp emite para requisição malformada, antes de qualquer
  rota; ele fica fora destas garantias e está registrado aqui de propósito.

---

## 10. Licença e contribuição

- **AGPL-3.0-only** (versão 3, sem "ou posterior"). Cabeçalho SPDX em todo
  arquivo fonte desde o primeiro: `# SPDX-License-Identifier: AGPL-3.0-only`
  e `# Copyright (C) 2026 Quero Automação Ltda`.
- **CLA obrigatório** em toda contribuição de terceiro, verificado por bot no
  PR. Uma contribuição sem CLA destrói a licença comercial de forma
  irreversível.
- **Dependência**: a regra vale para o que a imagem ENTREGA (dependências
  Python de execução e pacotes embutidos no bundle do painel); ferramenta de
  build que não viaja na imagem fica fora dela. MIT, BSD, ISC, Apache-2.0 e PSF
  (a licença do próprio Python e de auxiliares do aiohttp) entram. LGPL só
  instalada via pip, nunca vendorizada, listada no NOTICE. **GPL e AGPL de
  terceiro não entram** enquanto existir a oferta comercial. Cláusula não
  comercial ou de pesquisa nunca. O CI roda `pip-licenses` e falha fora dessa
  lista. Pacote embutido no painel entra no aviso de licença que a imagem
  carrega, gerado no build.
- **Marca**: "Tuya IP Hub" e "Quero Automação" não são licenciadas. Fork adota
  nome próprio. "Tuya" é marca da Tuya Inc., citada de forma nominativa.
- Nomes de fabricante dos aparelhos controlados (Denon, Samsung, LG, Sony,
  Onkyo, Yamaha, Roku, Sonos, LinkPlay, Tuya) são uso nominativo e ficam.
  **Não cite** outros projetos ou produtos de automação em código, comentário
  ou documento; descreva o que o código faz.
- Documento de fabricante (PDF, manual) nunca entra no repositório.
  `*.pdf` no `.gitignore`.

---

## 11. Regras de escrita

- Ingles em primeiro lugar com tradução para o Português do Brasil em código, comentário, commit e docs internos; o
  painel e o README são bilíngues (pt e en) com paridade testada.
- **Nunca travessão** (nem em-dash nem en-dash), em lugar nenhum. Vírgula,
  ponto, dois pontos, parênteses ou hífen. Aspas retas.
- Comentário explica o **porquê**, nunca o quê. Sem "histórico" em comentário
  ("antes era assim"): o repositório nasce limpo.
- Commit: `tipo(escopo): resumo` (feat, fix, refactor, docs, chore, test, ci,
  build), escopo opcional, corpo dizendo por quê. Todo commit passa no CI.
- API devolve `{"ok": bool, "code": str|null, ...}`; o código de erro é
  estável e o painel traduz. A API nunca responde frase humana.

---

## 12. Testes

- `cd core && pytest`. Camadas em pastas: `puros` (config, auth, regex,
  manifesto, descoberta gerada), `api` (cliente de teste do aiohttp sobre toda
  rota, com dublês de driver), `drivers` (cada driver contra `simulado.py`,
  alimentado por gravações de tráfego real quando houver), `seguranca` (cada
  item do §9 é um teste que ataca).
- Regra: **todo defeito corrigido vira teste** na camada mais barata que o
  pegaria.
- Matriz pública de aparelhos com três estados honestos: verificado em
  hardware (alguém rodou e assinou), testado em simulado, declarado.
- Fumaça de bancada (`scripts/fumaca.sh`): sobe do zero, senha, painel, descobre,
  cadastra, comanda, cena. Roda antes de toda release.

---

## 13. Ordem de construção

Cada marco termina com CI verde e um commit. Não pule.

| # | Marco | Portão de saída |
|---|---|---|
| 0 | Esqueleto: estrutura, `pytest`, CI em PR, Dockerfile, `compose`, README que roda | `docker compose up` sobe um `/health` |
| 1 | `config` + `auth` + `portao` + `api/setup` + painel mínimo (posse, senha, login, sair) | todos os testes de §9 verdes |
| 2 | Contrato de driver (§6) + `catalogo` + `descoberta` gerada + `simulado` + painel de equipamentos | driver de exemplo contra simulado, descoberta de teste |
| 3 | Motor declarativo (§7) + catálogo embarcado + editor no painel | 3 JSON de exemplo (TCP, HTTP, UDP) verdes contra simulado |
| 4 | LinkPlay como driver multiroom + DP-bus completo (§8) + cenas | fumaça com caixa real registrada na matriz |
| 5 | Drivers nativos, um por vez, cada um com simulado: Denon, Onkyo, Yamaha, Samsung, LG webOS, Roku, Sony, Sonos, HEOS, Android TV | cada um com teste; pareamento explícito nos que exigem |
| 6 | Release: tag, imagem no GHCR (arm64 + amd64), aviso de versão nova no painel, `API.md` gerado | um estranho instala pelo README sem ajuda |
| 7 | Beta público | matriz de aparelhos aberta, templates de issue e PR, CLA no bot |

---

## 14. Fatos validados em bancada (não redescobrir)

Custaram dias. Estão aqui para o driver LinkPlay e o DP-bus nascerem certos.

**LinkPlay (AudioCast, iEAST) por HTTP `httpapi.asp?command=`**
- Identidade: campo `uuid` de `getStatusEx`. mDNS `_linkplay._tcp` re-resolve
  o IP. Poll `getPlayerStatus` a cada 5 s escalonado; 2 falhas = offline.
- Comandos: `setPlayerCmd:vol:N`, `setPlayerCmd:play:<url>`, `setPlayerCmd:pause`,
  `setPlayerCmd:switchmode:<wifi|bluetooth|line-in|usb>` (só os que
  `plm_support` lista). Preset = tocar URL configurada.
- Multiroom nativo: `ConnectMasterAp:JoinGroupMaster:...` no escravo,
  `multiroom:Ungroup` e afins no mestre. **Play em escravo desmonta o grupo**:
  transporte vai sempre para o mestre. Volume de escravo via `SlaveVolume` no
  mestre. **Escravo reporta `stop` mesmo tocando**: espelhe o estado do
  mestre nos escravos. Grupo só entre caixas do mesmo domínio (LinkPlay com
  LinkPlay); nunca oferecer grupo misto.
- Tocar URL de áudio local (o hub serve por HTTP sem auth em `/audio/`, quem
  busca é a caixa): útil para o teste de som do assistente (um bipe gerado
  com a biblioteca padrão, não uma voz).
- iEAST TCP 8899 (avançado): mínimo **200 ms entre comandos**; comandos de
  mudo e preset de hardware; tocar URL e agrupar só existem na API HTTP.
- Reboot da caixa: some em ~30 s, volta pela identidade em ~50 s sem tocar em IP.

**DP-bus**: report otimista + releitura em 1,5 s funcionou com ack em ~30 ms.
Nomes de zona, cena e grupo em JSON compacto cabem em 255 bytes com 6 zonas.

**Receivers e TVs (das bibliotecas usadas)**: Denon aceita **uma** conexão
telnet e briga com qualquer outro controlador, use só HTTP; Onkyo desligado
não responde IP sem "Network Standby", ligue por Wake-on-LAN com o MAC
cadastrado; Samsung e webOS exigem pareamento com popup na TV, que é fluxo
**explícito** (`autenticar()`), nunca efeito colateral do primeiro comando;
Sonos e HEOS são always-on, não declaram `ligar`/`desligar` (omitir a
capacidade é o certo, não implementar para recusar).

**Appliance ARM de referência**: Docker sem bridge e sem iptables
(`network_mode: host` obrigatório, `-p` não funciona), sem BuildKit
(`DOCKER_BUILDKIT=0`, então nada de `$BUILDPLATFORM` no Dockerfile), build só
com `--network host`. `/health` responde em ~7 s no boot; `start_period` de
45 s no healthcheck é folga certa. Consequência para o repositório: o `docker-compose.yml` e o README não podem depender de `-p` (publicação de porta) nem de recurso exclusivo do BuildKit no Dockerfile.

---
