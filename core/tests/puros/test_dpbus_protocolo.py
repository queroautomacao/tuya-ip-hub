# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 under attack: every malformed frame is a refusal with a code, never a crash.

The other end of this bus is whatever bridge someone wrote against the public contract, so
a frame that is garbage has to come back as an ack and leave the socket standing. The rules
attacked here: the chip never echoes a received DP, a DP takes only the type it declares,
an enum takes at most ten values, a string DP is report only and the token of section 9
never travels anywhere but the first frame.

Seção 8 sob ataque: todo quadro malformado é uma recusa com código, nunca uma quebra.

Do outro lado deste barramento está a ponte que alguém escreveu contra o contrato público,
então um quadro que é lixo tem de voltar como ack e deixar o socket de pé. As regras
atacadas aqui: o chip nunca ecoa um DP recebido, um DP só aceita o tipo que declara, um enum
aceita no máximo dez valores, um DP string é só de report e o token da seção 9 não viaja em
lugar nenhum além do primeiro quadro.
"""

import pytest

from iphub.dpbus import mapa
from iphub.dpbus.protocolo import (
    BLOCO_OFFLINE,
    CODIGOS,
    DP_DESCONHECIDO,
    DP_SOMENTE_LEITURA,
    FRAME_INVALIDO,
    ID_MAXIMO,
    NAO_AUTENTICADO,
    T_ACK,
    T_REPORT,
    T_SNAPSHOT,
    TOKEN_MAXIMO,
    VALOR_INVALIDO,
    ack,
    ler_auth,
    ler_set,
    report,
    snapshot,
    valor_valido,
)
from iphub.drivers.base import CODIGOS as CODIGOS_DE_DRIVER

TOKEN = "TvLQKcm3rEGaCFj3TrJmkPKrKPfHbYqYVTV0EahqAqI"


def _set(**campos) -> dict:
    return {"t": "set", "id": 1, "dpid": 101, "v": 30, **campos}


def test_o_vocabulario_e_o_da_secao_8():
    assert set(CODIGOS) == {
        "dp_desconhecido",
        "dp_somente_leitura",
        "valor_invalido",
        "bloco_offline",
        "nao_autenticado",
        "frame_invalido",
    }


def test_nenhum_codigo_e_uma_frase():
    # Section 11: the API answers a stable code and the panel translates it.
    # Seção 11: a API responde um código estável e o painel o traduz.
    for codigo in CODIGOS:
        assert codigo == codigo.lower()
        assert " " not in codigo


def test_um_set_valido_vira_pedido():
    leitura = ler_set(_set())
    assert leitura.codigo == ""
    assert leitura.id == 1
    assert leitura.pedido.dp.dpid == 101
    assert leitura.pedido.valor == 30


@pytest.mark.parametrize("bruto", [None, 7, "set", [], ["t", "set"], True, 1.5])
def test_um_quadro_que_nao_e_objeto_e_recusado(bruto):
    assert ler_set(bruto).codigo == FRAME_INVALIDO


@pytest.mark.parametrize(
    "bruto",
    [
        {},
        {"dpid": 101, "v": 30},
        {"t": "ping", "dpid": 101, "v": 30},
        {"t": "auth", "token": TOKEN},
        {"t": "ack", "dpid": 101, "v": 30},
        {"t": "report", "dpid": 101, "v": 30},
        {"t": "SET", "dpid": 101, "v": 30},
        {"t": 7, "dpid": 101, "v": 30},
        {"t": None, "dpid": 101, "v": 30},
    ],
)
def test_um_t_ausente_ou_desconhecido_e_recusado(bruto):
    assert ler_set(bruto).codigo == FRAME_INVALIDO


@pytest.mark.parametrize("dpid", ["101", 101.0, True, None, [101], {"dpid": 101}])
def test_um_dpid_que_nao_e_numero_e_recusado(dpid):
    leitura = ler_set({"t": "set", "id": 3, "dpid": dpid, "v": 30})
    assert leitura.codigo == FRAME_INVALIDO
    assert leitura.id == 3


def test_um_set_sem_dpid_e_recusado():
    assert ler_set({"t": "set", "id": 1, "v": 30}).codigo == FRAME_INVALIDO


@pytest.mark.parametrize("dpid", [0, 100, 136, 140, 147, 999, -101])
def test_um_dpid_fora_do_contrato_e_recusado(dpid):
    assert ler_set(_set(dpid=dpid)).codigo == DP_DESCONHECIDO


@pytest.mark.parametrize("dpid", [104, 105, 130, 133, 134, 135])
def test_um_set_num_dp_de_report_e_recusado(dpid):
    # Why: online, tocando and the names are born of real state; a bridge that could write
    # them would publish a speaker as online without any speaker having answered.
    # Por que: online, tocando e os nomes nascem de estado real; uma ponte que pudesse
    # escrevê-los publicaria uma caixa como online sem caixa nenhuma ter respondido.
    assert ler_set(_set(dpid=dpid, v="x")).codigo == DP_SOMENTE_LEITURA


def test_um_dp_string_recusa_o_set_antes_de_olhar_o_valor():
    assert ler_set(_set(dpid=105)).codigo == DP_SOMENTE_LEITURA
    assert not valor_valido(mapa.de_dp(105), "Faixa 1")


def test_preset_e_cena_sao_de_envio_e_aceitam_set():
    assert ler_set(_set(dpid=103, v="cmd1")).pedido is not None
    assert ler_set(_set(dpid=mapa.CENA, v="cena3")).pedido is not None


@pytest.mark.parametrize("valor", [0, 50, 100])
def test_o_volume_aceita_a_escala_da_secao_6(valor):
    assert ler_set(_set(v=valor)).pedido.valor == valor


@pytest.mark.parametrize("valor", [-1, 101, 1000, 30.0, "30", True, False, None, [30]])
def test_o_volume_recusa_o_que_nao_e_um_inteiro_de_zero_a_cem(valor):
    # Why: True is an int for Python, so a bool would land as the volume 1 of the block.
    # Por que: True é int para o Python, então um bool chegaria como o volume 1 do bloco.
    assert ler_set(_set(v=valor)).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("valor", [True, False])
def test_o_play_aceita_um_booleano(valor):
    assert ler_set(_set(dpid=102, v=valor)).pedido.valor is valor


@pytest.mark.parametrize("valor", [1, 0, "true", "on", None, 1.0])
def test_o_play_recusa_o_que_nao_e_booleano(valor):
    assert ler_set(_set(dpid=102, v=valor)).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("valor", ["cmd0", "cmd9", "CMD1", "cmd", 1, None, "cena1"])
def test_o_preset_recusa_um_valor_fora_do_enum(valor):
    assert ler_set(_set(dpid=103, v=valor)).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("valor", ["cena0", "cena9", "cmd1", 3])
def test_a_cena_recusa_um_valor_fora_do_enum(valor):
    assert ler_set(_set(dpid=mapa.CENA, v=valor)).codigo == VALOR_INVALIDO


def test_o_grupo_aceita_solo_e_para_no_decimo_valor():
    assert ler_set(_set(dpid=mapa.GRUPO, v="solo")).pedido is not None
    assert ler_set(_set(dpid=mapa.GRUPO, v=f"grupo{mapa.GRUPOS}")).pedido is not None
    fora = ler_set(_set(dpid=mapa.GRUPO, v=f"grupo{mapa.GRUPOS + 1}"))
    assert fora.codigo == VALOR_INVALIDO


def test_a_entrada_sem_valores_declarados_recusa_tudo():
    # Section 14: only the inputs plm_support lists exist, so a bus that guessed would
    # command an input the speaker does not have.
    # Seção 14: só existem as entradas que o plm_support lista, então um barramento que
    # adivinhasse comandaria uma entrada que a caixa não tem.
    assert ler_set(_set(dpid=141, v="wifi")).codigo == VALOR_INVALIDO


def test_a_entrada_aceita_so_o_que_o_hardware_declara():
    fontes = ("wifi", "bluetooth")
    assert ler_set(_set(dpid=141, v="wifi"), valores=fontes).pedido is not None
    assert ler_set(_set(dpid=141, v="usb"), valores=fontes).codigo == VALOR_INVALIDO


def test_a_entrada_para_no_teto_de_dez_valores():
    fontes = tuple(f"fonte{n}" for n in range(1, 13))
    assert ler_set(_set(dpid=141, v="fonte10"), valores=fontes).pedido is not None
    assert ler_set(_set(dpid=141, v="fonte11"), valores=fontes).codigo == VALOR_INVALIDO


@pytest.mark.parametrize("identificador", [1, 0, -3, "abc", "", None])
def test_o_id_volta_como_veio(identificador):
    assert ler_set(_set(id=identificador)).id == identificador


@pytest.mark.parametrize("identificador", ["x" * (ID_MAXIMO + 1), {"a": 1}, [1], 1.5, True, False])
def test_um_id_que_o_ack_teria_de_ecoar_de_volta_e_recusado(identificador):
    # Why: the ack echoes the id, so a client sending a megabyte of id would be answered
    # with a megabyte on every frame.
    # Por que: o ack ecoa o id, então um cliente mandando um megabyte de id receberia um
    # megabyte de volta a cada quadro.
    leitura = ler_set(_set(id=identificador))
    assert leitura.codigo == FRAME_INVALIDO
    assert leitura.id is None


def test_um_id_que_o_utf_8_nao_devolve_e_recusado():
    # Why: the ack echoes the id, so a lone surrogate accepted here would come back as an
    # encoding error on the very frame the client is waiting for.
    # Por que: o ack ecoa o id, então um surrogado solto aceito aqui voltaria como erro de
    # codificação justamente no quadro que o cliente está esperando.
    assert ler_set(_set(id="pedido \ud800")).codigo == FRAME_INVALIDO


def test_o_id_volta_mesmo_quando_o_quadro_e_recusado():
    leitura = ler_set(_set(id="pedido-9", dpid=999))
    assert (leitura.id, leitura.codigo) == ("pedido-9", DP_DESCONHECIDO)


def test_uma_chave_que_o_contrato_nao_nomeia_e_ignorada():
    # The frame is a wire protocol other bridges implement, so an extra key is ignored;
    # nothing reads it and it changes nothing.
    # O quadro é protocolo de fio que outras pontes implementam, então uma chave a mais é
    # ignorada; ninguém a lê e ela não muda nada.
    leitura = ler_set(_set(origem="ponte"))
    assert leitura.pedido.valor == 30


def test_toda_recusa_usa_o_vocabulario():
    quadros = [None, {}, _set(dpid="x"), _set(dpid=999), _set(dpid=104), _set(v="alto")]
    for bruto in quadros:
        assert ler_set(bruto).codigo in CODIGOS


def test_o_ack_diz_ok_sem_codigo():
    assert ack(7) == {"t": T_ACK, "id": 7, "ok": True, "code": None}


def test_o_ack_carrega_o_codigo_que_recusou():
    assert ack("a", BLOCO_OFFLINE) == {"t": T_ACK, "id": "a", "ok": False, "code": BLOCO_OFFLINE}


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
    assert report(101, 30, 1_700_000_000.9) == {
        "t": T_REPORT,
        "dpid": 101,
        "v": 30,
        "ts": 1_700_000_000,
    }


@pytest.mark.parametrize("dpid", [103, 131, 999, 140, "101", None])
def test_um_report_de_dp_que_o_chip_nunca_confirma_e_um_defeito(dpid):
    # Why: the chip never echoes, so a report of a send only DP would publish a state that
    # no device confirmed; it is a defect of whoever built it, not a bad frame from outside.
    # Por que: o chip nunca ecoa, então um report de um DP de só envio publicaria um estado
    # que aparelho nenhum confirmou; é defeito de quem o montou, não quadro ruim de fora.
    with pytest.raises(ValueError):
        report(dpid, "cmd1", 1.0)


@pytest.mark.parametrize(
    ("dpid", "valor"),
    [(101, "30"), (101, 101), (101, True), (104, 1), (105, 7), (141, ""), (141, 3)],
)
def test_um_report_com_o_tipo_errado_e_recusado(dpid, valor):
    with pytest.raises(ValueError):
        report(dpid, valor, 1.0)


def test_um_report_de_entrada_nao_precisa_da_lista_de_valores():
    assert report(141, "wifi", 1.0)["v"] == "wifi"


def test_um_titulo_longo_demais_e_encurtado_e_nao_derruba_o_barramento():
    # Section 14: the title comes from the firmware and is not to be trusted blindly.
    # Seção 14: o título vem do firmware e não é para ser acreditado às cegas.
    quadro = report(105, "ç" * 400, 1.0)
    assert len(quadro["v"].encode("utf-8")) <= mapa.TEXTO_MAXIMO_BYTES


def test_um_json_de_nomes_longo_demais_e_recusado_em_vez_de_cortado():
    # Why: a cut JSON is not JSON, so the bridge would read the names of six blocks as garbage.
    # Por que: um JSON cortado não é JSON, então a ponte leria os nomes de seis blocos como lixo.
    with pytest.raises(ValueError):
        report(mapa.NOMES_BLOCOS, '{"z":["' + "x" * 300 + '"]}', 1.0)


def test_o_snapshot_leva_so_o_que_pode_ser_reportado():
    valores = {101: 30, 103: "cmd1", 104: True, 131: "cena1", 999: "x"}
    dps = snapshot(valores)["dps"]
    assert snapshot(valores)["t"] == T_SNAPSHOT
    assert dps == {"101": 30, "104": True}


def test_o_snapshot_omite_um_dp_que_ainda_nao_tem_valor():
    assert snapshot({101: None, 104: False})["dps"] == {"104": False}


def test_o_snapshot_sai_na_ordem_da_secao_8():
    valores = {130: "Faixa", 101: 30, 104: True}
    assert list(snapshot(valores)["dps"]) == ["101", "104", "130"]


def test_o_auth_devolve_o_token_do_primeiro_quadro():
    assert ler_auth({"t": "auth", "token": TOKEN}) == TOKEN


@pytest.mark.parametrize(
    "bruto",
    [
        None,
        [],
        "auth",
        {},
        {"t": "set", "token": TOKEN},
        {"t": "auth"},
        {"t": "auth", "token": ""},
        {"t": "auth", "token": 7},
        {"t": "auth", "token": None},
        {"t": "auth", "token": ["x"]},
        {"t": "auth", "token": "x" * (TOKEN_MAXIMO + 1)},
    ],
)
def test_um_quadro_que_nao_e_o_auth_da_secao_8_nao_autentica(bruto):
    assert ler_auth(bruto) == ""


@pytest.mark.parametrize("token", ["tökén-de-maquina", "\ud800", "senha ção"])
def test_um_token_fora_do_ascii_e_recusado_em_vez_de_estourar(token):
    # Why: the api_token is a token_urlsafe, which is ASCII, and comparing a non ASCII
    # string in constant time raises instead of answering that it does not match.
    # Por que: o api_token é um token_urlsafe, que é ASCII, e comparar uma string não ASCII
    # em tempo constante estoura em vez de responder que não casa.
    assert ler_auth({"t": "auth", "token": token}) == ""


def test_o_codigo_de_nao_autenticado_existe_para_o_socket_recusar():
    assert NAO_AUTENTICADO in CODIGOS
