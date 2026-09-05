# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 under attack: every malformed frame is a refusal with a code, never a crash.

The other end of this bus is whatever bridge someone wrote against the public contract, so
a frame that is garbage has to come back as an ack and leave the socket standing. The rules
attacked here: the first frame names the token of section 9 AND the licence, and neither
travels anywhere else; a dpid is read against the table of the product of that licence; the
chip never echoes a received DP, so a send only one is never reported and a report only one
never takes a set; a DP takes only the type and the range it declares; a string carries 255
bytes and not one more; the query frame answers with the slice of one licence and nothing
else.

Seção 8 sob ataque: todo quadro malformado é uma recusa com código, nunca uma quebra.

Do outro lado deste barramento está a ponte que alguém escreveu contra o contrato público,
então um quadro que é lixo tem de voltar como ack e deixar o socket de pé. As regras
atacadas aqui: o primeiro quadro nomeia o token da seção 9 E a licença, e nenhum dos dois
viaja em outro lugar; um dpid é lido contra a tabela do produto daquela licença; o chip
nunca ecoa um DP recebido, então um de só envio nunca é reportado e um de só report nunca
aceita set; um DP só aceita o tipo e a faixa que declara; uma string carrega 255 bytes e nem
um a mais; o quadro de consulta responde com a fatia de uma licença e nada mais.
"""

import pytest

from iphub.dpbus import mapa
from iphub.dpbus.mapa import (
    PRODUTO_AR,
    PRODUTO_AV,
    TEXTO_MAXIMO_BYTES,
    de_dp,
    dp_de,
    reportaveis,
)
from iphub.dpbus.protocolo import (
    CODIGOS,
    DP_DESCONHECIDO,
    DP_SOMENTE_LEITURA,
    FRAME_INVALIDO,
    ID_MAXIMO,
    LICENCA_DESCONHECIDA,
    LICENCA_MAXIMO,
    NAO_AUTENTICADO,
    NUMERO_OFFLINE,
    T_ACK,
    T_REPORT,
    T_SNAPSHOT,
    TOKEN_MAXIMO,
    VALOR_INVALIDO,
    Auth,
    Leitura,
    Pedido,
    ack,
    ler_auth,
    ler_quadro,
    report,
    snapshot,
    valor_valido,
)
from iphub.drivers.base import CODIGOS as CODIGOS_DE_DRIVER
from iphub.drivers.manifesto import MODOS_AR, VENTOS

TOKEN = "TvLQKcm3rEGaCFj3TrJmkPKrKPfHbYqYVTV0EahqAqI"
LICENCA = "sala"
AR = PRODUTO_AR
AV = PRODUTO_AV

# The audio and video product: the numbers of section 8 by function, so a test reads the
# function and not a number that means something else on the other product.
# O produto de áudio e vídeo: os números da seção 8 pela função, para um teste ler a função e
# não um número que significa outra coisa no outro produto.
LIGADO_1 = dp_de(AV, "ligado", 1)
NIVEL_1 = dp_de(AV, "nivel", 1)
NIVEL_12 = dp_de(AV, "nivel", 12)
CENA_AV = dp_de(AV, "cena")
GRUPO = dp_de(AV, "grupo")
COMANDO = dp_de(AV, "comando")
ONLINE_AV = dp_de(AV, "online")
MUDOS = dp_de(AV, "mudos")
ENTRADAS = dp_de(AV, "entradas")
TITULOS = dp_de(AV, "titulos")
PERFIS_1 = dp_de(AV, "perfis", indice=1)
NOMES_CENAS_AV = dp_de(AV, "nomes_cenas", indice=1)

# The air product: machine 1 starts at 101, the installation at 171.
# O produto de ar: a máquina 1 começa em 101, a instalação em 171.
LIGADO_AR_1 = dp_de(AR, "ligado", 1)
TEMPERATURA_1 = dp_de(AR, "temperatura", 1)
MODO_1 = dp_de(AR, "modo", 1)
VENTO_1 = dp_de(AR, "vento", 1)
CENA_AR = dp_de(AR, "cena")
ONLINE_AR = dp_de(AR, "online")
NOMES_MAQUINAS = dp_de(AR, "nomes")

# Written by hand from section 8, so a table that drifted would not judge itself.
# Escritos à mão a partir da seção 8, para uma tabela que derivou não se julgar sozinha.
SOMENTE_REPORTE = {AR: (172, 173, 174, 175), AV: tuple(range(144, 156))}
FORA_DO_CONTRATO = {
    AR: (0, 100, 105, 110, 140, 170, 176, 999, -101),
    AV: (0, 100, 113, 120, 133, 140, 156, 999, -101),
}


def _set(**campos) -> dict:
    return {"t": "set", "id": 1, "dpid": NIVEL_1, "v": 30, **campos}


def _consulta(**campos) -> dict:
    return {"t": "consulta", "id": 2, **campos}


def _auth(**campos) -> dict:
    return {"t": "auth", "token": TOKEN, "licenca": LICENCA, **campos}


def test_os_numeros_usados_aqui_sao_os_da_secao_8():
    assert (LIGADO_1, NIVEL_1, NIVEL_12, CENA_AV, GRUPO, COMANDO) == (101, 121, 132, 141, 142, 143)
    assert (ONLINE_AV, MUDOS, ENTRADAS, TITULOS, PERFIS_1, NOMES_CENAS_AV) == (
        144,
        145,
        146,
        148,
        149,
        154,
    )
    assert (LIGADO_AR_1, TEMPERATURA_1, MODO_1, VENTO_1) == (101, 102, 103, 104)
    assert (CENA_AR, ONLINE_AR, NOMES_MAQUINAS) == (171, 172, 173)


def test_o_vocabulario_e_o_da_secao_8():
    assert set(CODIGOS) == {
        "dp_desconhecido",
        "dp_somente_leitura",
        "valor_invalido",
        "numero_offline",
        "nao_autenticado",
        "frame_invalido",
        "licenca_desconhecida",
    }


def test_nenhum_codigo_e_uma_frase():
    # Section 11: the API answers a stable code and the panel translates it.
    # Seção 11: a API responde um código estável e o painel o traduz.
    for codigo in CODIGOS:
        assert codigo == codigo.lower()
        assert " " not in codigo


def test_uma_leitura_vazia_nao_e_pedido_nem_consulta_nem_recusa():
    assert Leitura() == Leitura(id=None, pedido=None, codigo="", consulta=False)


def test_um_set_valido_vira_pedido():
    leitura = ler_quadro(_set(), AV)
    assert leitura == Leitura(id=1, pedido=Pedido(dp=de_dp(AV, NIVEL_1), valor=30))
    assert leitura.codigo == ""
    assert leitura.consulta is False


def test_o_produto_diz_contra_qual_tabela_o_dpid_e_lido():
    """102 is the power of equipment 2 on audio and video and the setpoint of machine 1 on
    air, so one frame means one thing per licence and the value is judged by that meaning.

    102 é o ligado do equipamento 2 em áudio e vídeo e o setpoint da máquina 1 em ar, então
    um quadro significa uma coisa por licença e o valor é julgado por esse significado.
    """
    assert ler_quadro({"t": "set", "dpid": 102, "v": True}, AV).pedido.dp.funcao == "ligado"
    assert ler_quadro({"t": "set", "dpid": 102, "v": 22}, AR).pedido.dp.funcao == "temperatura"
    assert ler_quadro({"t": "set", "dpid": 102, "v": 22}, AV).codigo == VALOR_INVALIDO
    assert ler_quadro({"t": "set", "dpid": 102, "v": True}, AR).codigo == VALOR_INVALIDO


def test_o_dpid_de_um_produto_nao_existe_no_outro():
    assert ler_quadro(_set(dpid=CENA_AR, v=3), AV).codigo == DP_DESCONHECIDO
    assert ler_quadro(_set(dpid=NOMES_CENAS_AV, v="x"), AR).codigo == DP_DESCONHECIDO
    # Why: the fifth number of every machine of the air product is free, section 8.
    # Por que: o quinto número de toda máquina do produto de ar fica livre, seção 8.
    assert ler_quadro(_set(dpid=105, v=True), AR).codigo == DP_DESCONHECIDO
    assert ler_quadro(_set(dpid=105, v=True), AV).pedido.dp.numero == 5


@pytest.mark.parametrize("produto", [AR, AV])
@pytest.mark.parametrize("bruto", [None, 7, "set", [], ["t", "set"], True, 1.5])
def test_um_quadro_que_nao_e_objeto_e_recusado(bruto, produto):
    assert ler_quadro(bruto, produto) == Leitura(codigo=FRAME_INVALIDO)


@pytest.mark.parametrize(
    "bruto",
    [
        {},
        {"dpid": NIVEL_1, "v": 30},
        {"t": "ping", "dpid": NIVEL_1, "v": 30},
        _auth(),
        {"t": "ack", "dpid": NIVEL_1, "v": 30},
        {"t": "report", "dpid": NIVEL_1, "v": 30},
        {"t": "snapshot", "id": 1},
        {"t": "SET", "dpid": NIVEL_1, "v": 30},
        {"t": "CONSULTA", "id": 1},
        {"t": 7, "dpid": NIVEL_1, "v": 30},
        {"t": None, "dpid": NIVEL_1, "v": 30},
        {"t": ["set"], "dpid": NIVEL_1, "v": 30},
    ],
)
def test_um_t_ausente_ou_desconhecido_e_recusado(bruto):
    assert ler_quadro(bruto, AV).codigo == FRAME_INVALIDO


@pytest.mark.parametrize("produto", [AR, AV])
def test_a_consulta_vira_uma_leitura_de_consulta_com_o_id_dela(produto):
    assert ler_quadro(_consulta(), produto) == Leitura(id=2, consulta=True)


def test_a_consulta_sem_id_e_aceita_e_o_id_volta_nulo():
    assert ler_quadro({"t": "consulta"}, AV) == Leitura(id=None, consulta=True)


def test_a_consulta_e_da_fatia_inteira_e_ignora_dpid_e_valor():
    assert ler_quadro(_consulta(dpid=999, v="x"), AV) == Leitura(id=2, consulta=True)


def test_a_consulta_com_um_id_que_o_contrato_recusa_e_recusada():
    assert ler_quadro(_consulta(id="x" * (ID_MAXIMO + 1)), AV) == Leitura(codigo=FRAME_INVALIDO)


@pytest.mark.parametrize("dpid", ["101", 101.0, True, None, [101], {"dpid": 101}])
def test_um_dpid_que_nao_e_numero_e_recusado(dpid):
    leitura = ler_quadro({"t": "set", "id": 3, "dpid": dpid, "v": 30}, AV)
    assert leitura.codigo == FRAME_INVALIDO
    assert leitura.id == 3


def test_um_set_sem_dpid_e_recusado():
    assert ler_quadro({"t": "set", "id": 1, "v": 30}, AV).codigo == FRAME_INVALIDO


@pytest.mark.parametrize("produto", [AR, AV])
def test_um_dpid_fora_do_contrato_e_recusado(produto):
    for dpid in FORA_DO_CONTRATO[produto]:
        assert ler_quadro(_set(dpid=dpid, v=True), produto).codigo == DP_DESCONHECIDO, dpid


@pytest.mark.parametrize("produto", [AR, AV])
def test_um_set_num_dp_de_report_e_recusado(produto):
    # Why: online, the muted ones, the titles and the names are born of real state; a bridge
    # that could write them would publish an equipment as online without any equipment
    # having answered.
    # Por que: online, os mudos, os títulos e os nomes nascem de estado real; uma ponte que
    # pudesse escrevê-los publicaria um equipamento como online sem equipamento nenhum ter
    # respondido.
    for dpid in SOMENTE_REPORTE[produto]:
        assert ler_quadro(_set(dpid=dpid, v="x"), produto).codigo == DP_SOMENTE_LEITURA, dpid


def test_um_dp_de_report_recusa_o_set_antes_de_olhar_o_valor():
    assert valor_valido(de_dp(AV, ONLINE_AV), 5)
    assert ler_quadro(_set(dpid=ONLINE_AV, v=5), AV).codigo == DP_SOMENTE_LEITURA
    assert ler_quadro(_set(dpid=TITULOS, v="1=Faixa"), AV).codigo == DP_SOMENTE_LEITURA


def test_cena_e_comando_sao_de_envio_e_aceitam_set():
    assert ler_quadro(_set(dpid=CENA_AV, v=3), AV).pedido.valor == 3
    assert ler_quadro(_set(dpid=CENA_AR, v=32), AR).pedido.valor == 32
    assert ler_quadro(_set(dpid=COMANDO, v="1:ligar"), AV).pedido.valor == "1:ligar"


@pytest.mark.parametrize("valor", [0, 50, 100])
def test_o_nivel_aceita_a_escala_da_secao_6(valor):
    assert ler_quadro(_set(v=valor), AV).pedido.valor == valor
    assert ler_quadro(_set(dpid=NIVEL_12, v=valor), AV).pedido.dp.numero == 12


@pytest.mark.parametrize("valor", [-1, 101, 1000, 30.0, "30", True, False, None, [30]])
def test_o_nivel_recusa_o_que_nao_e_um_inteiro_de_zero_a_cem(valor):
    # Why: True is an int for Python, so a bool would land as the level 1 of the number.
    # Por que: True é int para o Python, então um bool chegaria como o nível 1 do número.
    assert ler_quadro(_set(v=valor), AV).codigo == VALOR_INVALIDO


@pytest.mark.parametrize(("produto", "dpid"), [(AV, LIGADO_1), (AR, LIGADO_AR_1)])
@pytest.mark.parametrize("valor", [True, False])
def test_o_ligado_aceita_um_booleano(produto, dpid, valor):
    assert ler_quadro(_set(dpid=dpid, v=valor), produto).pedido.valor is valor


@pytest.mark.parametrize(("produto", "dpid"), [(AV, LIGADO_1), (AR, LIGADO_AR_1)])
@pytest.mark.parametrize("valor", [1, 0, "true", "on", None, 1.0])
def test_o_ligado_recusa_o_que_nao_e_booleano(produto, dpid, valor):
    assert ler_quadro(_set(dpid=dpid, v=valor), produto).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("valor", [16, 22, 30])
def test_a_temperatura_aceita_os_graus_da_secao_6(valor):
    assert ler_quadro(_set(dpid=TEMPERATURA_1, v=valor), AR).pedido.valor == valor


@pytest.mark.parametrize("valor", [15, 31, 0, 100, 22.0, "22", True, None])
def test_a_temperatura_recusa_o_que_esta_fora_de_16_a_30(valor):
    assert ler_quadro(_set(dpid=TEMPERATURA_1, v=valor), AR).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("valor", MODOS_AR)
def test_o_modo_do_ar_aceita_o_enum_da_secao_8(valor):
    assert ler_quadro(_set(dpid=MODO_1, v=valor), AR).pedido.valor == valor


@pytest.mark.parametrize("valor", ["FRIO", "cool", "", "frio ", "medio", 1, None, ["frio"]])
def test_o_modo_do_ar_recusa_o_que_esta_fora_do_enum(valor):
    assert ler_quadro(_set(dpid=MODO_1, v=valor), AR).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("valor", VENTOS)
def test_o_vento_aceita_o_enum_da_secao_8(valor):
    assert ler_quadro(_set(dpid=VENTO_1, v=valor), AR).pedido.valor == valor


@pytest.mark.parametrize("valor", ["turbo", "medium", "frio", "", 2, None])
def test_o_vento_recusa_o_que_esta_fora_do_enum(valor):
    assert ler_quadro(_set(dpid=VENTO_1, v=valor), AR).codigo == VALOR_INVALIDO


@pytest.mark.parametrize(("produto", "dpid"), [(AV, CENA_AV), (AR, CENA_AR)])
def test_a_cena_aceita_um_numero_de_um_a_trinta_e_dois(produto, dpid):
    for valor in (1, 17, 32):
        assert ler_quadro(_set(dpid=dpid, v=valor), produto).pedido.valor == valor
    for valor in (0, 33, "3", 3.0, True, None, "cena1"):
        assert ler_quadro(_set(dpid=dpid, v=valor), produto).codigo == VALOR_INVALIDO, valor


def test_o_grupo_aceita_o_solo_e_os_doze_numeros():
    for valor in (0, 1, 12):
        assert ler_quadro(_set(dpid=GRUPO, v=valor), AV).pedido.valor == valor
    for valor in (-1, 13, True, "1", 1.0, None):
        assert ler_quadro(_set(dpid=GRUPO, v=valor), AV).codigo == VALOR_INVALIDO, valor


@pytest.mark.parametrize(
    "valor",
    [
        "1:ligar",
        "12:entrada:3",
        "1:tecla:canal_mais",
        "3:extra:nome com espaço e ção",
        "x" * TEXTO_MAXIMO_BYTES,
    ],
)
def test_o_canal_de_comando_aceita_uma_string_curta_e_imprimivel(valor):
    # Why: the words are read by the module that owns the numbers; here a string only has to
    # be short and printable, so a megabyte of text never reaches a parser.
    # Por que: as palavras são lidas pelo módulo dono dos números; aqui uma string só precisa
    # ser curta e imprimível, para um megabyte de texto nunca chegar a um analisador.
    assert ler_quadro(_set(dpid=COMANDO, v=valor), AV).pedido.valor == valor


@pytest.mark.parametrize(
    "valor",
    [
        "",
        "1:ligar\n",
        "1:ligar\t",
        "\x00",
        "1:ligar\x7f",
        "\ud800",
        "x" * (TEXTO_MAXIMO_BYTES + 1),
        7,
        None,
        True,
        ["1:ligar"],
    ],
)
def test_o_canal_de_comando_recusa_o_vazio_o_controle_e_o_que_nao_e_texto(valor):
    assert ler_quadro(_set(dpid=COMANDO, v=valor), AV).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("identificador", [1, 0, -3, "abc", "", None, "x" * ID_MAXIMO])
def test_o_id_volta_como_veio(identificador):
    assert ler_quadro(_set(id=identificador), AV).id == identificador


@pytest.mark.parametrize("identificador", ["x" * (ID_MAXIMO + 1), {"a": 1}, [1], 1.5, True, False])
def test_um_id_que_o_ack_teria_de_ecoar_de_volta_e_recusado(identificador):
    # Why: the ack echoes the id, so a client sending a megabyte of id would be answered
    # with a megabyte on every frame.
    # Por que: o ack ecoa o id, então um cliente mandando um megabyte de id receberia um
    # megabyte de volta a cada quadro.
    leitura = ler_quadro(_set(id=identificador), AV)
    assert leitura.codigo == FRAME_INVALIDO
    assert leitura.id is None


def test_um_id_que_o_utf_8_nao_devolve_e_recusado():
    # Why: the ack echoes the id, so a lone surrogate accepted here would come back as an
    # encoding error on the very frame the client is waiting for.
    # Por que: o ack ecoa o id, então um surrogado solto aceito aqui voltaria como erro de
    # codificação justamente no quadro que o cliente está esperando.
    assert ler_quadro(_set(id="pedido \ud800"), AV).codigo == FRAME_INVALIDO


def test_o_id_volta_mesmo_quando_o_quadro_e_recusado():
    leitura = ler_quadro(_set(id="pedido-9", dpid=999), AV)
    assert (leitura.id, leitura.codigo) == ("pedido-9", DP_DESCONHECIDO)


def test_uma_chave_que_o_contrato_nao_nomeia_e_ignorada():
    # The frame is a wire protocol other bridges implement, so an extra key is ignored;
    # nothing reads it and it changes nothing.
    # O quadro é protocolo de fio que outras pontes implementam, então uma chave a mais é
    # ignorada; ninguém a lê e ela não muda nada.
    assert ler_quadro(_set(origem="ponte"), AV).pedido.valor == 30
    assert ler_quadro(_consulta(origem="ponte"), AV) == Leitura(id=2, consulta=True)


@pytest.mark.parametrize("produto", [AR, AV])
def test_toda_recusa_usa_o_vocabulario(produto):
    quadros = [
        None,
        {},
        _set(dpid="x"),
        _set(dpid=999),
        _set(dpid=SOMENTE_REPORTE[produto][0]),
        _set(v="alto"),
        _set(id=1.5),
    ]
    for bruto in quadros:
        assert ler_quadro(bruto, produto).codigo in CODIGOS


def test_o_ack_diz_ok_sem_codigo():
    assert ack(7) == {"t": T_ACK, "id": 7, "ok": True, "code": None}


def test_o_ack_carrega_o_codigo_que_recusou():
    assert ack("a", NUMERO_OFFLINE) == {"t": T_ACK, "id": "a", "ok": False, "code": NUMERO_OFFLINE}


@pytest.mark.parametrize("codigo", [*CODIGOS, *CODIGOS_DE_DRIVER])
def test_o_ack_aceita_todo_codigo_estavel_dos_dois_vocabularios(codigo):
    assert ack(1, codigo)["code"] == codigo


@pytest.mark.parametrize(
    "codigo", ["the device did not answer", "Erro", "", " ", "x" * 41, 7, ["eq_offline"]]
)
def test_o_ack_recusa_uma_frase(codigo):
    with pytest.raises(ValueError):
        ack(1, codigo)


def test_um_report_carrega_o_estado_e_o_carimbo():
    assert report(de_dp(AV, NIVEL_1), 30, 1_700_000_000.9) == {
        "t": T_REPORT,
        "dpid": NIVEL_1,
        "v": 30,
        "ts": 1_700_000_000,
    }


@pytest.mark.parametrize(("produto", "dpid"), [(AV, CENA_AV), (AV, COMANDO), (AR, CENA_AR)])
def test_um_report_de_dp_que_o_chip_nunca_confirma_e_um_defeito(produto, dpid):
    # Why: the chip never echoes, so a report of a send only DP would publish a state that
    # no device confirmed; it is a defect of whoever built it, not a bad frame from outside.
    # Por que: o chip nunca ecoa, então um report de um DP de só envio publicaria um estado
    # que aparelho nenhum confirmou; é defeito de quem o montou, não quadro ruim de fora.
    with pytest.raises(ValueError):
        report(de_dp(produto, dpid), 1, 1.0)


@pytest.mark.parametrize(
    ("produto", "dpid", "valor"),
    [
        (AV, NIVEL_1, "30"),
        (AV, NIVEL_1, 101),
        (AV, NIVEL_1, True),
        (AV, LIGADO_1, 1),
        (AV, GRUPO, 13),
        (AV, ONLINE_AV, 1 << 12),
        (AV, MUDOS, -1),
        (AV, ENTRADAS, 3),
        (AV, TITULOS, 7),
        (AV, TITULOS, None),
        (AR, TEMPERATURA_1, 15),
        (AR, MODO_1, "cool"),
        (AR, VENTO_1, 3),
        (AR, ONLINE_AR, 1 << 8),
        (AR, NOMES_MAQUINAS, ["Sala"]),
    ],
)
def test_um_report_com_o_tipo_errado_e_recusado(produto, dpid, valor):
    with pytest.raises(ValueError):
        report(de_dp(produto, dpid), valor, 1.0)


def test_um_report_de_string_leva_o_texto_como_veio_inclusive_vazio():
    # Why: no number with an active input is the empty string of the map, and that is a
    # state the bridge has to see, not a value to drop.
    # Por que: nenhum número com entrada ativa é a string vazia do mapa, e isso é um estado
    # que a ponte precisa ver, não um valor a descartar.
    assert report(de_dp(AV, ENTRADAS), "1=2;3=1", 1.0)["v"] == "1=2;3=1"
    assert report(de_dp(AV, ENTRADAS), mapa.pares({}), 1.0)["v"] == ""
    assert report(de_dp(AV, TITULOS), mapa.titulos({1: "Faixa"}), 1.0)["v"] == "1=Faixa"


def test_um_texto_acima_dos_255_bytes_e_recusado_em_vez_de_cortado():
    """A cut JSON is not JSON and a cut profile is no profile, so whoever builds the text is
    the one that shortens or refuses it, and the wire refuses what would not fit.

    Um JSON cortado não é JSON e um perfil cortado não é perfil, então quem monta o texto é
    quem o encurta ou recusa, e o fio recusa o que não caberia.
    """
    with pytest.raises(ValueError):
        report(de_dp(AV, NOMES_CENAS_AV), '{"c":["' + "x" * 300 + '"]}', 1.0)
    with pytest.raises(ValueError):
        report(de_dp(AV, TITULOS), "ç" * 200, 1.0)
    encurtado = mapa.titulos({numero: "ç" * 18 for numero in range(1, 13)})
    assert report(de_dp(AV, TITULOS), encurtado, 1.0)["v"] == encurtado


def test_um_texto_no_limite_exato_dos_255_bytes_passa():
    texto = "ç" * 127 + "x"
    assert len(texto.encode("utf-8")) == TEXTO_MAXIMO_BYTES
    assert report(de_dp(AV, PERFIS_1), texto, 1.0)["v"] == texto


def test_um_texto_com_surrogado_solto_e_recusado():
    with pytest.raises(ValueError):
        report(de_dp(AV, TITULOS), "1=\ud800", 1.0)


def test_o_snapshot_leva_so_o_que_pode_ser_reportado_da_fatia_de_um_produto():
    valores = {NIVEL_1: 30, CENA_AV: 3, COMANDO: "1:ligar", LIGADO_1: True, ONLINE_AV: 1, 999: 1}
    assert snapshot(AV, valores, "q7") == {
        "t": T_SNAPSHOT,
        "id": "q7",
        "dps": {"101": True, "121": 30, "144": 1},
    }


def test_o_snapshot_do_produto_de_ar_le_a_tabela_de_ar():
    valores = {TEMPERATURA_1: 22, CENA_AR: 3, LIGADO_AR_1: True, MODO_1: "frio", 155: "x"}
    quadro = snapshot(AR, valores)
    assert quadro["dps"] == {"101": True, "102": 22, "103": "frio"}
    assert quadro["id"] is None


def test_o_snapshot_omite_um_dp_que_ainda_nao_tem_valor():
    # Why: a bridge that read a null would take it for a state and turn an empty number into
    # an equipment that is off.
    # Por que: uma ponte que lesse um nulo o tomaria por estado e tornaria um número vazio num
    # equipamento desligado.
    assert snapshot(AV, {NIVEL_1: None, LIGADO_1: False})["dps"] == {"101": False}
    assert snapshot(AV, {})["dps"] == {}


def test_o_snapshot_sai_na_ordem_da_secao_8_e_com_chaves_de_texto():
    valores = {NOMES_CENAS_AV: '{"c":[]}', NIVEL_1: 30, LIGADO_1: True, ONLINE_AV: 1, TITULOS: ""}
    dps = snapshot(AV, valores)["dps"]
    assert list(dps) == ["101", "121", "144", "148", "154"]
    assert list(dps) == [str(dpid) for dpid in reportaveis(AV) if dpid in valores]
    assert all(type(chave) is str for chave in dps)


def test_o_titulo_que_nunca_e_empurrado_responde_a_consulta():
    assert not de_dp(AV, TITULOS).empurrado
    assert snapshot(AV, {TITULOS: "1=Faixa"})["dps"] == {"148": "1=Faixa"}


def test_o_auth_devolve_o_token_e_a_licenca_do_primeiro_quadro():
    assert ler_auth(_auth()) == Auth(token=TOKEN, licenca=LICENCA)


def test_uma_chave_a_mais_no_auth_e_ignorada():
    assert ler_auth(_auth(versao=2)) == Auth(token=TOKEN, licenca=LICENCA)


def test_um_auth_vazio_nao_tem_token_nem_licenca():
    assert Auth() == Auth(token="", licenca="")
    assert not Auth().token
    assert not Auth().licenca


@pytest.mark.parametrize(
    "bruto",
    [
        None,
        [],
        "auth",
        {},
        {"t": "set", "token": TOKEN, "licenca": LICENCA},
        _auth(t="AUTH"),
        {"t": "auth"},
        {"t": "auth", "token": TOKEN},
        {"t": "auth", "licenca": LICENCA},
        _auth(token=""),
        _auth(token=7),
        _auth(token=None),
        _auth(token=["x"]),
        _auth(token="x" * (TOKEN_MAXIMO + 1)),
        _auth(licenca=""),
        _auth(licenca=7),
        _auth(licenca=None),
        _auth(licenca=["sala"]),
        _auth(licenca="l" * (LICENCA_MAXIMO + 1)),
    ],
)
def test_um_quadro_que_nao_e_o_auth_da_secao_8_nao_autentica(bruto):
    assert ler_auth(bruto) == Auth()


@pytest.mark.parametrize("token", ["tökén-de-maquina", "\ud800", "senha ção"])
def test_um_token_fora_do_ascii_e_recusado_em_vez_de_estourar(token):
    # Why: the api_token is a token_urlsafe, which is ASCII, and comparing a non ASCII
    # string in constant time raises instead of answering that it does not match.
    # Por que: o api_token é um token_urlsafe, que é ASCII, e comparar uma string não ASCII
    # em tempo constante estoura em vez de responder que não casa.
    assert ler_auth(_auth(token=token)) == Auth()


def test_o_token_no_limite_e_a_licenca_no_limite_passam():
    auth = ler_auth(_auth(token="t" * TOKEN_MAXIMO, licenca="l" * LICENCA_MAXIMO))
    assert auth == Auth(token="t" * TOKEN_MAXIMO, licenca="l" * LICENCA_MAXIMO)


def test_os_codigos_do_socket_existem_para_ele_recusar():
    assert NAO_AUTENTICADO in CODIGOS
    assert LICENCA_DESCONHECIDA in CODIGOS
    assert NUMERO_OFFLINE in CODIGOS
