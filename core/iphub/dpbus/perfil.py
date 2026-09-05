# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8: the profile of an equipment of audio and video, the string the panel of the
platform reads to draw the right controls for that number.

numero|template|nome|entradas|atalhos|modos|funcoes: the lists are labels joined by ',', the
functions are letters, and the whole string fits 200 bytes or the registration that produced
it is refused. Nothing here reaches a device: a profile is built from the registration and
the manifest, so it is the same string on every hub for the same registration.

Seção 8: o perfil de um equipamento de áudio e vídeo, a string que o painel da plataforma
lê para desenhar os controles certos daquele número.

numero|template|nome|entradas|atalhos|modos|funcoes: as listas são rótulos unidos por ',',
as funções são letras, e a string inteira cabe em 200 bytes ou o cadastro que a produziu é
recusado. Nada aqui alcança um aparelho: um perfil nasce do cadastro e do manifesto, então é
a mesma string em todo hub para o mesmo cadastro.
"""

import re
from collections.abc import Sequence

from iphub.config import Cadastro, Item
from iphub.dpbus import mapa
from iphub.drivers.manifesto import Manifesto, template_de

SEPARADOR_DE_CAMPO = "|"
SEPARADOR_DE_ITEM = ","

# Section 8: the name travels shortened to this many characters inside the profile.
# Seção 8: o nome viaja encurtado a este tanto de caracteres dentro do perfil.
NOME_MAXIMO = 20

# The letters of section 8, each one a control the panel draws when it is present.
# As letras da seção 8, cada uma um controle que o painel desenha quando ela está presente.
LETRA_LIGAR = "L"
LETRA_NIVEL = "N"
LETRA_MUDO = "M"
LETRA_ENTRADA = "E"
LETRA_TECLAS = "T"
LETRA_MODO = "D"
# The four keys of the transport, in the order a player draws them: previous, the play and
# pause pair, stop, next. Each one is a capability of its own in section 6, so each one is a
# letter of its own: a speaker that skips tracks and one that only plays are different
# equipment, and the panel of the platform reads the difference here.
# As quatro teclas do transporte, na ordem em que um player as desenha: anterior, o par tocar
# e pausar, parar, próxima. Cada uma é capacidade própria na seção 6, então cada uma é letra
# própria: uma caixa que pula faixa e uma que só toca são equipamentos diferentes, e o painel
# da plataforma lê a diferença aqui.
LETRA_ANTERIOR = "A"
LETRA_TRANSPORTE = "P"
LETRA_PARAR = "S"
LETRA_PROXIMA = "F"
LETRA_GRUPO = "G"
LETRAS = (
    LETRA_LIGAR,
    LETRA_NIVEL,
    LETRA_MUDO,
    LETRA_ENTRADA,
    LETRA_TECLAS,
    LETRA_MODO,
    LETRA_ANTERIOR,
    LETRA_TRANSPORTE,
    LETRA_PARAR,
    LETRA_PROXIMA,
    LETRA_GRUPO,
)

# Which list of the registration each capability of section 6 reads its values from.
# De qual lista do cadastro cada capacidade da seção 6 lê os valores dela.
LISTA_DA_CAPACIDADE = {"fonte": "entradas", "atalho": "atalhos", "modo": "modos"}

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
_SEPARADORES = re.compile(r"[|;,]")


def montar(numero: int, cadastro: Cadastro, manifesto: Manifesto) -> str:
    """The profile of one number, section 8.

    O perfil de um número, seção 8.
    """
    return SEPARADOR_DE_CAMPO.join(
        (
            str(numero),
            template_de(manifesto.categoria),
            nome(cadastro.nome),
            _rotulos(cadastro, manifesto, "fonte"),
            _rotulos(cadastro, manifesto, "atalho"),
            _rotulos(cadastro, manifesto, "modo"),
            funcoes(cadastro, manifesto),
        )
    )


def nome(bruto: str) -> str:
    """The name as it travels in the profile: no separator, no control character, at most
    twenty characters.

    O nome como ele viaja no perfil: sem separador, sem caractere de controle, no máximo vinte
    caracteres.
    """
    limpo = _SEPARADORES.sub(" ", _CONTROLE.sub("", bruto)).strip()
    return limpo[:NOME_MAXIMO]


def funcoes(cadastro: Cadastro, manifesto: Manifesto) -> str:
    """The letters of section 8 in their fixed order, one per control the equipment offers.

    As letras da seção 8 na ordem fixa delas, uma por controle que o equipamento oferece.
    """
    capacidades = set(manifesto.capacidades)
    letras = []
    # Why: half of the power pair is no switch at all, because a switch that turns on and
    # cannot turn off is a switch the customer cannot trust; the same holds for transport.
    # Por que: metade do par de energia não é chave nenhuma, porque uma chave que liga e não
    # desliga é uma chave em que o cliente não pode confiar; o mesmo vale para o transporte.
    if {"ligar", "desligar"} <= capacidades:
        letras.append(LETRA_LIGAR)
    if "volume" in capacidades:
        letras.append(LETRA_NIVEL)
    if "mudo" in capacidades:
        letras.append(LETRA_MUDO)
    if "fonte" in capacidades and itens(cadastro, "entradas"):
        letras.append(LETRA_ENTRADA)
    if "tecla" in capacidades and manifesto.teclas:
        letras.append(LETRA_TECLAS)
    if "modo" in capacidades and itens(cadastro, "modos"):
        letras.append(LETRA_MODO)
    if "anterior" in capacidades:
        letras.append(LETRA_ANTERIOR)
    if {"tocar", "pausar"} <= capacidades:
        letras.append(LETRA_TRANSPORTE)
    # Why: stop, previous and next have no opposite half to wait for, unlike the power pair
    # and the play and pause pair: a key that skips a track needs nothing to undo it.
    # Por que: parar, anterior e próxima não têm metade oposta pela qual esperar, ao contrário
    # do par de energia e do par tocar e pausar: uma tecla que pula faixa não precisa de nada
    # que a desfaça.
    if "parar" in capacidades:
        letras.append(LETRA_PARAR)
    if "proxima" in capacidades:
        letras.append(LETRA_PROXIMA)
    if "agrupar" in capacidades:
        letras.append(LETRA_GRUPO)
    return "".join(letras)


def itens(cadastro: Cadastro, lista: str) -> tuple[Item, ...]:
    return tuple(cadastro.listas.get(lista, ()))


def cabe(perfil: str) -> bool:
    """True when the profile fits the ceiling of section 8.

    Verdadeiro quando o perfil cabe no teto da seção 8.
    """
    try:
        return len(perfil.encode("utf-8")) <= mapa.PERFIL_MAXIMO_BYTES
    except UnicodeEncodeError:
        return False


def cabe_em_qualquer_numero(cadastro: Cadastro, manifesto: Manifesto) -> bool:
    """Judged with the widest number, so a registration accepted on number 3 still fits when
    it is moved to number 12.

    Julgado com o número mais largo, para um cadastro aceito no número 3 ainda caber quando
    for movido para o número 12.
    """
    return cabe(montar(mapa.NUMEROS[mapa.PRODUTO_AV], cadastro, manifesto))


def _rotulos(cadastro: Cadastro, manifesto: Manifesto, capacidade: str) -> str:
    # Why: a list the manifest cannot act on is not offered, because a button that only ever
    # answers nao_suportado is a button the customer learns to distrust.
    # Por que: uma lista sobre a qual o manifesto não age não é oferecida, porque um botão que
    # só responde nao_suportado é um botão em que o cliente aprende a desconfiar.
    if capacidade not in manifesto.capacidades:
        return ""
    lista = itens(cadastro, LISTA_DA_CAPACIDADE[capacidade])
    return SEPARADOR_DE_ITEM.join(item.rotulo for item in lista)


def rotulos_de(itens_da_lista: Sequence[Item]) -> tuple[str, ...]:
    return tuple(item.rotulo for item in itens_da_lista)
