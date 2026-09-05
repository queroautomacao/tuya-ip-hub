# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6: the manifest is everything known about a device, and no second table exists.

Seção 6: o manifesto é tudo que se sabe de um aparelho, e não existe segunda tabela.
"""

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

# Section 6: the keys a driver may declare it sends, in the words the whole hub uses; the
# panel and the command channel of section 8 speak these words and the driver translates
# each one to its protocol.
# Seção 6: as teclas que um driver pode declarar que manda, nas palavras que o hub inteiro
# usa; o painel e o canal de comando da seção 8 falam estas palavras e o driver traduz cada
# uma para o protocolo dele.
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

# The vocabulary of an air conditioner, which is the enum of section 8 word for word.
# O vocabulário de um ar condicionado, que é o enum da seção 8 palavra por palavra.
MODOS_AR = ("auto", "frio", "quente", "vento", "seco")
VENTOS = ("auto", "baixo", "medio", "alto")

# Section 6: the setpoint of an air conditioner travels in whole degrees inside this range.
# Seção 6: o setpoint de um ar condicionado viaja em graus inteiros dentro desta faixa.
TEMPERATURA_MINIMA = 16
TEMPERATURA_MAXIMA = 30

CATEGORIA_DE_AR = "ar_condicionado"
CATEGORIAS_DE_TV = ("tv", "projetor")
CAPACIDADES_DE_AR = ("temperatura", "vento")

PRODUTO_AR = "ar"
PRODUTO_AV = "av"
TEMPLATE_TV = "tv"
TEMPLATE_AUDIO = "au"

MOTORES = ("nativo", "declarativo")

IDIOMAS = ("en", "pt")

CAPACIDADE_DE_GRUPO = "agrupar"
CATEGORIA_DE_GRUPO = "multiroom"

CAMPO_RESERVADO = "ip"

# The fields of Descoberta, each reported under its own name so a fix has one address.
# Os campos da Descoberta, cada um reportado sob o próprio nome para um conserto ter endereço.
ASSINATURAS = ("ssdp_st", "ssdp_fabricantes", "mdns_servicos")

TEXTO_DESCRICAO = "descricao"
TEXTO_AUTH = "auth_ajuda"
PREFIXO_TEXTO_CAMPO = "campo_"

# Why: the tipo is the key of the equipment in config.json and of the driver in the
# catalog, so it stays in the alphabet a file name and a JSON key share.
# Por que: o tipo é a chave do equipamento no config.json e do driver no catálogo, então
# ele fica no alfabeto que um nome de arquivo e uma chave JSON compartilham.
_TIPO = re.compile(r"[a-z0-9_]+")


class Auth(StrEnum):
    """How the device grants control, section 6: it travels to the panel as plain text.

    Como o aparelho concede controle, seção 6: viaja para o painel como texto puro.
    """

    NENHUMA = "nenhuma"
    POPUP_NO_APARELHO = "popup_no_aparelho"
    CODIGO = "codigo"
    CHAVE = "chave"


class TipoCampo(StrEnum):
    """SEGREDO is a device credential: it lives in config.json and never leaves the daemon.

    SEGREDO é credencial de aparelho: vive no config.json e nunca sai do daemon.
    """

    TEXTO = "texto"
    INTEIRO = "inteiro"
    SEGREDO = "segredo"


@dataclass(frozen=True)
class Campo:
    """What the registration asks for besides the ip, which is never a field of its own.

    O que o cadastro pede além do ip, que nunca é campo próprio.
    """

    nome: str
    tipo: TipoCampo = TipoCampo.TEXTO
    obrigatorio: bool = False
    padrao: str = ""


@dataclass(frozen=True)
class Descoberta:
    """The signatures this driver claims; the sweep plan is generated from them.

    As assinaturas que este driver reivindica; o plano de varredura nasce delas.
    """

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
    motor: str = "nativo"
    # The words of TECLAS this driver sends, and for an air conditioner the modes and the
    # fan speeds of its vocabulary; a driver that declares the capability declares the words.
    # As palavras de TECLAS que este driver manda, e num ar condicionado os modos e as
    # velocidades do vocabulário dele; um driver que declara a capacidade declara as palavras.
    teclas: tuple[str, ...] = ()
    modos: tuple[str, ...] = ()
    ventos: tuple[str, ...] = ()


@dataclass
class Estado:
    """The only shape a driver publishes: a new field here, with a test, or nowhere.

    A única forma que um driver publica: campo novo aqui, com teste, ou em lugar nenhum.
    """

    online: bool
    ligado: bool | None = None
    # Why: section 6 fixes the scale at 0 to 100 so the DP-bus and the panel never ask which
    # scale a given device speaks; converting the real range is the driver's job.
    # Por que: a seção 6 fixa a escala em 0 a 100 para o DP-bus e o painel nunca perguntarem
    # que escala um aparelho fala; converter a faixa real é trabalho do driver.
    volume: int | None = None
    mudo: bool | None = None
    fonte: str | None = None
    fontes: tuple = ()
    # Why: the transport and the title are different facts, and reading one from the other
    # made a speaker playing over bluetooth, over a line input, or a radio with no metadata,
    # report paused while it played. A driver that cannot tell leaves it None.
    # Por que: o transporte e o título são fatos diferentes, e ler um do outro fazia uma caixa
    # tocando por bluetooth, por entrada de linha, ou um rádio sem metadado, reportar pausada
    # enquanto tocava. Um driver que não sabe dizer deixa isto em None.
    reproduzindo: bool | None = None
    tocando: str | None = None
    # Why: an air conditioner is one of the two products of section 8, and its setpoint,
    # mode and fan speed are the facts the app reads; a receiver reads modo as its sound mode.
    # Por que: um ar condicionado é um dos dois produtos da seção 8, e o setpoint, o modo e a
    # velocidade dele são os fatos que o app lê; um receiver lê modo como o modo de som.
    temperatura: int | None = None
    modo: str | None = None
    vento: str | None = None
    detalhe: str = ""


def produto_de(categoria: str) -> str:
    """Section 8: an air conditioner goes to the product of air, everything else to audio
    and video.

    Seção 8: um ar condicionado vai para o produto de ar, todo o resto para áudio e vídeo.
    """
    return PRODUTO_AR if categoria == CATEGORIA_DE_AR else PRODUTO_AV


def template_de(categoria: str) -> str:
    """The template the panel of section 8 draws: tv for a TV or a projector, audio for the
    rest of the audio and video product.

    O template que o painel da seção 8 desenha: tv para uma TV ou um projetor, áudio para o
    resto do produto de áudio e vídeo.
    """
    return TEMPLATE_TV if categoria in CATEGORIAS_DE_TV else TEMPLATE_AUDIO


class ManifestoInvalido(ValueError):
    """Carries every problem found, so a contributor fixes the driver in one pass.

    Carrega todo problema encontrado, para um contribuidor consertar o driver numa passada.
    """

    def __init__(self, tipo: object, problemas: tuple[str, ...]) -> None:
        self.tipo = tipo
        self.problemas = problemas
        super().__init__(f"manifesto {tipo!r} is invalid: " + "; ".join(problemas))


def validar(manifesto: Manifesto) -> None:
    """Raises ManifestoInvalido listing EVERY broken rule at once, never only the first.

    Estoura ManifestoInvalido listando TODA regra quebrada de uma vez, nunca só a primeira.
    """
    encontrados = tuple(_problemas(manifesto))
    if encontrados:
        raise ManifestoInvalido(manifesto.tipo, encontrados)


def _problemas(manifesto: Manifesto) -> Iterator[str]:
    yield from _da_identidade(manifesto)
    yield from _da_descoberta(manifesto)
    yield from _das_capacidades(manifesto)
    yield from _do_rotulo(manifesto)
    yield from _dos_campos(manifesto)
    yield from _dos_textos(manifesto)


def _da_identidade(manifesto: Manifesto) -> Iterator[str]:
    tipo = manifesto.tipo
    if not isinstance(tipo, str) or not _TIPO.fullmatch(tipo):
        yield f"tipo: must be a non empty name of [a-z0-9_], found {tipo!r}"
    if manifesto.categoria not in CATEGORIAS:
        yield f"categoria: must be one of {list(CATEGORIAS)}, found {manifesto.categoria!r}"
    if manifesto.motor not in MOTORES:
        yield f"motor: must be one of {list(MOTORES)}, found {manifesto.motor!r}"
    if not isinstance(manifesto.auth, Auth):
        yield f"auth: must be an Auth, found {manifesto.auth!r}"
    if not isinstance(manifesto.descoberta, Descoberta):
        yield f"descoberta: must be a Descoberta, found {manifesto.descoberta!r}"


def _da_descoberta(manifesto: Manifesto) -> Iterator[str]:
    # Why: a signature that is not a tuple of strings only breaks when the sweep plan is
    # built, which is runtime on a customer LAN; section 6 wants it to break at load.
    # Por que: uma assinatura que não é tupla de strings só quebra quando o plano de varredura
    # é montado, que é runtime numa LAN de cliente; a seção 6 quer que quebre na carga.
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
        yield f"capacidades: outside the vocabulary of section 6: {fora}"
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
    # Why: section 6, an air conditioner speaks modo and vento in the words of the vocabulary,
    # so it declares them; any other equipment takes the modo of the registration list, so it
    # declares no word and a word it declared anyway would be a list nobody reads.
    # Por que: seção 6, um ar condicionado fala modo e vento nas palavras do vocabulário, então
    # os declara; todo outro equipamento recebe o modo da lista do cadastro, então não declara
    # palavra e uma palavra declarada assim mesmo seria uma lista que ninguém lê.
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
    """A capability spoken in words is declared with its words, and only with known ones.

    Uma capacidade falada em palavras é declarada com as palavras dela, e só com conhecidas.
    """
    palavras = getattr(manifesto, campo)
    if not isinstance(palavras, tuple) or not all(isinstance(p, str) for p in palavras):
        yield f"{campo}: must be a tuple of strings, found {palavras!r}"
        return
    fora = [p for p in palavras if p not in vocabulario]
    if fora:
        yield f"{campo}: outside the vocabulary of section 6: {fora}"
    repetidas = sorted({p for p in palavras if palavras.count(p) > 1})
    if repetidas:
        yield f"{campo}: repeated: {repetidas}"
    declara = isinstance(manifesto.capacidades, tuple) and capacidade in manifesto.capacidades
    if declara and not palavras and exige_palavras:
        yield f"{campo}: capacidade {capacidade!r} needs at least one word"
    if palavras and not (declara and exige_palavras):
        yield f"{campo}: words declared without the capacidade {capacidade!r}"


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
        # Why: section 6 makes the ip the address the discovery re-resolves, never identity
        # and never configuration; a driver that took it as a field would freeze it.
        # Por que: a seção 6 faz do ip o endereço que a descoberta re-resolve, nunca
        # identidade e nunca configuração; um driver que o tomasse como campo o congelaria.
        yield (
            f"config_campos: a campo named {CAMPO_RESERVADO!r} is refused; the ip is the "
            f"address the discovery re-resolves, not a config field"
        )
    sem_tipo = sorted(c.nome for c in campos if not isinstance(c.tipo, TipoCampo))
    if sem_tipo:
        yield f"config_campos: tipo must be a TipoCampo in {sem_tipo}"
    # Why: the panel reads padrao as text and obrigatorio as a flag, so a port default
    # written as the number 4352 makes its reader refuse the WHOLE catalog and leaves the
    # operator with an empty form for every driver. True as 1 is refused for the same reason.
    # Por que: o painel lê padrao como texto e obrigatorio como bandeira, então um padrão de
    # porta escrito como o número 4352 faz o leitor dele recusar o catálogo INTEIRO e deixa o
    # integrador com formulário vazio em todo driver. True como 1 é recusado pelo mesmo motivo.
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
    # Why: a hand written manifest can mix key types, and sorting them raw would raise
    # inside the validator, which must report the problem instead of crashing on it.
    # Por que: um manifesto escrito na mão pode misturar tipos de chave, e ordená-las
    # cruas estouraria dentro do validador, que precisa relatar o problema, não quebrar.
    if not isinstance(valor, dict):
        return type(valor).__name__
    return sorted(str(chave) for chave in valor)
