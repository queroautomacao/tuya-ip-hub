# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8: the numbering of the data points, the direction of each one and the names.

This module is the only place the numbers of the contract exist, and it opens no socket and
knows no driver: the bus, the panel routes and a scene all read the same table instead of
each one carrying a number of its own.

Three platform rules of section 8 shape it, and they are why a table alone is not enough.
The Tuya chip NEVER echoes a data point it received, so a report is only ever born of real
state and a send only DP (the preset and the scene) is never reported. A custom enum is not
reported either and takes at most ten values. A string DP carries at most 255 BYTES, which
is why the names of DP 133, 134 and 135 are refused when they do not fit, instead of being
cut in the middle of a character, where the JSON would reach the bridge unparseable.

Seção 8: a numeração dos data points, o sentido de cada um e os nomes.

Este módulo é o único lugar onde os números do contrato existem, e ele não abre socket e não
conhece driver: o barramento, as rotas do painel e uma cena leem a mesma tabela em vez de
cada um carregar um número próprio.

Três regras de plataforma da seção 8 o moldam, e elas são o motivo de uma tabela sozinha não
bastar. O chip Tuya NUNCA ecoa um data point que recebeu, então um report só nasce de estado
real e um DP de só envio (o preset e a cena) nunca é reportado. Um enum customizado também
não é reportado e aceita no máximo dez valores. Um DP string carrega no máximo 255 BYTES, e
por isso os nomes dos DP 133, 134 e 135 são recusados quando não cabem, em vez de cortados no
meio de um caractere, onde o JSON chegaria à ponte impossível de ler.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

ZONAS = 6
ZONA_BASE = 101
ZONA_PASSO = 5
ENTRADA_BASE = 140

CENA = 131
GRUPO = 132
NOMES_ZONAS = 133
NOMES_CENAS = 134
NOMES_GRUPOS = 135

# The five DPs of a zone block, in the order section 8 numbers them.
# Os cinco DPs de um bloco de zona, na ordem em que a seção 8 os numera.
FUNCOES_DO_BLOCO = ("volume", "play", "preset", "online", "tocando")
FUNCAO_ENTRADA = "entrada"
FUNCOES_ZONA = (*FUNCOES_DO_BLOCO, FUNCAO_ENTRADA)
FUNCOES_GLOBAIS = ("cena", "grupo", "nomes_zonas", "nomes_cenas", "nomes_grupos")

# Why: a custom enum takes at most ten values on the platform, so solo plus nine groups is
# the whole of DP 132, and a tenth group would be a value the platform refuses to carry.
# Por que: um enum customizado aceita no máximo dez valores na plataforma, então solo mais
# nove grupos caberia no DP 132, e um décimo grupo seria valor que a plataforma recusa.
ENUM_MAXIMO = 10
PRESETS = 8
CENAS = 8
# Why: a group is named after the zone that leads it, and section 8 has six blocks, so grupo7
# to grupo9 were values the panel offered and a scene could save that no zone can ever name:
# the platform would take the set and the hub would answer that the group does not exist.
# Por que: um grupo tem o nome da zona que o lidera, e a seção 8 tem seis blocos, então
# grupo7 a grupo9 eram valores que o painel oferecia e uma cena podia salvar que nenhuma zona
# consegue nomear: a plataforma aceitaria o set e o hub responderia que o grupo não existe.
GRUPOS = ZONAS

TEXTO_MAXIMO_BYTES = 255
THROTTLE_TOCANDO_S = 5.0

VALOR_MINIMO = 0
VALOR_MAXIMO = 100

SOLO = "solo"
VALORES_PRESET = tuple(f"cmd{n}" for n in range(1, PRESETS + 1))
VALORES_CENA = tuple(f"cena{n}" for n in range(1, CENAS + 1))
VALORES_GRUPO = (SOLO, *(f"grupo{n}" for n in range(1, GRUPOS + 1)))

# The stable codes a caller of nomes_json answers with; the panel translates them.
# Os códigos estáveis com que quem chama nomes_json responde; o painel os traduz.
NOMES_DEMAIS = "nomes_demais"
NOMES_LONGOS = "nomes_longos"
NOME_NAO_GRAVAVEL = "nome_nao_gravavel"
CODIGOS_DE_NOMES = (NOMES_DEMAIS, NOMES_LONGOS, NOME_NAO_GRAVAVEL)


class Tipo(StrEnum):
    """The type of the data point on the platform; the words are the ones section 8 uses.

    O tipo do data point na plataforma; as palavras são as que a seção 8 usa.
    """

    VALOR = "value"
    BOOL = "bool"
    ENUM = "enum"
    TEXTO = "string"


class Sentido(StrEnum):
    """RW travels both ways, ENVIO is only received and REPORTE is only published.

    ENVIO exists because the chip never echoes: a preset and a scene are commands, and
    reporting one back would publish a state that no device ever confirmed.

    RW viaja nos dois sentidos, ENVIO só é recebido e REPORTE só é publicado.

    ENVIO existe porque o chip nunca ecoa: um preset e uma cena são comandos, e reportar um
    de volta publicaria um estado que aparelho nenhum confirmou.
    """

    RW = "rw"
    ENVIO = "envio"
    REPORTE = "reporte"


@dataclass(frozen=True)
class Dp:
    """One data point of section 8; zona is 0 for the ones that are not part of a block.

    Um data point da seção 8; a zona é 0 para os que não fazem parte de um bloco.
    """

    dpid: int
    funcao: str
    zona: int
    tipo: Tipo
    sentido: Sentido
    valores: tuple[str, ...] = ()
    throttle_s: float = 0.0
    # Why: a track title arrives from a device and may be any length, so it is shortened to
    # the 255 bytes of the DP; a names JSON of the same type is refused instead, because a
    # shortened JSON is not JSON any more.
    # Por que: um título de faixa vem de um aparelho e pode ter qualquer tamanho, então ele é
    # encurtado para os 255 bytes do DP; um JSON de nomes do mesmo tipo é recusado, porque um
    # JSON encurtado deixa de ser JSON.
    texto_livre: bool = False

    @property
    def reportavel(self) -> bool:
        return self.sentido is not Sentido.ENVIO

    @property
    def ajustavel(self) -> bool:
        return self.sentido is not Sentido.REPORTE


class NomesInvalidos(ValueError):
    """Carries the stable code the caller answers with, so no route invents one of its own.

    Carrega o código estável com que quem chamou responde, para nenhuma rota inventar um.
    """

    def __init__(self, codigo: str, detalhe: str) -> None:
        self.codigo = codigo
        super().__init__(f"{codigo}: {detalhe}")


_BLOCO: dict[str, dict] = {
    "volume": {"tipo": Tipo.VALOR, "sentido": Sentido.RW},
    "play": {"tipo": Tipo.BOOL, "sentido": Sentido.RW},
    "preset": {"tipo": Tipo.ENUM, "sentido": Sentido.ENVIO, "valores": VALORES_PRESET},
    "online": {"tipo": Tipo.BOOL, "sentido": Sentido.REPORTE},
    "tocando": {
        "tipo": Tipo.TEXTO,
        "sentido": Sentido.REPORTE,
        "throttle_s": THROTTLE_TOCANDO_S,
        "texto_livre": True,
    },
}

# The compact key each names DP carries, and how many names fit in it at most.
# A chave compacta que cada DP de nomes carrega, e quantos nomes cabem nele no máximo.
CHAVE_DE_NOMES = {NOMES_ZONAS: "z", NOMES_CENAS: "c", NOMES_GRUPOS: "g"}
QUANTIDADE_DE_NOMES = {NOMES_ZONAS: ZONAS, NOMES_CENAS: CENAS, NOMES_GRUPOS: GRUPOS}


def _tabela() -> tuple[Dp, ...]:
    """Section 8 written once: every number below comes from the table of the document.

    A seção 8 escrita uma vez: todo número abaixo vem da tabela do documento.
    """
    dps = []
    for zona in range(1, ZONAS + 1):
        base = ZONA_BASE + ZONA_PASSO * (zona - 1)
        for posicao, funcao in enumerate(FUNCOES_DO_BLOCO):
            dps.append(Dp(dpid=base + posicao, funcao=funcao, zona=zona, **_BLOCO[funcao]))
        dps.append(
            Dp(
                dpid=ENTRADA_BASE + zona,
                funcao=FUNCAO_ENTRADA,
                zona=zona,
                tipo=Tipo.ENUM,
                sentido=Sentido.RW,
            )
        )
    dps += [
        Dp(CENA, "cena", 0, Tipo.ENUM, Sentido.ENVIO, VALORES_CENA),
        Dp(GRUPO, "grupo", 0, Tipo.ENUM, Sentido.RW, VALORES_GRUPO),
        Dp(NOMES_ZONAS, "nomes_zonas", 0, Tipo.TEXTO, Sentido.REPORTE),
        Dp(NOMES_CENAS, "nomes_cenas", 0, Tipo.TEXTO, Sentido.REPORTE),
        Dp(NOMES_GRUPOS, "nomes_grupos", 0, Tipo.TEXTO, Sentido.REPORTE),
    ]
    return tuple(sorted(dps, key=lambda dp: dp.dpid))


DPS = _tabela()
MAPA = {dp.dpid: dp for dp in DPS}
_POR_FUNCAO = {(dp.zona, dp.funcao): dp.dpid for dp in DPS}

REPORTAVEIS = tuple(dp.dpid for dp in DPS if dp.reportavel)
AJUSTAVEIS = tuple(dp.dpid for dp in DPS if dp.ajustavel)


def dp_de(zona: int, funcao: str) -> int:
    """The number of one function, with zona 0 for a global one. Raises for a pair that is
    not in the table, because the caller built it from our own configuration and not from
    the wire.

    O número de uma função, com a zona 0 para uma global. Estoura para um par fora da tabela,
    porque quem chama o montou da nossa própria configuração e não do fio.
    """
    dpid = _POR_FUNCAO.get((zona, funcao))
    if dpid is None:
        raise ValueError(f"section 8 has no data point for zona {zona!r} and funcao {funcao!r}")
    return dpid


def de_dp(dpid: object) -> Dp | None:
    """The data point of a number, or None for anything the contract does not name.

    Takes any object because the number arrives from a client frame or from a saved scene,
    and the JSON true is an int for Python while it is not a data point for anybody.

    O data point de um número, ou None para o que o contrato não nomeia.

    Aceita qualquer objeto porque o número chega de um quadro de cliente ou de uma cena
    salva, e o true do JSON é int para o Python enquanto não é data point para ninguém.
    """
    if type(dpid) is not int:
        return None
    return MAPA.get(dpid)


def vazio_de(dp: Dp) -> object | None:
    """What a data point reads as when its zone has no speaker, or None when it has no such
    reading.

    Why: a block whose equipment was removed stops producing values, and the last thing
    published about it would stand forever: a bridge showing a zone online, at some volume,
    playing something, with nothing behind it. An enum has no honest empty reading, so it is
    left alone rather than being given an invented one.

    O que um data point lê quando a zona dele não tem caixa, ou None quando ele não tem essa
    leitura.

    Por que: um bloco cujo equipamento foi removido para de produzir valores, e o último
    publicado a respeito ficaria valendo para sempre: uma ponte mostrando uma zona online, num
    volume, tocando algo, sem nada por trás. Um enum não tem leitura vazia honesta, então ele
    fica como está em vez de receber uma inventada.
    """
    if not dp.zona:
        return None
    if dp.tipo is Tipo.BOOL:
        return False
    if dp.tipo is Tipo.TEXTO:
        return ""
    if dp.tipo is Tipo.VALOR:
        return 0
    return None


def da_zona(zona: int) -> tuple[Dp, ...]:
    return tuple(dp for dp in DPS if dp.zona == zona)


def valores_de_enum(valores: Iterable[str]) -> tuple[str, ...]:
    """The values an enum DP may really offer: unique, non empty and at most ten of them.

    Os valores que um DP enum pode mesmo oferecer: únicos, não vazios e no máximo dez.
    """
    # Why: the inputs of a device come from the hardware (section 14, plm_support) and a
    # device with eleven of them would build an enum the platform refuses whole, which would
    # take the input of that zone off the bus instead of taking one input off the list.
    # Por que: as entradas de um aparelho vêm do hardware (seção 14, plm_support) e um
    # aparelho com onze delas montaria um enum que a plataforma recusa inteiro, o que tiraria
    # a entrada daquela zona do barramento em vez de tirar uma entrada da lista.
    escolhidos: list[str] = []
    for valor in valores:
        if isinstance(valor, str) and valor and valor not in escolhidos:
            escolhidos.append(valor)
        if len(escolhidos) == ENUM_MAXIMO:
            break
    return tuple(escolhidos)


def nomes_json(dpid: int, nomes: Sequence[str]) -> str:
    """The compact JSON of DP 133, 134 or 135, or NomesInvalidos with a stable code.

    O JSON compacto do DP 133, 134 ou 135, ou NomesInvalidos com um código estável.
    """
    chave = CHAVE_DE_NOMES.get(dpid)
    if chave is None:
        raise ValueError(f"dp {dpid!r} is not one of the names data points of section 8")
    lista = list(nomes)
    if not all(isinstance(nome, str) for nome in lista):
        raise ValueError(f"dp {dpid}: every name must be a string, found {lista!r}")
    limite = QUANTIDADE_DE_NOMES[dpid]
    if len(lista) > limite:
        raise NomesInvalidos(NOMES_DEMAIS, f"dp {dpid} carries at most {limite} names")
    # Why: ensure_ascii would write an accented letter as six bytes, so a Portuguese name
    # would eat the budget of the DP for nothing; the frame is UTF-8 all the way.
    # Por que: o ensure_ascii escreveria uma letra acentuada em seis bytes, então um nome em
    # português comeria o orçamento do DP à toa; o quadro é UTF-8 do começo ao fim.
    texto = json.dumps({chave: lista}, ensure_ascii=False, separators=(",", ":"))
    tamanho = _tamanho_em_bytes(texto)
    if tamanho is None:
        raise NomesInvalidos(NOME_NAO_GRAVAVEL, f"dp {dpid} holds a name UTF-8 cannot write")
    if tamanho > TEXTO_MAXIMO_BYTES:
        raise NomesInvalidos(
            NOMES_LONGOS,
            f"dp {dpid} would carry {tamanho} bytes, the ceiling is {TEXTO_MAXIMO_BYTES}",
        )
    return texto


def nomes_cabem(dpid: int, nomes: Sequence[str]) -> bool:
    """True when nomes_json would answer, so a route validates a name before saving it.

    Verdadeiro quando o nomes_json responderia, para uma rota validar um nome antes de gravar.
    """
    try:
        nomes_json(dpid, nomes)
    except NomesInvalidos:
        return False
    return True


def texto_de_dp(texto: str) -> str:
    """A free text reading inside the 255 bytes of a string DP, never cut inside a character.

    Uma leitura de texto livre dentro dos 255 bytes de um DP string, nunca cortada dentro de
    um caractere.
    """
    # Why: a lone surrogate is the one thing a str holds that UTF-8 cannot write, and a title
    # that carried one would raise on the way out of the socket instead of reaching the
    # bridge shortened; what a device sent is data, and it never breaks the bus.
    # Por que: um surrogado solto é a única coisa que um str guarda e o UTF-8 não escreve, e
    # um título que levasse um estouraria na saída do socket em vez de chegar encurtado à
    # ponte; o que um aparelho mandou é dado, e ele nunca quebra o barramento.
    bruto = texto.encode("utf-8", errors="ignore")
    if len(bruto) <= TEXTO_MAXIMO_BYTES:
        return bruto.decode("utf-8")
    return bruto[:TEXTO_MAXIMO_BYTES].decode("utf-8", errors="ignore")


def _tamanho_em_bytes(texto: str) -> int | None:
    try:
        return len(texto.encode("utf-8"))
    except UnicodeEncodeError:
        return None
