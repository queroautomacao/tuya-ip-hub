# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 under attack: the numbering is written here by hand and the code has to match.

The table below is transcribed from the document, not generated from the module, because a
test that built the numbers the same way the code does would agree with a wrong formula.
The rest attacks the three platform rules: the chip never echoes a received DP, an enum
takes at most ten values, and a string DP carries 255 bytes and not one more.

Seção 8 sob ataque: a numeração está escrita aqui à mão e o código tem de casar com ela.

A tabela abaixo é transcrita do documento, não gerada do módulo, porque um teste que
montasse os números do mesmo jeito que o código concordaria com uma fórmula errada. O resto
ataca as três regras de plataforma: o chip nunca ecoa um DP recebido, um enum aceita no
máximo dez valores, e um DP string carrega 255 bytes e nem um a mais.
"""

import json

import pytest

from iphub.dpbus.mapa import (
    AJUSTAVEIS,
    CENA,
    CODIGOS_DE_NOMES,
    DPS,
    ENUM_MAXIMO,
    FUNCOES_GLOBAIS,
    FUNCOES_ZONA,
    GRUPO,
    GRUPOS,
    MAPA,
    NOME_NAO_GRAVAVEL,
    NOMES_CENAS,
    NOMES_DEMAIS,
    NOMES_GRUPOS,
    NOMES_LONGOS,
    NOMES_ZONAS,
    REPORTAVEIS,
    TEXTO_MAXIMO_BYTES,
    THROTTLE_TOCANDO_S,
    VALORES_CENA,
    VALORES_GRUPO,
    VALORES_PRESET,
    ZONAS,
    NomesInvalidos,
    Sentido,
    Tipo,
    da_zona,
    de_dp,
    dp_de,
    nomes_cabem,
    nomes_json,
    texto_de_dp,
    valores_de_enum,
)

# The whole of the section 8 table, copied from CLAUDE.md; zona 0 is a global data point.
# A tabela inteira da seção 8, copiada do CLAUDE.md; a zona 0 é um data point global.
SECAO_8 = {
    101: (1, "volume"),
    102: (1, "play"),
    103: (1, "preset"),
    104: (1, "online"),
    105: (1, "tocando"),
    106: (2, "volume"),
    107: (2, "play"),
    108: (2, "preset"),
    109: (2, "online"),
    110: (2, "tocando"),
    111: (3, "volume"),
    112: (3, "play"),
    113: (3, "preset"),
    114: (3, "online"),
    115: (3, "tocando"),
    116: (4, "volume"),
    117: (4, "play"),
    118: (4, "preset"),
    119: (4, "online"),
    120: (4, "tocando"),
    121: (5, "volume"),
    122: (5, "play"),
    123: (5, "preset"),
    124: (5, "online"),
    125: (5, "tocando"),
    126: (6, "volume"),
    127: (6, "play"),
    128: (6, "preset"),
    129: (6, "online"),
    130: (6, "tocando"),
    131: (0, "cena"),
    132: (0, "grupo"),
    133: (0, "nomes_zonas"),
    134: (0, "nomes_cenas"),
    135: (0, "nomes_grupos"),
    141: (1, "entrada"),
    142: (2, "entrada"),
    143: (3, "entrada"),
    144: (4, "entrada"),
    145: (5, "entrada"),
    146: (6, "entrada"),
}

SOMENTE_ENVIO = (103, 108, 113, 118, 123, 128, 131)
SOMENTE_REPORTE = (104, 105, 109, 110, 114, 115, 119, 120, 124, 125, 129, 130, 133, 134, 135)

FORA_DO_CONTRATO = (0, 1, 100, 136, 137, 138, 139, 140, 147, 200, -101)

# Overhead of the compact JSON of one name in DP 133: {"z":[" plus "]}.
# Peso do JSON compacto de um nome no DP 133: {"z":[" mais "]}.
MOLDURA_DE_UM_NOME = 10


def _nome(tamanho: int, acentos: int = 0) -> str:
    """A name of exactly tamanho characters, acentos of them costing two bytes in UTF-8.

    Um nome de exatamente tamanho caracteres, acentos deles custando dois bytes em UTF-8.
    """
    return "á" * acentos + "z" * (tamanho - acentos)


def test_a_tabela_e_a_da_secao_8():
    assert set(MAPA) == set(SECAO_8)
    encontrado = {dp.dpid: (dp.zona, dp.funcao) for dp in DPS}
    assert encontrado == SECAO_8


def test_o_vocabulario_de_funcoes_e_o_da_tabela():
    assert set(FUNCOES_ZONA) == {dp.funcao for dp in DPS if dp.zona}
    assert set(FUNCOES_GLOBAIS) == {dp.funcao for dp in DPS if not dp.zona}


def test_os_codigos_de_nomes_sao_estaveis_e_nenhum_e_uma_frase():
    # Section 11: whoever refuses a name answers a code and the panel translates it.
    # Seção 11: quem recusa um nome responde um código e o painel o traduz.
    assert set(CODIGOS_DE_NOMES) == {NOMES_DEMAIS, NOMES_LONGOS, NOME_NAO_GRAVAVEL}
    for codigo in CODIGOS_DE_NOMES:
        assert codigo == codigo.lower()
        assert " " not in codigo


def test_toda_zona_tem_seis_data_points_e_sao_seis_zonas():
    assert ZONAS == 6
    for zona in range(1, ZONAS + 1):
        assert len(da_zona(zona)) == 6
    assert not da_zona(7)


def test_o_numero_vai_e_volta():
    for dpid, (zona, funcao) in SECAO_8.items():
        assert dp_de(zona, funcao) == dpid
        dp = de_dp(dpid)
        assert dp is not None
        assert (dp.zona, dp.funcao) == (zona, funcao)


@pytest.mark.parametrize(
    ("zona", "funcao"),
    [
        (0, "volume"),
        (7, "volume"),
        (-1, "volume"),
        (1, "clima"),
        (1, ""),
        (1, "cena"),
        (0, "entrada"),
        (0, "volume"),
    ],
)
def test_dp_de_recusa_um_par_fora_da_tabela(zona, funcao):
    with pytest.raises(ValueError):
        dp_de(zona, funcao)


@pytest.mark.parametrize("dpid", FORA_DO_CONTRATO)
def test_de_dp_recusa_um_numero_fora_do_contrato(dpid):
    assert de_dp(dpid) is None


@pytest.mark.parametrize("dpid", [True, False, 101.0, "101", None, [101], 101j])
def test_de_dp_recusa_o_que_nao_e_um_inteiro(dpid):
    # Why: the JSON true is an int for Python, and True would resolve to the volume of zone 1.
    # Por que: o true do JSON é int para o Python, e True resolveria o volume da zona 1.
    assert de_dp(dpid) is None


@pytest.mark.parametrize("dpid", SOMENTE_ENVIO)
def test_o_chip_nunca_ecoa_entao_preset_e_cena_nao_sao_reportaveis(dpid):
    dp = de_dp(dpid)
    assert dp.sentido is Sentido.ENVIO
    assert not dp.reportavel
    assert dp.ajustavel
    assert dpid not in REPORTAVEIS


@pytest.mark.parametrize("dpid", SOMENTE_REPORTE)
def test_online_tocando_e_os_nomes_nunca_aceitam_um_set(dpid):
    dp = de_dp(dpid)
    assert dp.sentido is Sentido.REPORTE
    assert not dp.ajustavel
    assert dp.reportavel
    assert dpid not in AJUSTAVEIS


def test_volume_play_grupo_e_entrada_vao_nos_dois_sentidos():
    for dpid in (101, 102, 132, 141):
        dp = de_dp(dpid)
        assert dp.sentido is Sentido.RW
        assert dp.reportavel and dp.ajustavel


def test_os_tipos_sao_os_da_secao_8():
    assert de_dp(101).tipo is Tipo.VALOR
    assert de_dp(102).tipo is Tipo.BOOL
    assert de_dp(103).tipo is Tipo.ENUM
    assert de_dp(104).tipo is Tipo.BOOL
    assert de_dp(105).tipo is Tipo.TEXTO
    assert de_dp(141).tipo is Tipo.ENUM
    for dpid in (NOMES_ZONAS, NOMES_CENAS, NOMES_GRUPOS):
        assert de_dp(dpid).tipo is Tipo.TEXTO


def test_so_o_tocando_tem_o_throttle_de_cinco_segundos():
    assert de_dp(105).throttle_s == THROTTLE_TOCANDO_S == 5.0
    outros = [dp.dpid for dp in DPS if dp.throttle_s and dp.funcao != "tocando"]
    assert not outros


def test_so_o_tocando_e_texto_livre():
    livres = {dp.funcao for dp in DPS if dp.texto_livre}
    assert livres == {"tocando"}


def test_nenhum_enum_passa_do_teto_de_dez_valores():
    # Why: a custom enum takes at most ten values on the platform, so an eleventh would make
    # the platform refuse the whole DP and take the function off the bus.
    # Por que: um enum customizado aceita no máximo dez valores na plataforma, então um
    # décimo primeiro faria a plataforma recusar o DP inteiro e tirar a função do barramento.
    for dp in DPS:
        assert len(dp.valores) <= ENUM_MAXIMO


def test_os_valores_fixos_sao_os_do_documento():
    assert VALORES_PRESET == tuple(f"cmd{n}" for n in range(1, 9))
    assert VALORES_CENA == tuple(f"cena{n}" for n in range(1, 9))
    assert VALORES_GRUPO[0] == "solo"
    assert VALORES_GRUPO[-1] == f"grupo{GRUPOS}"
    # Why: a group is named after the zone that leads it, so section 8 offers one per block
    # and no more: a grupo7 would be a value the panel offers and a scene saves that no zone
    # can ever name. It stays under the ceiling of ten the platform imposes.
    # Por que: um grupo tem o nome da zona que o lidera, então a seção 8 oferece um por bloco e
    # nada além: um grupo7 seria valor que o painel oferece e uma cena salva que nenhuma zona
    # consegue nomear. Fica abaixo do teto de dez que a plataforma impõe.
    assert len(VALORES_GRUPO) == ZONAS + 1
    assert len(VALORES_GRUPO) <= ENUM_MAXIMO
    assert de_dp(103).valores == VALORES_PRESET
    assert de_dp(CENA).valores == VALORES_CENA
    assert de_dp(GRUPO).valores == VALORES_GRUPO


def test_a_entrada_nao_declara_valores_porque_o_hardware_os_declara():
    # Section 14: only the inputs plm_support lists exist, so the map cannot fix them.
    # Seção 14: só existem as entradas que o plm_support lista, então o mapa não as fixa.
    assert de_dp(141).valores == ()


def test_valores_de_enum_para_no_teto_da_plataforma():
    fontes = tuple(f"fonte{n}" for n in range(1, 15))
    assert valores_de_enum(fontes) == fontes[:ENUM_MAXIMO]


def test_valores_de_enum_descarta_repetido_e_vazio():
    assert valores_de_enum(["wifi", "wifi", "", "bluetooth", None, "wifi"]) == (
        "wifi",
        "bluetooth",
    )


def test_o_json_de_nomes_e_compacto_e_mantem_o_acento():
    texto = nomes_json(NOMES_ZONAS, ["Sala", "Cozinha", "Área"])
    assert texto == '{"z":["Sala","Cozinha","Área"]}'
    assert json.loads(texto) == {"z": ["Sala", "Cozinha", "Área"]}


def test_cada_dp_de_nomes_tem_a_sua_chave():
    assert nomes_json(NOMES_ZONAS, ["a"]).startswith('{"z"')
    assert nomes_json(NOMES_CENAS, ["a"]).startswith('{"c"')
    assert nomes_json(NOMES_GRUPOS, ["a"]).startswith('{"g"')


def test_duzentos_e_cinquenta_e_cinco_bytes_passam_e_o_byte_seguinte_nao():
    cabe = [_nome(TEXTO_MAXIMO_BYTES - MOLDURA_DE_UM_NOME)]
    assert len(nomes_json(NOMES_ZONAS, cabe).encode("utf-8")) == TEXTO_MAXIMO_BYTES
    nao_cabe = [_nome(TEXTO_MAXIMO_BYTES - MOLDURA_DE_UM_NOME + 1)]
    with pytest.raises(NomesInvalidos) as erro:
        nomes_json(NOMES_ZONAS, nao_cabe)
    assert erro.value.codigo == NOMES_LONGOS
    assert not nomes_cabem(NOMES_ZONAS, nao_cabe)


def test_um_byte_a_mais_e_contado_em_bytes_e_nao_em_caracteres():
    # Why: an accented letter is one character and two bytes, and a ceiling counted in
    # characters would hand the platform a 260 byte string that it refuses whole.
    # Por que: uma letra acentuada é um caractere e dois bytes, e um teto contado em
    # caracteres entregaria à plataforma uma string de 260 bytes que ela recusa inteira.
    limite = TEXTO_MAXIMO_BYTES - MOLDURA_DE_UM_NOME
    with pytest.raises(NomesInvalidos) as erro:
        nomes_json(NOMES_ZONAS, [_nome(limite, acentos=1)])
    assert erro.value.codigo == NOMES_LONGOS


def test_seis_zonas_com_nome_longo_e_acentuado_ainda_cabem():
    # The bench fact of section 14: zone, scene and group names fit 255 bytes with six zones.
    # O fato de bancada da seção 14: nomes de zona, cena e grupo cabem em 255 bytes com seis.
    nomes = [_nome(34, acentos=4) for _ in range(ZONAS)]
    texto = nomes_json(NOMES_ZONAS, nomes)
    assert len(texto.encode("utf-8")) <= TEXTO_MAXIMO_BYTES
    escapado = json.dumps({"z": nomes}, ensure_ascii=True, separators=(",", ":"))
    assert len(escapado.encode("utf-8")) > TEXTO_MAXIMO_BYTES


def test_mais_nomes_do_que_o_dp_carrega_e_recusado():
    for dpid, quantos in ((NOMES_ZONAS, ZONAS), (NOMES_CENAS, 8), (NOMES_GRUPOS, GRUPOS)):
        assert nomes_cabem(dpid, ["x"] * quantos)
        with pytest.raises(NomesInvalidos) as erro:
            nomes_json(dpid, ["x"] * (quantos + 1))
        assert erro.value.codigo == NOMES_DEMAIS


def test_um_nome_que_o_utf_8_nao_escreve_e_recusado_com_codigo():
    # Why: a lone surrogate would raise on the way out of the socket, where the honest
    # answer is that the name is not writable and the panel has to say so.
    # Por que: um surrogado solto estouraria na saída do socket, onde a resposta honesta é
    # que o nome não é gravável e o painel tem de dizer isso.
    with pytest.raises(NomesInvalidos) as erro:
        nomes_json(NOMES_CENAS, ["festa \ud800"])
    assert erro.value.codigo == NOME_NAO_GRAVAVEL
    assert not nomes_cabem(NOMES_CENAS, ["festa \ud800"])


def test_nomes_json_recusa_um_dp_que_nao_e_de_nomes():
    for dpid in (101, 105, CENA, GRUPO, 999):
        with pytest.raises(ValueError):
            nomes_json(dpid, ["x"])


def test_nomes_json_recusa_um_nome_que_nao_e_texto():
    with pytest.raises(ValueError):
        nomes_json(NOMES_ZONAS, ["Sala", 7])


def test_um_texto_livre_e_encurtado_sem_partir_um_caractere():
    titulo = "ç" * 300
    curto = texto_de_dp(titulo)
    bruto = curto.encode("utf-8")
    assert len(bruto) <= TEXTO_MAXIMO_BYTES
    assert bruto.decode("utf-8") == curto
    assert titulo.startswith(curto)


def test_um_texto_que_cabe_nao_e_tocado():
    assert texto_de_dp("Fita Cassete") == "Fita Cassete"
    assert texto_de_dp("") == ""


def test_um_texto_livre_com_surrogado_nao_estoura():
    assert texto_de_dp("faixa \ud800 boa") == "faixa  boa"
