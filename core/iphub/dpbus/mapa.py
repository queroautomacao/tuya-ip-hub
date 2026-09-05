# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8: the two products, the numbering of their data points, the direction, the
report class and the names.

This module is the only place the numbers of the contract exist, and it opens no socket and
knows no driver: the bus, the panel routes and a scene all read the same tables instead of
each one carrying a number of its own.

Two products, one licence each: `ar` (air conditioning, eight machines, four data points
each) and `av` (audio and video, twelve pieces of equipment, two data points each). What an
automation or a voice assistant may reach is a bool, a value or an enum, so everything of
that kind sits in its own data point; what only the panel reads travels packed in strings.

The platform rules that shape the tables (section 14): the chip never echoes a received
data point, so a send only one is never reported; a custom enum takes at most ten values; a
string carries at most 255 bytes, which is why the names, the profiles and the titles are
measured before they are published; and a device is recommended to report at most 300 times
a day, which is why every data point carries a report class.

Seção 8: os dois produtos, a numeração dos data points deles, o sentido, a classe de report e
os nomes.

Este módulo é o único lugar onde os números do contrato existem, e ele não abre socket e não
conhece driver: o barramento, as rotas do painel e uma cena leem as mesmas tabelas em vez de
cada um carregar um número próprio.

Dois produtos, uma licença cada: `ar` (ar condicionado, oito máquinas, quatro data points
cada) e `av` (áudio e vídeo, doze equipamentos, dois data points cada). O que uma automação ou
uma assistente de voz alcança é bool, valor ou enum, então tudo desse tipo fica num data point
próprio; o que só o painel lê viaja empacotado em strings.

As regras de plataforma que moldam as tabelas (seção 14): o chip nunca ecoa um data point
recebido, então um de só envio nunca é reportado; um enum customizado aceita no máximo dez
valores; uma string carrega no máximo 255 bytes, e por isso os nomes, os perfis e os títulos
são medidos antes de serem publicados; e um dispositivo deve reportar no máximo 300 vezes por
dia, e por isso todo data point carrega uma classe de report.
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
# Quantos números cada produto carrega; o app do cliente conta a partir de 1.
NUMEROS = {PRODUTO_AR: 8, PRODUTO_AV: 12}

# Why: the scene is a value and not an enum, because an enum stops at ten and a house has
# more scenes than that; 32 keeps the two name strings at sixteen names each.
# Por que: a cena é um valor e não um enum, porque um enum para em dez e uma casa tem mais
# cenas do que isso; 32 mantém as duas strings de nomes em dezesseis nomes cada.
CENAS = 32
NOMES_POR_DP = 16

ENUM_MAXIMO = 10
TEXTO_MAXIMO_BYTES = 255
VALOR_MINIMO = 0
VALOR_MAXIMO = 100

# Why: twelve titles share one string of 255 bytes, so each one gets this many characters;
# a title is read on the panel and not in a scene, and the first words are what identify it.
# Por que: doze títulos dividem uma string de 255 bytes, então cada um recebe estes
# caracteres; um título é lido no painel e não numa cena, e as primeiras palavras o identificam.
TITULO_MAXIMO = 18

# Why: a profile is a line of the packed strings of section 8, and the whole set of twelve
# has to fit five of them; this ceiling per profile is what makes the packing always succeed.
# Por que: um perfil é uma linha das strings empacotadas da seção 8, e o conjunto de doze
# precisa caber em cinco delas; este teto por perfil é o que faz o empacotamento sempre caber.
PERFIL_MAXIMO_BYTES = 200
PERFIS_DPS = 5

SEPARADOR = ";"

# The report policy of section 8, in numbers: what the platform recommends per day, and the
# count at which the bus tightens the windows so the recommendation is never reached.
# A política de reports da seção 8, em números: o que a plataforma recomenda por dia, e a
# contagem em que o barramento aperta as janelas para a recomendação nunca ser alcançada.
REPORTS_POR_DIA = 300
AVISO_DO_DIA = 250
JANELA_APERTADA_S = 30.0

# The stable codes a caller answers with when names do not fit; the panel translates them.
# Os códigos estáveis com que quem chama responde quando nomes não cabem; o painel os traduz.
NOMES_DEMAIS = "nomes_demais"
NOMES_LONGOS = "nomes_longos"
NOME_NAO_GRAVAVEL = "nome_nao_gravavel"
PERFIS_LONGOS = "perfis_longos"
CODIGOS_DE_NOMES = (NOMES_DEMAIS, NOMES_LONGOS, NOME_NAO_GRAVAVEL, PERFIS_LONGOS)

CHAVE_NOMES_MAQUINAS = "m"
CHAVE_NOMES_CENAS = "c"

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


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

    ENVIO exists because the chip never echoes: a scene and a command are orders, and
    reporting one back would publish a state that no device ever confirmed.

    RW viaja nos dois sentidos, ENVIO só é recebido e REPORTE só é publicado.

    ENVIO existe porque o chip nunca ecoa: uma cena e um comando são ordens, e reportar um de
    volta publicaria um estado que aparelho nenhum confirmou.
    """

    RW = "rw"
    ENVIO = "envio"
    REPORTE = "reporte"


class Classe(StrEnum):
    """The report class of section 8: A is state the app must see now, B is context, C is
    informative and only leaves when a registration changes or when the bridge asks.

    A classe de report da seção 8: A é estado que o app precisa ver agora, B é contexto, C é
    informativo e só sai quando um cadastro muda ou quando a ponte pergunta.
    """

    A = "a"
    B = "b"
    C = "c"


JANELAS_S = {Classe.A: 2.0, Classe.B: 10.0, Classe.C: 0.0}


@dataclass(frozen=True)
class Dp:
    """One data point of section 8; numero is 0 for the ones that belong to the whole
    installation, and indice tells apart the parts of a string spread over several of them.

    Um data point da seção 8; o numero é 0 para os que são da instalação inteira, e o indice
    distingue as partes de uma string espalhada por vários deles.
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
    # Why: a title changes with every track, and pushing it would be the one second sensor
    # of this product; it answers the query of the panel and is never pushed.
    # Por que: um título muda a cada faixa, e empurrá-lo seria o sensor de um segundo deste
    # produto; ele responde à consulta do painel e nunca é empurrado.
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
    """Carries the stable code the caller answers with, so no route invents one of its own.

    Carrega o código estável com que quem chamou responde, para nenhuma rota inventar um.
    """

    def __init__(self, codigo: str, detalhe: str) -> None:
        self.codigo = codigo
        super().__init__(f"{codigo}: {detalhe}")


def _bits(produto: str) -> int:
    return (1 << NUMEROS[produto]) - 1


def _tabela_ar() -> tuple[Dp, ...]:
    """The product of the air conditioners: machine k starts at 101 + 5(k - 1).

    O produto dos ares condicionados: a máquina k começa em 101 + 5(k - 1).
    """
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
    ]
    return tuple(dps)


def _tabela_av() -> tuple[Dp, ...]:
    """The product of the audio and video: ligado at 100 + n, nivel at 120 + n.

    O produto de áudio e vídeo: ligado em 100 + n, nível em 120 + n.
    """
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
    """Every data point of one product, in the order of section 8.

    Todo data point de um produto, na ordem da seção 8.
    """
    return DPS[produto]


def reportaveis(produto: str) -> tuple[int, ...]:
    return tuple(dp.dpid for dp in DPS[produto] if dp.reportavel)


def de_dp(produto: object, dpid: object) -> Dp | None:
    """The data point of a number in one product, or None for anything the contract does
    not name.

    Takes any object because the number arrives from a client frame or from a saved file,
    and the JSON true is an int for Python while it is not a data point for anybody.

    O data point de um número num produto, ou None para o que o contrato não nomeia.

    Aceita qualquer objeto porque o número chega de um quadro de cliente ou de um arquivo
    salvo, e o true do JSON é int para o Python enquanto não é data point para ninguém.
    """
    if type(dpid) is not int or not isinstance(produto, str) or produto not in MAPAS:
        return None
    return MAPAS[produto].get(dpid)


def dp_de(produto: str, funcao: str, numero: int = 0, indice: int = 0) -> int:
    """The number of one function. Raises for a combination that is not in the table,
    because the caller built it from our own configuration and not from the wire.

    O número de uma função. Estoura para uma combinação fora da tabela, porque quem chama a
    montou da nossa própria configuração e não do fio.
    """
    dpid = _POR_FUNCAO.get((produto, funcao, numero, indice))
    if dpid is None:
        raise ValueError(
            f"section 8 has no data point for produto {produto!r}, funcao {funcao!r}, "
            f"numero {numero!r}, indice {indice!r}"
        )
    return dpid


def dps_de(produto: str, funcao: str) -> tuple[Dp, ...]:
    """Every data point of one function of one product, in the order of the numbers.

    Todo data point de uma função de um produto, na ordem dos números.
    """
    return tuple(dp for dp in DPS[produto] if dp.funcao == funcao)


def numero_de_cena(valor: object) -> int | None:
    """The scene number a scene data point carries, or None for anything outside 1..32.

    O número de cena que um data point de cena carrega, ou None para o que está fora de 1..32.
    """
    if type(valor) is not int or not 1 <= valor <= CENAS:
        return None
    return valor


def bits(numeros: Iterable[int]) -> int:
    """One bit per number, number n at bit n - 1, which is how online and muted travel.

    Um bit por número, o número n no bit n - 1, que é como online e mudo viajam.
    """
    valor = 0
    for numero in numeros:
        if numero >= 1:
            valor |= 1 << (numero - 1)
    return valor


def pares(valores: Mapping[int, int]) -> str:
    """The active input or mode of every number that has one, as n=k joined by ';'.

    A entrada ou o modo ativo de todo número que tem um, como n=k unidos por ';'.
    """
    return SEPARADOR.join(f"{numero}={valores[numero]}" for numero in sorted(valores))


def titulos(valores: Mapping[int, str]) -> str:
    """The title of what plays on every number, each inside its characters and all of them
    inside the 255 bytes, dropping from the last number when they do not fit.

    O título do que toca em cada número, cada um dentro dos caracteres dele e todos dentro
    dos 255 bytes, tirando do último número quando não cabem.
    """
    itens = [f"{numero}={_titulo(valores[numero])}" for numero in sorted(valores)]
    while itens:
        texto = SEPARADOR.join(itens)
        if len(texto.encode("utf-8", errors="ignore")) <= TEXTO_MAXIMO_BYTES:
            return texto
        itens.pop()
    return ""


def _titulo(texto: str) -> str:
    # Why: the title is what a device answered, and a lone surrogate in it must never reach
    # the socket of the bridge, so what UTF-8 cannot write is dropped before the cut.
    # Por que: o título é o que um aparelho respondeu, e um surrogado solto nele nunca pode
    # chegar ao socket da ponte, então o que o UTF-8 não escreve cai antes do corte.
    limpo = _CONTROLE.sub("", texto).replace(SEPARADOR, " ").replace("=", " ").strip()
    return limpo.encode("utf-8", errors="ignore").decode("utf-8")[:TITULO_MAXIMO]


def empacotar(perfis: Sequence[str], partes: int = PERFIS_DPS) -> tuple[str, ...]:
    """The profiles spread over the strings of section 8, filled in order, each inside 255
    bytes; NomesInvalidos when they do not fit, because a cut profile is no profile.

    Os perfis espalhados pelas strings da seção 8, preenchidas em ordem, cada uma dentro dos
    255 bytes; NomesInvalidos quando não cabem, porque um perfil cortado não é perfil.
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
    """The profiles back from the strings, which is what the panel does before reading them.

    Os perfis de volta das strings, que é o que o painel faz antes de os ler.
    """
    perfis: list[str] = []
    for parte in partes:
        if parte:
            perfis.extend(parte.split(SEPARADOR))
    return tuple(perfis)


def nomes_json(chave: str, nomes: Sequence[str], limite: int) -> str:
    """The compact JSON of a names string, or NomesInvalidos with a stable code.

    O JSON compacto de uma string de nomes, ou NomesInvalidos com um código estável.
    """
    lista = list(nomes)
    if not all(isinstance(nome, str) for nome in lista):
        raise ValueError(f"every name must be a string, found {lista!r}")
    if len(lista) > limite:
        raise NomesInvalidos(NOMES_DEMAIS, f"a names string carries at most {limite} names")
    # Why: ensure_ascii would write an accented letter as six bytes, so a Portuguese name
    # would eat the budget of the DP for nothing; the frame is UTF-8 all the way.
    # Por que: o ensure_ascii escreveria uma letra acentuada em seis bytes, então um nome em
    # português comeria o orçamento do DP à toa; o quadro é UTF-8 do começo ao fim.
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
    """The names of the 32 scenes as the two strings of section 8, sixteen names each.

    Os nomes das 32 cenas como as duas strings da seção 8, dezesseis nomes cada.
    """
    lista = list(nomes)
    if len(lista) > CENAS:
        raise NomesInvalidos(NOMES_DEMAIS, f"section 8 numbers {CENAS} scenes")
    primeira = nomes_json(CHAVE_NOMES_CENAS, lista[:NOMES_POR_DP], NOMES_POR_DP)
    segunda = nomes_json(CHAVE_NOMES_CENAS, lista[NOMES_POR_DP:], NOMES_POR_DP)
    return primeira, segunda


def nomes_das_maquinas(nomes: Sequence[str]) -> str:
    return nomes_json(CHAVE_NOMES_MAQUINAS, nomes, NUMEROS[PRODUTO_AR])


def nomes_cabem(nomes: Sequence[str]) -> bool:
    """True when the scene names would publish, so a route validates before saving.

    Verdadeiro quando os nomes de cena publicariam, para uma rota validar antes de gravar.
    """
    try:
        nomes_das_cenas(nomes)
    except NomesInvalidos:
        return False
    return True


def texto_de_dp(texto: str) -> str:
    """A free text reading inside the 255 bytes of a string DP, never cut inside a character.

    Uma leitura de texto livre dentro dos 255 bytes de um DP string, nunca cortada dentro de
    um caractere.
    """
    # Why: a lone surrogate is the one thing a str holds that UTF-8 cannot write, and a text
    # that carried one would raise on the way out of the socket instead of reaching the
    # bridge shortened; what a device sent is data, and it never breaks the bus.
    # Por que: um surrogado solto é a única coisa que um str guarda e o UTF-8 não escreve, e
    # um texto que levasse um estouraria na saída do socket em vez de chegar encurtado à
    # ponte; o que um aparelho mandou é dado, e ele nunca quebra o barramento.
    bruto = texto.encode("utf-8", errors="ignore")
    if len(bruto) <= TEXTO_MAXIMO_BYTES:
        return bruto.decode("utf-8")
    return bruto[:TEXTO_MAXIMO_BYTES].decode("utf-8", errors="ignore")


def _tamanho(texto: str) -> int | None:
    try:
        return len(texto.encode("utf-8"))
    except UnicodeEncodeError:
        return None
