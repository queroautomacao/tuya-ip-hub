# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 under attack: the profile of a number is the one string the panel of the
platform reads to draw the controls, so every field of it is pinned here by hand.

The letters are the contract: a letter that appeared without its capability would draw a
button that only ever answers nao_suportado, and a letter that failed to appear would hide a
control the equipment has. Half of a pair (ligar without desligar, tocar without pausar) is no
control at all, a list is only written when the manifest can act on it, and the whole string
fits 200 bytes judged with the widest number, so a registration accepted on number 3 still
fits when it is moved to number 12.

Seção 8 sob ataque: o perfil de um número é a única string que o painel da plataforma lê para
desenhar os controles, então cada campo dele está fixado aqui na mão.

As letras são o contrato: uma letra que aparecesse sem a capacidade dela desenharia um botão
que só responde nao_suportado, e uma letra que deixasse de aparecer esconderia um controle
que o equipamento tem. Metade de um par (ligar sem desligar, tocar sem pausar) não é controle
nenhum, uma lista só é escrita quando o manifesto age sobre ela, e a string inteira cabe em
200 bytes julgada com o número mais largo, para um cadastro aceito no número 3 ainda caber
quando for movido para o número 12.
"""

import dataclasses

import pytest

from iphub.config import Cadastro, Item
from iphub.dpbus import mapa
from iphub.dpbus.perfil import (
    LETRAS,
    LISTA_DA_CAPACIDADE,
    NOME_MAXIMO,
    cabe,
    cabe_em_qualquer_numero,
    funcoes,
    itens,
    montar,
    nome,
    rotulos_de,
)
from iphub.drivers.manifesto import CATEGORIAS, Manifesto

CAPACIDADES_INTEIRAS = (
    "ligar",
    "desligar",
    "volume",
    "mudo",
    "fonte",
    "tecla",
    "modo",
    "tocar",
    "pausar",
    "atalho",
)
ENTRADAS = (Item("HDMI 1", "hdmi1"), Item("HDMI 2", "hdmi2"))
ATALHOS = (Item("Netflix", "app:netflix"),)
MODOS = (Item("Filme", "movie"), Item("Musica", "music"))

# Why: sixteen characters is the ceiling of a label and ten the ceiling of the inputs, so
# these are the heaviest lists a registration can carry into a profile.
# Por que: dezesseis caracteres é o teto de um rótulo e dez o teto das entradas, então estas
# são as listas mais pesadas que um cadastro leva para dentro de um perfil.
NOME_DE_VINTE = "Sala de estar grande"
ENTRADAS_PESADAS = tuple(Item(f"Entrada {k:02d} longa", f"in{k}") for k in range(1, 11))


def _manifesto(
    categoria: str = "receiver",
    capacidades: tuple[str, ...] = CAPACIDADES_INTEIRAS,
    teclas: tuple[str, ...] = ("mais", "menos"),
) -> Manifesto:
    textos = {"descricao": "Receiver de teste"}
    return Manifesto(
        tipo="receiver_falso",
        rotulo={"pt": "Receiver", "en": "Receiver"},
        categoria=categoria,
        capacidades=capacidades,
        textos={"pt": dict(textos), "en": dict(textos)},
        teclas=teclas,
    )


def _cadastro(
    nome: str = "Sala",
    entradas: tuple[Item, ...] = ENTRADAS,
    atalhos: tuple[Item, ...] = ATALHOS,
    modos: tuple[Item, ...] = MODOS,
) -> Cadastro:
    return Cadastro(
        identidade="uuid-1",
        tipo="receiver_falso",
        nome=nome,
        ip="192.0.2.10",
        listas={"entradas": entradas, "atalhos": atalhos, "modos": modos},
    )


def test_o_perfil_tem_o_formato_da_secao_8():
    """numero|template|nome|entradas|atalhos|modos|funcoes, the lists as labels joined by ','.

    numero|template|nome|entradas|atalhos|modos|funcoes, as listas como rótulos unidos por ','.
    """
    assert (
        montar(3, _cadastro(), _manifesto())
        == "3|au|Sala|HDMI 1,HDMI 2|Netflix|Filme,Musica|LNMETDP"
    )
    assert montar(12, _cadastro(), _manifesto()).startswith("12|au|Sala|")


def test_as_letras_sao_as_da_secao_8_na_ordem_fixa():
    """The order of the letters never follows the order the manifest declared them in.

    A ordem das letras nunca segue a ordem em que o manifesto as declarou.
    """
    assert LETRAS == ("L", "N", "M", "E", "T", "D", "A", "P", "S", "F", "G")
    invertidas = (
        "agrupar",
        "proxima",
        "parar",
        "pausar",
        "tocar",
        "anterior",
        "modo",
        "tecla",
        "fonte",
        "mudo",
        "volume",
        "desligar",
        "ligar",
    )
    manifesto = _manifesto(categoria="multiroom", capacidades=invertidas)
    assert funcoes(_cadastro(), manifesto) == "LNMETDAPSFG"


@pytest.mark.parametrize(
    ("capacidades", "esperado"),
    [
        ((), ""),
        (("ligar",), ""),
        (("desligar",), ""),
        (("ligar", "desligar"), "L"),
        (("volume",), "N"),
        (("mudo",), "M"),
        (("fonte",), "E"),
        (("tecla",), "T"),
        (("modo",), "D"),
        (("tocar",), ""),
        (("pausar",), ""),
        (("tocar", "pausar"), "P"),
        (("parar",), "S"),
        (("anterior",), "A"),
        (("proxima",), "F"),
        (("tocar", "pausar", "parar", "proxima", "anterior"), "APSF"),
        (("comando_extra", "atalho"), ""),
    ],
)
def test_cada_letra_so_aparece_com_a_capacidade_dela(capacidades, esperado):
    """Half of the power pair or of the play and pause pair is no control the customer can
    trust; stop, previous and next answer for themselves, because no half is missing.

    Metade do par de energia ou do par tocar e pausar não é controle em que o cliente confie;
    parar, anterior e próxima respondem por si, porque não falta metade nenhuma.
    """
    assert funcoes(_cadastro(), _manifesto(capacidades=capacidades)) == esperado


def test_a_letra_de_grupo_so_aparece_com_agrupar():
    multiroom = _manifesto(categoria="multiroom", capacidades=("volume", "agrupar"))
    assert funcoes(_cadastro(), multiroom) == "NG"
    assert funcoes(_cadastro(), _manifesto(categoria="multiroom", capacidades=("volume",))) == "N"


def test_uma_lista_vazia_e_um_vocabulario_vazio_nao_dao_letra():
    """E needs fonte AND inputs to choose from, T needs tecla AND the keys the driver sends, D
    needs modo AND modes to choose from; a control with nothing behind it is not offered.

    E precisa de fonte E de entradas para escolher, T precisa de tecla E das teclas que o
    driver manda, D precisa de modo E de modos para escolher; um controle sem nada atrás não
    é oferecido.
    """
    assert funcoes(_cadastro(entradas=()), _manifesto(capacidades=("fonte",))) == ""
    assert funcoes(_cadastro(), _manifesto(capacidades=("tecla",), teclas=())) == ""
    assert funcoes(_cadastro(modos=()), _manifesto(capacidades=("modo",))) == ""
    sem_listas = Cadastro(identidade="uuid-1", tipo="receiver_falso", nome="Sala")
    assert funcoes(sem_listas, _manifesto()) == "LNMTP"
    assert funcoes(_cadastro(), _manifesto(capacidades=())) == ""


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Sala", "Sala"),
        ("Sala de estar da casa grande", "Sala de estar da cas"),
        ("A|B;C,D", "A B C D"),
        ("Sala\x00\x1f\x7f", "Sala"),
        ("\x00\x00\x00\x00\x00Sala de estar da casa", "Sala de estar da cas"),
        ("  Sala  ", "Sala"),
        ("|;,", ""),
        ("Área", "Área"),
        ("", ""),
    ],
)
def test_o_nome_viaja_encurtado_sem_separador_e_sem_controle(bruto, esperado):
    """The separators of the profile and of the strings of section 8 never travel inside a
    name, a control character never reaches the bus, and the cut comes after the cleaning.

    Os separadores do perfil e das strings da seção 8 nunca viajam dentro de um nome, um
    caractere de controle nunca chega ao barramento, e o corte vem depois da limpeza.
    """
    assert nome(bruto) == esperado
    assert len(esperado) <= NOME_MAXIMO == 20


def test_o_nome_vai_no_perfil_ja_encurtado():
    perfil = montar(1, _cadastro(nome="Sala|de;estar,da casa grande"), _manifesto())
    assert perfil.split("|")[2] == "Sala de estar da cas"
    assert perfil.count("|") == 6


def test_uma_lista_so_e_escrita_quando_a_capacidade_e_declarada():
    """A list the manifest cannot act on is not offered: a button that only ever answers
    nao_suportado is a button the customer learns to distrust.

    Uma lista sobre a qual o manifesto não age não é oferecida: um botão que só responde
    nao_suportado é um botão em que o cliente aprende a desconfiar.
    """
    assert (
        montar(1, _cadastro(), _manifesto(capacidades=("fonte",))) == "1|au|Sala|HDMI 1,HDMI 2|||E"
    )
    assert montar(1, _cadastro(), _manifesto(capacidades=("atalho",))) == "1|au|Sala||Netflix||"
    assert montar(1, _cadastro(), _manifesto(capacidades=("modo",))) == "1|au|Sala|||Filme,Musica|D"
    assert montar(1, _cadastro(), _manifesto(capacidades=())) == "1|au|Sala||||"
    vazio = _cadastro(entradas=(), atalhos=(), modos=())
    assert montar(1, vazio, _manifesto()) == "1|au|Sala||||LNMTP"


@pytest.mark.parametrize("categoria", CATEGORIAS)
def test_o_template_e_tv_para_tv_e_projetor_e_au_para_o_resto(categoria):
    perfil = montar(1, _cadastro(), _manifesto(categoria=categoria, capacidades=("volume",)))
    esperado = "tv" if categoria in ("tv", "projetor") else "au"
    assert perfil.split("|")[1] == esperado


def test_cabe_mede_bytes_e_para_em_200():
    """Section 8: a profile weighs at most 200 BYTES, so an accented name counts twice per
    letter, and a text UTF-8 cannot write never fits.

    Seção 8: um perfil pesa no máximo 200 BYTES, então um nome acentuado conta duas vezes por
    letra, e um texto que o UTF-8 não escreve nunca cabe.
    """
    assert mapa.PERFIL_MAXIMO_BYTES == 200
    assert cabe("x" * 200)
    assert not cabe("x" * 201)
    assert cabe("é" * 100)
    assert not cabe("é" * 101)
    assert cabe("")
    assert not cabe("\ud800")


def test_cabe_em_qualquer_numero_julga_com_o_numero_12():
    """A registration accepted on number 1 still fits when it is moved to number 12, so the
    judgement is made with the widest number, which is one byte wider.

    Um cadastro aceito no número 1 ainda cabe quando for movido para o número 12, então o
    julgamento é feito com o número mais largo, que é um byte mais largo.
    """
    cadastro = _cadastro(nome=NOME_DE_VINTE, entradas=ENTRADAS_PESADAS, atalhos=(), modos=())
    justo = _manifesto(capacidades=("fonte",))
    assert len(montar(12, cadastro, justo).encode("utf-8")) == 200
    assert len(montar(1, cadastro, justo).encode("utf-8")) == 199
    assert cabe_em_qualquer_numero(cadastro, justo)
    largo = _manifesto(capacidades=("fonte", "volume"))
    assert cabe(montar(1, cadastro, largo))
    assert not cabe_em_qualquer_numero(cadastro, largo)


def test_itens_e_rotulos_leem_as_listas_do_cadastro():
    assert itens(_cadastro(), "entradas") == ENTRADAS
    assert itens(_cadastro(), "modos") == MODOS
    assert itens(Cadastro(identidade="uuid-1", tipo="receiver_falso"), "entradas") == ()
    assert rotulos_de(ENTRADAS) == ("HDMI 1", "HDMI 2")
    assert rotulos_de(()) == ()
    assert LISTA_DA_CAPACIDADE == {"fonte": "entradas", "atalho": "atalhos", "modo": "modos"}


def test_o_item_de_uma_lista_e_congelado():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ENTRADAS[0].rotulo = "outro"
