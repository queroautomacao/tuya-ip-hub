# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The two products, the numbering of their data points, the direction, the
report class and the names.

This module is the only place the numbers of the contract exist, and it opens no socket and
knows no driver: the bus, the panel routes and a scene all read the same tables instead of
each one carrying a number of its own.

Two products, one licence each: `ar` (air conditioning, eight machines, four data points
each) and `av` (audio and video, twelve pieces of equipment, two data points each). What an
automation or a voice assistant may reach is a bool, a value or an enum, so everything of
that kind sits in its own data point; what only the panel reads travels packed in strings.

The platform rules that shape the tables: the chip never echoes a received
data point, so a send only one is never reported; a custom enum takes at most ten values; a
string carries at most 255 bytes, which is why the names, the profiles and the titles are
measured before they are published; and a device is recommended to report at most 300 times
a day, which is why every data point carries a report class.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from iphub.drivers.manifesto import MODOS_AR, TEMPERATURA_MAXIMA, TEMPERATURA_MINIMA, VENTOS

PRODUTO_AR = "ar"
PRODUTO_AV = "av"
PRODUTOS = (PRODUTO_AR, PRODUTO_AV)

# How many numbers each product carries; the app of the customer counts from 1.
NUMEROS = {PRODUTO_AR: 8, PRODUTO_AV: 12}

# The scene is a value and not an enum, because an enum stops at ten and a house has
# more scenes than that; 32 keeps the two name strings at sixteen names each.
CENAS = 32
NOMES_POR_DP = 16

ENUM_MAXIMO = 10
TEXTO_MAXIMO_BYTES = 255
VALOR_MINIMO = 0
VALOR_MAXIMO = 100

# Twelve titles share one string of 255 bytes, so each one gets this many characters;
# a title is read on the panel and not in a scene, and the first words are what identify it.
TITULO_MAXIMO = 18

# A profile is a line of the packed strings, and the whole set of twelve
# has to fit five of them; this ceiling per profile is what makes the packing always succeed.
PERFIL_MAXIMO_BYTES = 200
PERFIS_DPS = 5

SEPARADOR = ";"

# The remote door, on the SAME two numbers in both products, so the panel of the app has one
# piece of code for it and not one per product. The bool is what the integrator presses and
# the value is what he reads: how many minutes are left of the window.
DP_REMOTO = 180
DP_REMOTO_RESTANTE = 181
DP_REMOTO_CODIGO = 182
DP_REMOTO_URL = 183
DP_REMOTO_HOME = 184
F_REMOTO = "acesso_remoto"
F_REMOTO_RESTANTE = "acesso_remoto_restante"
F_REMOTO_CODIGO = "acesso_remoto_codigo"
F_REMOTO_URL = "acesso_remoto_url"
F_REMOTO_HOME = "acesso_remoto_home"
JANELA_EM_MINUTOS = 24 * 60

# The report policy, in numbers: what the platform recommends per day, and the
# count at which the bus tightens the windows so the recommendation is never reached.
REPORTS_POR_DIA = 300
AVISO_DO_DIA = 250
JANELA_APERTADA_S = 30.0

# The stable codes a caller answers with when names do not fit; the panel translates them.
NOMES_DEMAIS = "nomes_demais"
NOMES_LONGOS = "nomes_longos"
NOME_NAO_GRAVAVEL = "nome_nao_gravavel"
PERFIS_LONGOS = "perfis_longos"
CODIGOS_DE_NOMES = (NOMES_DEMAIS, NOMES_LONGOS, NOME_NAO_GRAVAVEL, PERFIS_LONGOS)

CHAVE_NOMES_MAQUINAS = "m"
CHAVE_NOMES_CENAS = "c"

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


class Tipo(StrEnum):
    """The type of the data point on the platform; the words are the ones uses."""

    VALOR = "value"
    BOOL = "bool"
    ENUM = "enum"
    TEXTO = "string"


class Sentido(StrEnum):
    """RW travels both ways, ENVIO is only received and REPORTE is only published.

    ENVIO exists because the chip never echoes: a scene and a command are orders, and
    reporting one back would publish a state that no device ever confirmed.
    """

    RW = "rw"
    ENVIO = "envio"
    REPORTE = "reporte"


class Classe(StrEnum):
    """The report class : A is state the app must see now, B is context, C is
    informative and only leaves when a registration changes or when the bridge asks.
    """

    A = "a"
    B = "b"
    C = "c"


JANELAS_S = {Classe.A: 2.0, Classe.B: 10.0, Classe.C: 0.0}


@dataclass(frozen=True)
class Dp:
    """One data point ; numero is 0 for the ones that belong to the whole
    installation, and indice tells apart the parts of a string spread over several of them.
    """

    dpid: int
    produto: str
    funcao: str
    tipo: Tipo
    sentido: Sentido
    classe: Classe = Classe.A
    numero: int = 0
    indice: int = 0
    valores: tuple[str, ...] = ()
    minimo: int = VALOR_MINIMO
    maximo: int = VALOR_MAXIMO
    # A title changes with every track, and pushing it would be the one second sensor
    # of this product; it answers the query of the panel and is never pushed.
    empurrado: bool = True

    @property
    def reportavel(self) -> bool:
        return self.sentido is not Sentido.ENVIO

    @property
    def ajustavel(self) -> bool:
        return self.sentido is not Sentido.REPORTE

    @property
    def janela_s(self) -> float:
        return JANELAS_S[self.classe]


class NomesInvalidos(ValueError):
    """Carries the stable code the caller answers with, so no route invents one of its own."""

    def __init__(self, codigo: str, detalhe: str) -> None:
        self.codigo = codigo
        super().__init__(f"{codigo}: {detalhe}")


def _bits(produto: str) -> int:
    return (1 << NUMEROS[produto]) - 1


def _tabela_do_remoto(produto: str) -> tuple[Dp, ...]:
    """The remote door of the installation, which belongs to no number of no product."""
    return (
        Dp(DP_REMOTO, produto, F_REMOTO, Tipo.BOOL, Sentido.RW),
        # The window shrinks by one every minute, and a report per minute for a whole day is
        # nowhere near the budget of the platform; class B keeps it honest anyway.
        Dp(
            DP_REMOTO_RESTANTE,
            produto,
            F_REMOTO_RESTANTE,
            Tipo.VALOR,
            Sentido.REPORTE,
            Classe.B,
            maximo=JANELA_EM_MINUTOS,
        ),
        # The code of the window, for the app to show to whoever armed it. It changes once
        # a day at most, so it is the quietest class there is.
        Dp(DP_REMOTO_CODIGO, produto, F_REMOTO_CODIGO, Tipo.TEXTO, Sentido.REPORTE, Classe.C),
        # Where to go: the relay hands the hub a new address every window, and this is the
        # one place the integrator can read it without being on the local network.
        Dp(DP_REMOTO_URL, produto, F_REMOTO_URL, Tipo.TEXTO, Sentido.REPORTE, Classe.C),
        # The home of the app this installation belongs to, written by the miniApp of the
        # maker once, and what the relay checks against the registry of the maker.
        Dp(DP_REMOTO_HOME, produto, F_REMOTO_HOME, Tipo.TEXTO, Sentido.RW, Classe.C),
    )


def _tabela_ar() -> tuple[Dp, ...]:
    """The product of the air conditioners: machine k starts at 101 + 5(k - 1)."""
    dps = []
    for k in range(1, NUMEROS[PRODUTO_AR] + 1):
        base = 101 + 5 * (k - 1)
        dps += [
            Dp(base, PRODUTO_AR, "ligado", Tipo.BOOL, Sentido.RW, numero=k),
            Dp(
                base + 1,
                PRODUTO_AR,
                "temperatura",
                Tipo.VALOR,
                Sentido.RW,
                numero=k,
                minimo=TEMPERATURA_MINIMA,
                maximo=TEMPERATURA_MAXIMA,
            ),
            Dp(base + 2, PRODUTO_AR, "modo", Tipo.ENUM, Sentido.RW, numero=k, valores=MODOS_AR),
            Dp(base + 3, PRODUTO_AR, "vento", Tipo.ENUM, Sentido.RW, numero=k, valores=VENTOS),
        ]
    dps += [
        Dp(171, PRODUTO_AR, "cena", Tipo.VALOR, Sentido.ENVIO, minimo=1, maximo=CENAS),
        Dp(172, PRODUTO_AR, "online", Tipo.VALOR, Sentido.REPORTE, maximo=_bits(PRODUTO_AR)),
        Dp(173, PRODUTO_AR, "nomes", Tipo.TEXTO, Sentido.REPORTE, Classe.C),
        Dp(174, PRODUTO_AR, "nomes_cenas", Tipo.TEXTO, Sentido.REPORTE, Classe.C, indice=1),
        Dp(175, PRODUTO_AR, "nomes_cenas", Tipo.TEXTO, Sentido.REPORTE, Classe.C, indice=2),
        *_tabela_do_remoto(PRODUTO_AR),
    ]
    return tuple(dps)


def _tabela_av() -> tuple[Dp, ...]:
    """The product of the audio and video: ligado at 100 + n, nivel at 120 + n."""
    dps = []
    for n in range(1, NUMEROS[PRODUTO_AV] + 1):
        dps.append(Dp(100 + n, PRODUTO_AV, "ligado", Tipo.BOOL, Sentido.RW, numero=n))
    for n in range(1, NUMEROS[PRODUTO_AV] + 1):
        dps.append(Dp(120 + n, PRODUTO_AV, "nivel", Tipo.VALOR, Sentido.RW, numero=n))
    bits = _bits(PRODUTO_AV)
    dps += [
        Dp(141, PRODUTO_AV, "cena", Tipo.VALOR, Sentido.ENVIO, minimo=1, maximo=CENAS),
        Dp(142, PRODUTO_AV, "grupo", Tipo.VALOR, Sentido.RW, maximo=NUMEROS[PRODUTO_AV]),
        Dp(143, PRODUTO_AV, "comando", Tipo.TEXTO, Sentido.ENVIO),
        Dp(144, PRODUTO_AV, "online", Tipo.VALOR, Sentido.REPORTE, maximo=bits),
        Dp(145, PRODUTO_AV, "mudos", Tipo.VALOR, Sentido.REPORTE, Classe.B, maximo=bits),
        Dp(146, PRODUTO_AV, "entradas", Tipo.TEXTO, Sentido.REPORTE, Classe.B),
        Dp(147, PRODUTO_AV, "modos", Tipo.TEXTO, Sentido.REPORTE, Classe.B),
        Dp(148, PRODUTO_AV, "titulos", Tipo.TEXTO, Sentido.REPORTE, Classe.C, empurrado=False),
    ]
    for indice in range(1, PERFIS_DPS + 1):
        dps.append(
            Dp(
                148 + indice,
                PRODUTO_AV,
                "perfis",
                Tipo.TEXTO,
                Sentido.REPORTE,
                Classe.C,
                indice=indice,
            )
        )
    dps += [
        Dp(154, PRODUTO_AV, "nomes_cenas", Tipo.TEXTO, Sentido.REPORTE, Classe.C, indice=1),
        Dp(155, PRODUTO_AV, "nomes_cenas", Tipo.TEXTO, Sentido.REPORTE, Classe.C, indice=2),
        *_tabela_do_remoto(PRODUTO_AV),
    ]
    return tuple(dps)


DPS: dict[str, tuple[Dp, ...]] = {PRODUTO_AR: _tabela_ar(), PRODUTO_AV: _tabela_av()}
MAPAS: dict[str, dict[int, Dp]] = {
    produto: {dp.dpid: dp for dp in dps} for produto, dps in DPS.items()
}
_POR_FUNCAO: dict[tuple[str, str, int, int], int] = {
    (dp.produto, dp.funcao, dp.numero, dp.indice): dp.dpid for dps in DPS.values() for dp in dps
}


def tabela(produto: str) -> tuple[Dp, ...]:
    """Every data point of one product, in the order."""
    return DPS[produto]


def reportaveis(produto: str) -> tuple[int, ...]:
    return tuple(dp.dpid for dp in DPS[produto] if dp.reportavel)


def de_dp(produto: object, dpid: object) -> Dp | None:
    """The data point of a number in one product, or None for anything the contract does
    not name.

    Takes any object because the number arrives from a client frame or from a saved file,
    and the JSON true is an int for Python while it is not a data point for anybody.
    """
    if type(dpid) is not int or not isinstance(produto, str) or produto not in MAPAS:
        return None
    return MAPAS[produto].get(dpid)


def dp_de(produto: str, funcao: str, numero: int = 0, indice: int = 0) -> int:
    """The number of one function. Raises for a combination that is not in the table,
    because the caller built it from our own configuration and not from the wire.
    """
    dpid = _POR_FUNCAO.get((produto, funcao, numero, indice))
    if dpid is None:
        raise ValueError(
            f"there is no data point for produto {produto!r}, funcao {funcao!r}, "
            f"numero {numero!r}, indice {indice!r}"
        )
    return dpid


def dps_de(produto: str, funcao: str) -> tuple[Dp, ...]:
    """Every data point of one function of one product, in the order of the numbers."""
    return tuple(dp for dp in DPS[produto] if dp.funcao == funcao)


def numero_de_cena(valor: object) -> int | None:
    """The scene number a scene data point carries, or None for anything outside 1..32."""
    if type(valor) is not int or not 1 <= valor <= CENAS:
        return None
    return valor


def bits(numeros: Iterable[int]) -> int:
    """One bit per number, number n at bit n - 1, which is how online and muted travel."""
    valor = 0
    for numero in numeros:
        if numero >= 1:
            valor |= 1 << (numero - 1)
    return valor


def pares(valores: Mapping[int, int]) -> str:
    """The active input or mode of every number that has one, as n=k joined by ';'."""
    return SEPARADOR.join(f"{numero}={valores[numero]}" for numero in sorted(valores))


def titulos(valores: Mapping[int, str]) -> str:
    """The title of what plays on every number, each inside its characters and all of them
    inside the 255 bytes, dropping from the last number when they do not fit.
    """
    itens = [f"{numero}={_titulo(valores[numero])}" for numero in sorted(valores)]
    while itens:
        texto = SEPARADOR.join(itens)
        if len(texto.encode("utf-8", errors="ignore")) <= TEXTO_MAXIMO_BYTES:
            return texto
        itens.pop()
    return ""


def _titulo(texto: str) -> str:
    # The title is what a device answered, and a lone surrogate in it must never reach
    # the socket of the bridge, so what UTF-8 cannot write is dropped before the cut.
    limpo = _CONTROLE.sub("", texto).replace(SEPARADOR, " ").replace("=", " ").strip()
    return limpo.encode("utf-8", errors="ignore").decode("utf-8")[:TITULO_MAXIMO]


def empacotar(perfis: Sequence[str], partes: int = PERFIS_DPS) -> tuple[str, ...]:
    """The profiles spread over the strings, filled in order, each inside 255
    bytes; NomesInvalidos when they do not fit, because a cut profile is no profile.
    """
    saida: list[str] = []
    atual = ""
    for perfil in perfis:
        if SEPARADOR in perfil or _tamanho(perfil) is None:
            raise NomesInvalidos(PERFIS_LONGOS, "a profile carries a separator or a surrogate")
        candidato = perfil if not atual else atual + SEPARADOR + perfil
        if len(candidato.encode("utf-8")) <= TEXTO_MAXIMO_BYTES:
            atual = candidato
            continue
        if atual:
            saida.append(atual)
        if len(perfil.encode("utf-8")) > TEXTO_MAXIMO_BYTES:
            raise NomesInvalidos(PERFIS_LONGOS, "one profile alone is longer than a string")
        atual = perfil
    if atual:
        saida.append(atual)
    if len(saida) > partes:
        raise NomesInvalidos(
            PERFIS_LONGOS, f"the profiles need {len(saida)} strings, only {partes} exist"
        )
    return tuple(saida + [""] * (partes - len(saida)))


def desempacotar(partes: Iterable[str]) -> tuple[str, ...]:
    """The profiles back from the strings, which is what the panel does before reading them."""
    perfis: list[str] = []
    for parte in partes:
        if parte:
            perfis.extend(parte.split(SEPARADOR))
    return tuple(perfis)


def nomes_json(chave: str, nomes: Sequence[str], limite: int) -> str:
    """The compact JSON of a names string, or NomesInvalidos with a stable code."""
    lista = list(nomes)
    if not all(isinstance(nome, str) for nome in lista):
        raise ValueError(f"every name must be a string, found {lista!r}")
    if len(lista) > limite:
        raise NomesInvalidos(NOMES_DEMAIS, f"a names string carries at most {limite} names")
    # Ensure_ascii would write an accented letter as six bytes, so a Portuguese name
    # would eat the budget of the DP for nothing; the frame is UTF-8 all the way.
    texto = json.dumps({chave: lista}, ensure_ascii=False, separators=(",", ":"))
    tamanho = _tamanho(texto)
    if tamanho is None:
        raise NomesInvalidos(NOME_NAO_GRAVAVEL, "a name holds a character UTF-8 cannot write")
    if tamanho > TEXTO_MAXIMO_BYTES:
        raise NomesInvalidos(
            NOMES_LONGOS,
            f"the names would carry {tamanho} bytes, the ceiling is {TEXTO_MAXIMO_BYTES}",
        )
    return texto


def nomes_das_cenas(nomes: Sequence[str]) -> tuple[str, str]:
    """The names of the 32 scenes as the two strings, sixteen names each."""
    lista = list(nomes)
    if len(lista) > CENAS:
        raise NomesInvalidos(NOMES_DEMAIS, f"there are {CENAS} scenes")
    primeira = nomes_json(CHAVE_NOMES_CENAS, lista[:NOMES_POR_DP], NOMES_POR_DP)
    segunda = nomes_json(CHAVE_NOMES_CENAS, lista[NOMES_POR_DP:], NOMES_POR_DP)
    return primeira, segunda


def nomes_das_maquinas(nomes: Sequence[str]) -> str:
    return nomes_json(CHAVE_NOMES_MAQUINAS, nomes, NUMEROS[PRODUTO_AR])


def nomes_cabem(nomes: Sequence[str]) -> bool:
    """True when the scene names would publish, so a route validates before saving."""
    try:
        nomes_das_cenas(nomes)
    except NomesInvalidos:
        return False
    return True


def texto_de_dp(texto: str) -> str:
    """A free text reading inside the 255 bytes of a string DP, never cut inside a character."""
    # A lone surrogate is the one thing a str holds that UTF-8 cannot write, and a text
    # that carried one would raise on the way out of the socket instead of reaching the
    # bridge shortened; what a device sent is data, and it never breaks the bus.
    bruto = texto.encode("utf-8", errors="ignore")
    if len(bruto) <= TEXTO_MAXIMO_BYTES:
        return bruto.decode("utf-8")
    return bruto[:TEXTO_MAXIMO_BYTES].decode("utf-8", errors="ignore")


def _tamanho(texto: str) -> int | None:
    try:
        return len(texto.encode("utf-8"))
    except UnicodeEncodeError:
        return None
