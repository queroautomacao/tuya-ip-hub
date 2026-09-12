# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The manifest is everything known about a device, and no second table exists."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

CAPACIDADES = (
    "ligar",
    "desligar",
    "volume",
    "mudo",
    "fonte",
    "tocar",
    "pausar",
    "parar",
    "proxima",
    "anterior",
    "agrupar",
    "tecla",
    "atalho",
    "modo",
    "vento",
    "temperatura",
    "comando_extra",
)

CATEGORIAS = (
    "audio",
    "multiroom",
    "tv",
    "receiver",
    "soundbar",
    "amplificador",
    "projetor",
    "ar_condicionado",
    "matriz",
    "rele",
    "outro",
)

# The keys a driver may declare it sends, in the words the whole hub uses; the
# panel and the command channel speak these words and the driver translates
# each one to its protocol.
TECLAS = (
    "mais",
    "menos",
    "canal_mais",
    "canal_menos",
    "cima",
    "baixo",
    "esquerda",
    "direita",
    "ok",
    "voltar",
    "inicio",
    "menu",
    "guia",
    "sair",
    "info",
    "play_pause",
    "proxima",
    "anterior",
    *(f"digito_{n}" for n in range(10)),
)

# The lists a registration carries, each with its ceiling, plus the ceiling of a
# label and of a value. They live here, with the vocabulary, because a manifest suggests items
# for them and the registration judges items by the same rule; config re-exports them.
LISTAS = ("entradas", "atalhos", "modos")
LISTAS_MAXIMO = {"entradas": 10, "atalhos": 8, "modos": 8}
ROTULO_MAXIMO = 16
VALOR_DE_LISTA_MAXIMO = 64

# Which capability reads each list; a driver only suggests items for a list that
# something reads, and an air conditioner reads modo from the vocabulary and not from a list.
CAPACIDADE_DA_LISTA = {"entradas": "fonte", "atalhos": "atalho", "modos": "modo"}

# The vocabulary of an air conditioner, which is the enum word for word.
MODOS_AR = ("auto", "frio", "quente", "vento", "seco")
VENTOS = ("auto", "baixo", "medio", "alto")

# The setpoint of an air conditioner travels in whole degrees inside this range.
TEMPERATURA_MINIMA = 16
TEMPERATURA_MAXIMA = 30

CATEGORIA_DE_AR = "ar_condicionado"
CATEGORIAS_DE_TV = ("tv", "projetor")
CAPACIDADES_DE_AR = ("temperatura", "vento")

PRODUTO_AR = "ar"
PRODUTO_AV = "av"
TEMPLATE_TV = "tv"
TEMPLATE_AUDIO = "au"


IDIOMAS = ("en", "pt")

CAPACIDADE_DE_GRUPO = "agrupar"
CATEGORIA_DE_GRUPO = "multiroom"

CAMPO_RESERVADO = "ip"

# The fields of Descoberta, each reported under its own name so a fix has one address.
ASSINATURAS = ("ssdp_st", "ssdp_fabricantes", "mdns_servicos")

TEXTO_DESCRICAO = "descricao"
TEXTO_PREPARO = "preparo"
TEXTO_AUTH = "auth_ajuda"
PREFIXO_TEXTO_CAMPO = "campo_"

# The tipo is the key of the equipment in config.json and of the driver in the
# catalog, so it stays in the alphabet a file name and a JSON key share.
_TIPO = re.compile(r"[a-z0-9_]+")


class Auth(StrEnum):
    """How the device grants control, it travels to the panel as plain text."""

    NENHUMA = "nenhuma"
    POPUP_NO_APARELHO = "popup_no_aparelho"
    CODIGO = "codigo"
    CHAVE = "chave"


class TipoCampo(StrEnum):
    """SEGREDO is a device credential: it lives in config.json and never leaves the daemon."""

    TEXTO = "texto"
    INTEIRO = "inteiro"
    SEGREDO = "segredo"


@dataclass(frozen=True)
class Campo:
    """What the registration asks for besides the ip, which is never a field of its own."""

    nome: str
    tipo: TipoCampo = TipoCampo.TEXTO
    obrigatorio: bool = False
    padrao: str = ""


@dataclass(frozen=True)
class Sugestao:
    """One item a driver offers for a list of the registration, so an equipment that was just
    added already carries something that works and shows the shape of the value.

    The value of a shortcut or of an input is a string of the protocol of the device, and
    an empty list teaches nobody what to type; the driver is the one place that knows.
    """

    lista: str
    rotulo: str
    valor: str


@dataclass(frozen=True)
class Descoberta:
    """The signatures this driver claims; the sweep plan is generated from them."""

    ssdp_st: tuple[str, ...] = ()
    ssdp_fabricantes: tuple[str, ...] = ()
    mdns_servicos: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifesto:
    tipo: str
    rotulo: dict[str, str]
    categoria: str
    capacidades: tuple[str, ...] = ()
    auth: Auth = Auth.NENHUMA
    descoberta: Descoberta = Descoberta()
    config_campos: tuple[Campo, ...] = ()
    textos: dict[str, dict[str, str]] = field(default_factory=dict)
    # The panel of the integrator shows which brands this hub reaches, and the brand of a
    # driver is a fact about the driver, so it is declared here and nowhere else; leaving it
    # empty is what a driver of no brand in particular (a generic protocol) does.
    marca: str = ""
    # The words of TECLAS this driver sends, and for an air conditioner the modes and the
    # fan speeds of its vocabulary; a driver that declares the capability declares the words.
    teclas: tuple[str, ...] = ()
    modos: tuple[str, ...] = ()
    ventos: tuple[str, ...] = ()
    # A device with no local API at all is reached through the cloud of its
    # maker, and then there is no address on the LAN to ask for or to validate; the identity
    # is the id the cloud gives it and the discovery has nothing to sweep.
    nuvem: bool = False
    # The NAME of the config field that the chosen device fills, and empty for a driver
    # that lists nothing. One field carries both facts, because the panel that offers a list
    # has to know where to put what was picked, and a second boolean beside a name could
    # disagree with it. It is not the same as nuvem: a cloud driver may command one device the
    # token already names, and a driver on the network never lists anything.
    lista_da_conta: str = ""
    # What this driver offers to fill the lists of a new registration with.
    sugestoes: tuple[Sugestao, ...] = ()


@dataclass
class Estado:
    """The only shape a driver publishes: a new field here, with a test, or nowhere."""

    online: bool
    ligado: bool | None = None
    # Fixes the scale at 0 to 100 so the DP-bus and the panel never ask which
    # scale a given device speaks; converting the real range is the driver's job.
    volume: int | None = None
    mudo: bool | None = None
    fonte: str | None = None
    fontes: tuple = ()
    # The sound modes of a receiver are a list of the MODEL and not of the line, and the
    # same word means nothing on the unit next to it: a receiver that answers 2ch_stereo and
    # straight refuses movie and game, which another of the same maker accepts. A driver that
    # can ask the device publishes what it answered, and the panel fills the list of the
    # registration from that instead of from a guess written months earlier in a manifest.
    # A driver that cannot ask leaves it empty and the operator types.
    modos: tuple = ()
    # The shortcuts of a device are what IT has installed, and no manifest knows that:
    # one television has Prime Video, Disney+ and GloboPlay and the one next to it has none of
    # them, while the id of each is a word only the television can say. Pairs of (value,
    # label) and not bare values, because the television already names them the way the
    # customer reads them, and a raw com.disney.disneyplus-prod on a button teaches nobody.
    atalhos: tuple = ()
    # The transport and the title are different facts, and reading one from the other
    # made a speaker playing over bluetooth, over a line input, or a radio with no metadata,
    # report paused while it played. A driver that cannot tell leaves it None.
    reproduzindo: bool | None = None
    tocando: str | None = None
    # An air conditioner is one of the two products, and its setpoint,
    # mode and fan speed are the facts the app reads; a receiver reads modo as its sound mode.
    temperatura: int | None = None
    modo: str | None = None
    vento: str | None = None
    detalhe: str = ""


def produto_de(categoria: str) -> str:
    """An air conditioner goes to the product of air, everything else to audio
    and video.
    """
    return PRODUTO_AR if categoria == CATEGORIA_DE_AR else PRODUTO_AV


def template_de(categoria: str) -> str:
    """The template the panel draws: tv for a TV or a projector, audio for the
    rest of the audio and video product.
    """
    return TEMPLATE_TV if categoria in CATEGORIAS_DE_TV else TEMPLATE_AUDIO


class ManifestoInvalido(ValueError):
    """Carries every problem found, so a contributor fixes the driver in one pass."""

    def __init__(self, tipo: object, problemas: tuple[str, ...]) -> None:
        self.tipo = tipo
        self.problemas = problemas
        super().__init__(f"manifesto {tipo!r} is invalid: " + "; ".join(problemas))


def validar(manifesto: Manifesto) -> None:
    """Raises ManifestoInvalido listing EVERY broken rule at once, never only the first."""
    encontrados = tuple(_problemas(manifesto))
    if encontrados:
        raise ManifestoInvalido(manifesto.tipo, encontrados)


def _problemas(manifesto: Manifesto) -> Iterator[str]:
    yield from _da_identidade(manifesto)
    yield from _da_descoberta(manifesto)
    yield from _da_nuvem(manifesto)
    yield from _da_lista_da_conta(manifesto)
    yield from _das_capacidades(manifesto)
    yield from _das_sugestoes(manifesto)
    yield from _do_rotulo(manifesto)
    yield from _dos_campos(manifesto)
    yield from _dos_textos(manifesto)


def _da_identidade(manifesto: Manifesto) -> Iterator[str]:
    tipo = manifesto.tipo
    if not isinstance(tipo, str) or not _TIPO.fullmatch(tipo):
        yield f"tipo: must be a non empty name of [a-z0-9_], found {tipo!r}"
    if manifesto.categoria not in CATEGORIAS:
        yield f"categoria: must be one of {list(CATEGORIAS)}, found {manifesto.categoria!r}"
    if not isinstance(manifesto.auth, Auth):
        yield f"auth: must be an Auth, found {manifesto.auth!r}"
    if not isinstance(manifesto.descoberta, Descoberta):
        yield f"descoberta: must be a Descoberta, found {manifesto.descoberta!r}"


def _da_nuvem(manifesto: Manifesto) -> Iterator[str]:
    """A cloud driver has nothing to sweep for and asks for a credential, never an address."""
    if not isinstance(manifesto.nuvem, bool):
        yield f"nuvem: must be a bool, found {manifesto.nuvem!r}"
        return
    if not manifesto.nuvem:
        return
    descoberta = manifesto.descoberta
    if isinstance(descoberta, Descoberta) and any(
        getattr(descoberta, nome) for nome in ASSINATURAS
    ):
        # A signature would put the device in the sweep plan of the LAN, where it will
        # never answer, and the finding of a sweep is an address, which this driver has none of.
        yield "nuvem: a cloud driver declares no descoberta signature"
    if manifesto.auth is Auth.NENHUMA:
        # Reaching a cloud means holding a credential of the customer, and has
        # one place for that, which is the explicit autenticar.
        yield "nuvem: a cloud driver authenticates, so auth cannot be NENHUMA"


def _da_descoberta(manifesto: Manifesto) -> Iterator[str]:
    # A signature that is not a tuple of strings only breaks when the sweep plan is
    # built, which is runtime on a customer LAN; wants it to break at load.
    descoberta = manifesto.descoberta
    if not isinstance(descoberta, Descoberta):
        return
    for nome in ASSINATURAS:
        valores = getattr(descoberta, nome)
        if not isinstance(valores, tuple) or not all(isinstance(v, str) for v in valores):
            yield f"{nome}: must be a tuple of strings, found {valores!r}"


def _das_capacidades(manifesto: Manifesto) -> Iterator[str]:
    capacidades = manifesto.capacidades
    if not isinstance(capacidades, tuple):
        yield f"capacidades: must be a tuple, found {type(capacidades).__name__}"
        return
    fora = [c for c in capacidades if c not in CAPACIDADES]
    if fora:
        yield f"capacidades: outside the vocabulary: {fora}"
    repetidas = sorted({c for c in capacidades if capacidades.count(c) > 1 and c in CAPACIDADES})
    if repetidas:
        yield f"capacidades: repeated: {repetidas}"
    if CAPACIDADE_DE_GRUPO in capacidades and manifesto.categoria != CATEGORIA_DE_GRUPO:
        yield (
            f"capacidades: {CAPACIDADE_DE_GRUPO!r} is only valid for categoria "
            f"{CATEGORIA_DE_GRUPO!r}, found {manifesto.categoria!r}"
        )
    de_ar = [c for c in CAPACIDADES_DE_AR if c in capacidades]
    if de_ar and manifesto.categoria != CATEGORIA_DE_AR:
        yield f"capacidades: {de_ar} are only valid for categoria {CATEGORIA_DE_AR!r}"
    yield from _do_vocabulario(manifesto, "tecla", "teclas", TECLAS)
    # An air conditioner speaks modo and vento in the words of the vocabulary,
    # so it declares them; any other equipment takes the modo of the registration list, so it
    # declares no word and a word it declared anyway would be a list nobody reads.
    de_ar = manifesto.categoria == CATEGORIA_DE_AR
    yield from _do_vocabulario(manifesto, "modo", "modos", MODOS_AR, exige_palavras=de_ar)
    yield from _do_vocabulario(manifesto, "vento", "ventos", VENTOS, exige_palavras=de_ar)


def _do_vocabulario(
    manifesto: Manifesto,
    capacidade: str,
    campo: str,
    vocabulario: tuple[str, ...],
    *,
    exige_palavras: bool = True,
) -> Iterator[str]:
    """A capability spoken in words is declared with its words, and only with known ones."""
    palavras = getattr(manifesto, campo)
    if not isinstance(palavras, tuple) or not all(isinstance(p, str) for p in palavras):
        yield f"{campo}: must be a tuple of strings, found {palavras!r}"
        return
    fora = [p for p in palavras if p not in vocabulario]
    if fora:
        yield f"{campo}: outside the vocabulary: {fora}"
    repetidas = sorted({p for p in palavras if palavras.count(p) > 1})
    if repetidas:
        yield f"{campo}: repeated: {repetidas}"
    declara = isinstance(manifesto.capacidades, tuple) and capacidade in manifesto.capacidades
    if declara and not palavras and exige_palavras:
        yield f"{campo}: capacidade {capacidade!r} needs at least one word"
    if palavras and not (declara and exige_palavras):
        yield f"{campo}: words declared without the capacidade {capacidade!r}"


def _das_sugestoes(manifesto: Manifesto) -> Iterator[str]:
    """A suggestion is judged by the rule the registration judges an item by, and offered only
    for a list a declared capability reads.
    """
    sugestoes = manifesto.sugestoes
    if not isinstance(sugestoes, tuple) or not all(isinstance(s, Sugestao) for s in sugestoes):
        yield "sugestoes: must be a tuple of Sugestao"
        return
    capacidades = manifesto.capacidades if isinstance(manifesto.capacidades, tuple) else ()
    fora = sorted({s.lista for s in sugestoes if s.lista not in LISTAS})
    if fora:
        yield f"sugestoes: lista must be one of {list(LISTAS)}, found {fora}"
    for lista in LISTAS:
        itens = [s for s in sugestoes if s.lista == lista]
        if not itens:
            continue
        capacidade = CAPACIDADE_DA_LISTA[lista]
        if capacidade not in capacidades:
            yield f"sugestoes: {lista!r} is only read with the capacidade {capacidade!r}"
        if lista == "modos" and manifesto.categoria == CATEGORIA_DE_AR:
            yield "sugestoes: an air conditioner reads modo from the vocabulary, not from a list"
        teto = LISTAS_MAXIMO[lista]
        if len(itens) > teto:
            yield f"sugestoes: {lista!r} has {len(itens)} items, the ceiling is {teto}"
        invalidos = [s.rotulo for s in itens if not item_valido(s.rotulo, s.valor)]
        if invalidos:
            yield f"sugestoes: {lista!r} carries an item the registration would refuse: {invalidos}"


def _da_lista_da_conta(manifesto: Manifesto):
    """The field the chosen device fills has to be a field this manifest declares.

    A name that is not declared is a device the panel picks and then writes nowhere, and
    the operator sees a list that does nothing when he clicks it.
    """
    nome = manifesto.lista_da_conta
    if not nome:
        return
    if nome not in {campo.nome for campo in manifesto.config_campos}:
        yield f"lista_da_conta: {nome!r} is not a field of config_campos"


def por_lista(manifesto: Manifesto) -> dict[str, tuple[Sugestao, ...]]:
    """The suggestions of a manifest grouped by the list they fill, in the order declared."""
    agrupadas = {}
    for lista in LISTAS:
        itens = tuple(s for s in manifesto.sugestoes if s.lista == lista)
        if itens:
            agrupadas[lista] = itens
    return agrupadas


def item_valido(rotulo: object, valor: object) -> bool:
    """A label the app can show and a value the driver can take,."""
    # The label travels inside the profile string, where ',' '|' and ';'
    # are the separators, and a control character would break the JSON of the bus.
    if not isinstance(rotulo, str) or not isinstance(valor, str):
        return False
    if not 0 < len(rotulo) <= ROTULO_MAXIMO or not rotulo.isprintable():
        return False
    if any(separador in rotulo for separador in (",", "|", ";")):
        return False
    return 0 < len(valor) <= VALOR_DE_LISTA_MAXIMO and valor.isprintable()


def _do_rotulo(manifesto: Manifesto) -> Iterator[str]:
    rotulo = manifesto.rotulo
    if not isinstance(rotulo, dict) or set(rotulo) != set(IDIOMAS):
        yield f"rotulo: must carry exactly the keys {sorted(IDIOMAS)}, found {_chaves(rotulo)}"
        return
    vazios = sorted(i for i in IDIOMAS if not isinstance(rotulo[i], str) or not rotulo[i].strip())
    if vazios:
        yield f"rotulo: empty or non string label for {vazios}"


def _dos_campos(manifesto: Manifesto) -> Iterator[str]:
    campos = manifesto.config_campos
    if not isinstance(campos, tuple) or not all(isinstance(c, Campo) for c in campos):
        yield "config_campos: must be a tuple of Campo"
        return
    nomes = [c.nome for c in campos]
    if not all(isinstance(n, str) and n.strip() for n in nomes):
        yield f"config_campos: every nome must be a non empty string, found {nomes!r}"
        return
    repetidos = sorted({n for n in nomes if nomes.count(n) > 1})
    if repetidos:
        yield f"config_campos: repeated nome: {repetidos}"
    if CAMPO_RESERVADO in nomes:
        # Makes the ip the address the discovery re-resolves, never identity
        # and never configuration; a driver that took it as a field would freeze it.
        yield (
            f"config_campos: a campo named {CAMPO_RESERVADO!r} is refused; the ip is the "
            f"address the discovery re-resolves, not a config field"
        )
    sem_tipo = sorted(c.nome for c in campos if not isinstance(c.tipo, TipoCampo))
    if sem_tipo:
        yield f"config_campos: tipo must be a TipoCampo in {sem_tipo}"
    # The panel reads padrao as text and obrigatorio as a flag, so a port default
    # written as the number 4352 makes its reader refuse the WHOLE catalog and leaves the
    # operator with an empty form for every driver. True as 1 is refused for the same reason.
    sem_padrao = sorted(c.nome for c in campos if not isinstance(c.padrao, str))
    if sem_padrao:
        yield f"config_campos: padrao must be a string in {sem_padrao}"
    sem_bandeira = sorted(c.nome for c in campos if type(c.obrigatorio) is not bool)
    if sem_bandeira:
        yield f"config_campos: obrigatorio must be a bool in {sem_bandeira}"


def _dos_textos(manifesto: Manifesto) -> Iterator[str]:
    textos = manifesto.textos
    if not isinstance(textos, dict) or set(textos) != set(IDIOMAS):
        yield f"textos: must carry exactly the keys {sorted(IDIOMAS)}, found {_chaves(textos)}"
        return
    if not all(isinstance(textos[i], dict) for i in IDIOMAS):
        yield "textos: every language must hold an object"
        return
    chaves = {idioma: set(textos[idioma]) for idioma in IDIOMAS}
    diferenca = sorted(chaves["pt"] ^ chaves["en"])
    if diferenca:
        yield f"textos: pt and en must carry the same keys, difference: {diferenca}"
    exigidas = _textos_exigidos(manifesto)
    for idioma in IDIOMAS:
        faltando = sorted(exigidas - chaves[idioma])
        if faltando:
            yield f"textos: {idioma} is missing {faltando}"
    nao_texto = sorted(
        f"{idioma}.{chave}"
        for idioma in IDIOMAS
        for chave, valor in textos[idioma].items()
        if not isinstance(valor, str)
    )
    if nao_texto:
        yield f"textos: every value must be a string, other type found in {nao_texto}"


def _textos_exigidos(manifesto: Manifesto) -> set[str]:
    exigidas = {TEXTO_DESCRICAO}
    if manifesto.auth != Auth.NENHUMA:
        exigidas.add(TEXTO_AUTH)
    if isinstance(manifesto.config_campos, tuple):
        for campo in manifesto.config_campos:
            if isinstance(campo, Campo) and isinstance(campo.nome, str):
                exigidas.add(PREFIXO_TEXTO_CAMPO + campo.nome)
    return exigidas


def _chaves(valor: object) -> object:
    # A hand written manifest can mix key types, and sorting them raw would raise
    # inside the validator, which must report the problem instead of crashing on it.
    if not isinstance(valor, dict):
        return type(valor).__name__
    return sorted(str(chave) for chave in valor)
