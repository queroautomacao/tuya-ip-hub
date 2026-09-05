# Decisões do Tuya IP Hub

> Como este projeto é construído, do zero. Leia inteiro antes de escrever a
> primeira linha. Este documento é a fonte das decisões; o código obedece a ele,
> não o contrário. Quando uma decisão mudar, mude aqui primeiro.

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
   de primeiro uso, equipamentos (número no app e multiroom no detalhe de cada
   um), drivers, cenas.
3. **Imagem Docker** única, publicada no GHCR, com o painel construído dentro.

O painel tem um **log** (decisão de 5/set/2026): as últimas 1000 linhas do que
o daemon fez, em memória, com o que cada driver pôs no fio, o que a ponte da Tuya
pediu e o que o painel mudou, numa tela com filtro por origem, busca e botão de
copiar. O logger `iphub` desce a DEBUG para o log ver tudo e o handler do
terminal fica em INFO, então o log do container não muda. Nada vai para o disco:
um log que sobrevivesse a um reboot seria um banco de dados. O poll bem
sucedido de um driver não entra (encheria o anel); um poll que falha entra.

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
                              # amplificador | projetor | ar_condicionado |
                              # matriz | rele | outro
    capacidades: tuple        # subconjunto de CAPACIDADES (abaixo)
    teclas: tuple             # subconjunto de TECLAS que o driver manda
    modos: tuple              # ar condicionado: subconjunto de MODOS_AR
    ventos: tuple             # ar condicionado: subconjunto de VENTOS
    auth: Auth                # NENHUMA | POPUP_NO_APARELHO | CODIGO | CHAVE
    descoberta: Descoberta    # ssdp_st, ssdp_fabricantes, mdns_servicos
    config_campos: tuple      # o que o cadastro pede além de ip (ex: porta)
    sugestoes: tuple          # itens que o driver oferece para as listas do cadastro (§8)
    textos: dict              # {"pt": {...}, "en": {...}} tudo que o painel mostra
    motor: str                # "nativo" | "declarativo"

CAPACIDADES = ("ligar", "desligar", "volume", "mudo", "fonte",
               "tocar", "pausar", "parar", "proxima", "anterior",   # transporte
               "agrupar",                                   # multiroom
               "tecla",                                     # uma de TECLAS
               "atalho",                                    # valor da lista do cadastro
               "modo",                                      # ar: MODOS_AR; AV: lista
               "vento", "temperatura",                      # ar condicionado
               "comando_extra")

TECLAS = ("mais", "menos", "canal_mais", "canal_menos", "cima", "baixo",
          "esquerda", "direita", "ok", "voltar", "inicio", "menu", "guia",
          "sair", "info", "play_pause", "proxima", "anterior",
          "digito_0", ..., "digito_9")
MODOS_AR = ("auto", "frio", "quente", "vento", "seco")
VENTOS = ("auto", "baixo", "medio", "alto")
```

`fonte`, `atalho` e `modo` (num equipamento de AV) recebem o **valor do driver**
que a lista do cadastro mapeou a partir do rótulo (§8); `tecla`, `modo` e
`vento` num ar condicionado recebem uma palavra do vocabulário acima, e o driver
a traduz para o protocolo dele. `temperatura` recebe graus inteiros de 16 a 30.
`parar` existe separado de `pausar` (decisão de 5/set/2026) porque uma pausa num
fluxo mantém a caixa conectada à estação, e uma rádio precisa soltá-la; um driver
que só pausa declara só `pausar`.

**Sugestões de lista** (decisão de 5/set/2026): o valor de um atalho ou de uma
entrada é uma string do protocolo do aparelho, que ninguém adivinha, então o
manifesto carrega `sugestoes` (lista, rótulo, valor) e um **cadastro novo nasce
com elas** quando o corpo não manda listas. Uma atualização que manda `listas`
vazio esvazia de propósito e nada volta. A sugestão é julgada pela mesma regra de
um item de cadastro e só vale para lista que uma capacidade declarada lê; o
painel oferece os exemplos de novo pelo botão do cartão de listas. O driver
sugere só o que ele sabe: a caixa LinkPlay sugere rádios, e nunca entradas, que
são as que o `plm_support` dela declara a cada poll.

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
    reproduzindo: bool | None = None    # o transporte esta tocando
    tocando: str | None = None          # o titulo do que toca
    temperatura: int | None = None      # ar condicionado, graus, 16 a 30
    modo: str | None = None             # ar: MODOS_AR; AV: valor do driver
    vento: str | None = None            # ar condicionado, VENTOS
    detalhe: str = ""
```

Regras que o gestor impõe (e testa) para todo driver:

- Ação fora de `capacidades` volta `nao_suportado` **antes** de chegar ao
  driver. O driver nunca implementa método só para recusar.
- **`reproduzindo` e `tocando` são fatos diferentes** (decisão de 3/set/2026): um
  é o transporte, o outro é o título. O título viaja no DP 148 da §8; o transporte
  não tem DP, o painel o lê pela API e o driver LinkPlay o espelha do mestre nos
  escravos. Ler um do outro fazia uma caixa tocando por bluetooth, por entrada de
  linha, ou um rádio sem metadado, reportar **pausada**, e o app mandava play no
  que já tocava. Driver que não sabe dizer deixa `reproduzindo` em `None`.
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
`multiroom`, capacidades de transporte e `agrupar`). Um equipamento cadastrado
pode ocupar um **número numa licença** (§8): um ar condicionado só entra numa
licença de ar, todo o resto entra numa licença de áudio e vídeo. Multiroom é
capacidade do equipamento (categoria `multiroom` mais `agrupar`), mostrada no
detalhe dele, e o grupo é por licença de áudio e vídeo. O **cadastro** de um
equipamento de AV leva as listas de **entradas**, **atalhos** e **modos**, cada
item um par rótulo e valor do driver, dentro dos tetos da §8; o perfil que o
painel da Tuya lê nasce delas e do manifesto. O hub funciona com **zero**
equipamentos e **zero** licenças cadastradas; nenhum assistente exige caixa para
seguir.

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

Além de `ligar`, `desligar`, `volume`, `mudo`, `fonte` e do transporte, um
arquivo declara `tecla`, `atalho`, `modo`, `vento` e `temperatura` com o mesmo
`envia` e `valores` (`"tecla": {"envia": "{valor}", "valores": {"canal_mais":
"CH+"}}`), e `estado.le` lê `temperatura`, `modo` e `vento` como lê `fonte`.

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
`tipo`. Recarrega quando o painel salva, sem reiniciar. Os JSON de exemplo do
marco 3 (TCP, HTTP, UDP) e o ar condicionado por TCP do marco 4b vivem em
`core/tests/drivers/exemplos/` e **não embarcam** (decisão de 4/set/2026):
protocolo inventado para provar o motor não é produto, e a lista de tipos do
painel só oferece o que controla um aparelho de verdade. O catálogo embarcado
nasce vazio e recebe driver revisado da comunidade.

---

## 8. Contrato de data points (DP-bus)

É o que a ponte Tuya consome, e é público. Regras da plataforma, medidas ou
lidas na documentação (§14): o chip Tuya **nunca ecoa** um DP recebido (report
só nasce de estado real); enum customizado tem até **10 valores** de até 15
caracteres; string e raw levam até **255 bytes**; a automação e a voz só agem em
**bool, valor e enum**; a plataforma recomenda até **40 funções** por produto e
até **300 reports por dia** por dispositivo.

### Dois produtos, uma licença por dispositivo (decisão de 4/set/2026)

O hub apresenta **dois produtos** na Tuya, cada um um dispositivo com identidade
própria (uuid, pid e chave), que é uma **licença**: um QR code de pareamento no
app, uma ponte no hub, uma fatia do DP-bus. A casa comum escaneia dois; a casa
com mais máquinas de ar escaneia outro produto de ar. As cenas são do hub e
iguais em todas as licenças.

| Produto | Números | Por número | Da instalação | DPs |
|---|---|---|---|---|
| `ar` (ar condicionado) | 8 máquinas | ligado, temperatura, modo, vento | cena, online, nomes das máquinas, nomes das cenas (2) | 37 |
| `av` (áudio e vídeo) | 12 equipamentos | ligado, nível | cena, grupo, comando, online, mudos, entradas, modos, títulos, perfis (5), nomes das cenas (2) | 39 |

A prioridade é a **cena do hub** disparada pela automação da Tuya; o controle
individual pelo painel é secundário, e o que ele precisa de verdade é a barra
de volume. Por isso um equipamento de AV gasta dois DPs e tudo o mais é da
instalação. Um ar condicionado é produto separado porque a Alexa reconhece o
tipo pela categoria do produto e cada máquina ganha voz pelo nome, com uma
capacidade de voz por DP (toggle, range, mode), mapeada na plataforma.

### Produto `ar`

Máquina k (1..8) começa em `101 + 5·(k-1)`; o quinto número fica livre.

| DP | Tipo | Sentido | Função |
|---|---|---|---|
| base + 0 | bool | R/W | ligado |
| base + 1 | value 16..30 | R/W | temperatura (setpoint em graus) |
| base + 2 | enum auto, frio, quente, vento, seco | R/W | modo |
| base + 3 | enum auto, baixo, medio, alto | R/W | vento |
| 171 | value 1..32 | só envio | cena |
| 172 | value, bit k-1 | report | online por máquina |
| 173 | string JSON `{"m":[...]}` | report | nomes das máquinas |
| 174, 175 | string JSON `{"c":[...]}` | report | nomes das cenas 1..16 e 17..32 |

### Produto `av`

| DP | Tipo | Sentido | Função |
|---|---|---|---|
| 100 + n (101..112) | bool | R/W | ligado do equipamento n; always-on deixa calado |
| 120 + n (121..132) | value 0..100 | R/W | nível (volume) do equipamento n |
| 141 | value 1..32 | só envio | cena |
| 142 | value 0..12 | R/W | grupo: 0 solo, n liderado pelo equipamento n |
| 143 | string | só envio | comando `n:acao[:valor]` |
| 144 | value, bit n-1 | report | online |
| 145 | value, bit n-1 | report | mudos |
| 146 | string `n=k;...` | report | entrada ativa (índice na lista do cadastro) |
| 147 | string `n=k;...` | report | modo de som ativo |
| 148 | string `n=texto;...` | report | título do que toca, até 18 caracteres |
| 149..153 | string | report | perfis empacotados |
| 154, 155 | string JSON `{"c":[...]}` | report | nomes das cenas |

**Canal de comando** (DP 143), do painel para o hub: `n:ligar`, `n:desligar`,
`n:mudo` (alterna), `n:entrada:k`, `n:atalho:k`, `n:modo:k` (k é índice 1..N na
lista do cadastro), `n:tecla:<TECLAS>`, `n:tocar`, `n:pausar`, `n:parar`,
`n:proxima`, `n:anterior`, `n:extra:<nome>`. O hub traduz para a capacidade do driver e
recusa com `nao_suportado` o que o manifesto não declara; o resultado nunca é
ecoado, o estado volta pelos reports.

**Perfil** de um equipamento, o que faz o painel se adaptar:
`numero|template|nome|entradas|atalhos|modos|funcoes`, itens por vírgula,
template `au` (áudio) ou `tv` (TV e projetor), funções como letras, nesta ordem
fixa: **L** liga e desliga, **N** nível, **M** mudo, **E** entrada, **T** teclas,
**D** modo, **A** anterior, **P** tocar e pausar, **S** parar, **F** próxima,
**G** grupo. As quatro letras de transporte (A P S F, decisão de 5/set/2026) são
as teclas de um player na ordem em que ele as desenha; cada uma é capacidade
própria da §6, então cada uma é letra própria, e só L e P exigem as duas metades
do par (uma chave que liga e não desliga não serve, e parar, anterior e próxima
não têm metade oposta).
Os perfis viajam nos DPs 149..153 separados por `;`, empacotados por tamanho.
Tetos do cadastro: rótulo 16 caracteres, 10 entradas, 8 atalhos, 8 modos,
perfil de até 200 bytes; o que não cabe é recusado ao salvar. O nome viaja
encurtado a 20 caracteres no perfil, sem os separadores.
Template pela categoria: `tv` e `projetor` viram `tv`; o resto vira `au`.

### Cenas

Até **32** cenas; a posição é o número. Um passo nomeia **equipamento, ação e
valor**, mais `espera_ms` opcional; sem ela vale o `intervalo_ms` da cena
(padrão 1000). Ações: as CAPACIDADES da §6, menos `agrupar`, mais `grupo`
(valor: a identidade do mestre em que o **equipamento do passo** entra, ou vazio
para ele sair). Um grupo é um mestre e o **conjunto de membros** que o cliente
escolheu (decisão de 5/set/2026), então uma cena o monta **um membro por passo**;
o passo que nomeia o mestre com valor vazio derruba o grupo inteiro, e ninguém
entra em si mesmo. O DP 142 continua o atalho simples: `n` faz o número n liderar
e toda caixa do tipo dele entra. `agrupar` é a capacidade
que o manifesto declara para o equipamento admitir o passo `grupo`, nunca um
passo: o movimento no driver recebe o IP do mestre, e IP nunca é chave (§6),
então a licença resolve a identidade na hora de rodar. Um passo que falha é
registrado e a cena segue. A automação da Tuya dispara a cena escrevendo o número no DP de cena de
qualquer licença; o mesmo número é a mesma cena em todas.

### Reports (decisão de 4/set/2026)

A Tuya recomenda 300 reports por dia num dispositivo comum e limita acima
disso, e **report disparado por consulta não conta**. O barramento:

- reporta só o que **mudou** em relação ao último valor publicado, nunca repete;
- classe **A** (ligado, nível, temperatura, modo, vento, grupo, online): ao
  mudar, janela mínima de **2 s** por DP, o último valor da janela vence;
- classe **B** (entradas, modos, mudos): ao mudar, janela de **10 s**;
- classe **C** (títulos, perfis, nomes): perfis e nomes só quando o cadastro
  muda; títulos **nunca** são empurrados, só respondem à consulta;
- conta os reports do dia por licença: em **250** a classe B para e a classe A
  alarga a janela para 30 s, com aviso no log; a nuvem nunca chega a limitar;
- comando reporta otimista (só se o valor mudou, e dentro da janela alargada
  depois dos 250) e relê em ~1,5 s, reportando só se o aparelho divergiu;
  comando novo para o mesmo DP (no canal de comando, para o mesmo número)
  cancela a verificação pendente;
- na subida da ponte não há rajada: a ponte consulta.

### WebSocket `/dpbus`

O **primeiro frame** é `{"t":"auth","token":"<api_token>","licenca":"<id>"}`
(nunca na URL; sem ele em 5 s, fecha com 4401; licença desconhecida também
fecha). Depois: `{"t":"set","id":..,"dpid":..,"v":..}` e `{"t":"consulta","id":..}`
do cliente; `{"t":"ack",...}`, `{"t":"report",...}` e `{"t":"snapshot",...}` do
servidor. O snapshot da consulta é a fatia daquela licença e não conta como
report. Uma ponte por licença, cada uma com a identidade dela, no mesmo hub.

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
  **Sem recuperação pela rede** (decisão de 4/set/2026): não há e-mail, segundo
  fator nem nuvem que prove quem é o dono, então uma rota de reset seria a porta
  de entrada. Quem alcança o `/data` já é dono, e `python -m iphub.esquecer`
  apaga a senha mantendo equipamentos, números no app e cenas, mata as sessões e rotaciona
  o `api_token`. Apagar o `config.json` levaria a instalação junto, então não é
  esse o caminho.
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
- **Licença** (§8): a chave da Tuya fica no `config.json` e **nunca é entregue
  ao painel**; o QR code de pareamento leva uuid e pid, nunca a chave. Criar,
  editar e apagar licença exige sessão, e apagar uma licença esvazia os números
  dela sem apagar equipamento.
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
  O painel carrega a marca da empresa (decisão de 4/set/2026): o produto se
  apresenta como **"QA IP Hub"**, com o logotipo da Quero Automação no
  cabeçalho; o software continua "Tuya IP Hub" na licença, no README e no
  código. Um fork troca o bloco `Marca` do painel e mais nada.
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
| 4b | §8 v2: dois produtos e licenças, cenas por ação de equipamento, canal de comando, perfis, política de reports, licenças na Conta com QR | os dois produtos cadastrados na Tuya falam com o hub; automação da Tuya dispara cena do hub |
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
  `setPlayerCmd:resume` (provado em 4/set/2026 no firmware 4.6: uma caixa em
  `pause` foi para `play` e o `curpos` andou 3281 ms em 3 s),
  `setPlayerCmd:switchmode:<wifi|bluetooth|line-in|usb>` (só os que
  `plm_support` lista). Preset = tocar URL configurada.
- Multiroom nativo: `ConnectMasterAp:JoinGroupMaster:...` no escravo,
  `multiroom:Ungroup` e afins no mestre. **Play em escravo desmonta o grupo**,
  e rádio ou preset apertado nele também: transporte e atalho vão sempre para
  o mestre. Volume de escravo via `SlaveVolume` no mestre. Um mestre leva **até
  sete escravos** e o cliente **escolhe um a um**: tirar um membro é
  `multiroom:SlaveKickout:<ip>` no mestre (o `Ungroup` derruba todos), então o
  contrato de driver da §6 tem `tirar_do_grupo(ip)` ao lado de `entrar_no_grupo` e
  `desfazer_grupo`. Um membro que o mestre recusou tirar continua nos livros,
  porque ele segue tocando o áudio do grupo. **Escravo reporta `stop` mesmo tocando**: espelhe o estado do
  mestre nos escravos. Grupo só entre caixas do mesmo domínio (LinkPlay com
  LinkPlay); nunca oferecer grupo misto.
- Tocar URL de áudio local (o hub serve por HTTP sem auth em `/audio/`, quem
  busca é a caixa): útil para o teste de som do assistente (um bipe gerado
  com a biblioteca padrão, não uma voz).
- Porta de controle iEAST TCP 8899 **aposentada** (decisão de 5/set/2026): as
  caixas do escritório são módulos LinkPlay comuns (A28 e A31, projeto
  uyesee-i50, `preset_key` 9 e 6) e a API HTTP pública cobre tudo que a porta
  levava: `setPlayerCmd:mute:1` (medido em 4/set/2026 no firmware 4.6, o campo
  `mute` mudou e voltou), `setPlayerCmd:switchmode:<line-in|bluetooth|udisk|
  optical>`, `MCUKeyShortClick:N` para a tecla de preset N, `setPlayerCmd:next`
  e `setPlayerCmd:prev`. Sem porta, não há ritmo de 200 ms a guardar. Os quatro
  últimos ainda não foram exercitados no aparelho (a matriz diz isso).
- **Modo 99 não prova grupo** (medido em 5/set/2026): a caixa Sala, parada depois
  de sair de um grupo, respondia `mode 99` no `getPlayerStatus` com `group 0` e
  sem `master_uuid` no `getStatusEx`, e o driver a tratava como escrava e recusava
  volume e transporte no painel. Escravo é `group 1` (ou `master_uuid` presente)
  no `getStatusEx`; o modo 99 só vale quando o campo `group` falta.
- **Rádio não tem título**: o firmware responde `Title` vazio para um fluxo cru até
  a estação mandar metadado, o que muitas nunca fazem. O hub pediu o fluxo por um
  atalho que o integrador nomeou, então o driver publica esse **rótulo** como
  `tocando` até a caixa nomear algo ou o transporte parar; um "tocando agora"
  vazio numa caixa que toca é o painel se dizendo quebrado.
- `setPlayerCmd:stop` é o que solta o fluxo: a pausa mantém a caixa conectada à
  estação, e uma rádio que derrubou a conexão nesse meio tempo não retoma. Por
  isso a §6 tem `parar` além de `pausar`. Rádio é a URL do fluxo que a caixa
  busca sozinha, http simples, sem redirecionamento e sem query string (a guarda
  do fio recusa `?`, `&` e `=`); o driver sugere três públicas no cadastro.
- **A caixa responde `OK` a qualquer comando**, inclusive a um que não existe
  (medido em 4/set/2026 no firmware 4.6: `setPlayerCmd:naoexiste` devolve `OK`).
  Então a resposta HTTP não é confirmação de nada, e um comando que a caixa não
  suporta chega ao hub como sucesso. A consequência é que a releitura do §8
  contra o estado real do aparelho não é refinamento, é a única verificação que
  existe para estas caixas; a checagem de `OK` só serve para o firmware que
  responde erro.
- Reboot da caixa: some em ~30 s, volta pela identidade em ~50 s sem tocar em IP.

**DP-bus**: report otimista + releitura em 1,5 s funcionou com ack em ~30 ms.
Nomes de equipamento, cena e grupo em JSON compacto cabem em 255 bytes com 6 equipamentos.

**Receivers e TVs (das bibliotecas usadas)**: Denon aceita **uma** conexão
telnet e briga com qualquer outro controlador, use só HTTP; Onkyo desligado
não responde IP sem "Network Standby", ligue por Wake-on-LAN com o MAC
cadastrado; Samsung e webOS exigem pareamento com popup na TV, que é fluxo
**explícito** (`autenticar()`), nunca efeito colateral do primeiro comando;
Sonos e HEOS são always-on, não declaram `ligar`/`desligar` (omitir a
capacidade é o certo, não implementar para recusar).

**Plataforma Tuya (lido na documentação em 4/set/2026)**: enum customizado até
10 valores de 15 caracteres; string e raw até 255 bytes; recomendação de 40
funções por produto; automação e voz só em bool, valor e enum; capacidade de voz
customizada mapeia um DP por capacidade (toggle bool/enum, mode enum, range
inteiro) e é cobrada por capacidade no PID ou por dispositivo; DP customizado
sem essa capacidade não entra na voz; 300 reports por dia recomendados num
dispositivo comum e 3.500 num sensor, throttling acima disso; report disparado
por consulta não conta; o painel MiniApp (Ray) lê o schema em runtime e um
painel serve a mais de um PID. A Tuya não libera o SDK de gateway para ARM,
então o caminho é um dispositivo por licença e não sub-dispositivos.

**Appliance ARM de referência**: Docker sem bridge e sem iptables
(`network_mode: host` obrigatório, `-p` não funciona), sem BuildKit
(`DOCKER_BUILDKIT=0`, então nada de `$BUILDPLATFORM` no Dockerfile), build só
com `--network host`. `/health` responde em ~7 s no boot; `start_period` de
45 s no healthcheck é folga certa. Consequência para o repositório: o `docker-compose.yml` e o README não podem depender de `-p` (publicação de porta) nem de recurso exclusivo do BuildKit no Dockerfile.

---
