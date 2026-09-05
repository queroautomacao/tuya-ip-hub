# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 under attack: the numbering of the two products is written here by hand and the
code has to match.

The tables below are transcribed from the document, not generated from the module, because a
test that built the numbers the same way the code does would agree with a wrong formula.
The rest attacks the platform rules: the chip never echoes a received DP, an enum takes at
most ten values, a string carries 255 bytes and not one more, and every data point carries
the report class the policy of section 8 reads.

Seção 8 sob ataque: a numeração dos dois produtos está escrita aqui à mão e o código tem de
casar com ela.

As tabelas abaixo são transcritas do documento, não geradas do módulo, porque um teste que
montasse os números do mesmo jeito que o código concordaria com uma fórmula errada. O resto
ataca as regras de plataforma: o chip nunca ecoa um DP recebido, um enum aceita no máximo dez
valores, uma string carrega 255 bytes e nem um a mais, e todo data point carrega a classe de
report que a política da seção 8 lê.
"""

import json

import pytest

from iphub.dpbus import mapa
from iphub.dpbus.mapa import (
    CENAS,
    ENUM_MAXIMO,
    NUMEROS,
    PRODUTO_AR,
    PRODUTO_AV,
    TEXTO_MAXIMO_BYTES,
    Classe,
    NomesInvalidos,
    Sentido,
    Tipo,
    bits,
    de_dp,
    desempacotar,
    dp_de,
    dps_de,
    empacotar,
    nomes_cabem,
    nomes_das_cenas,
    nomes_das_maquinas,
    numero_de_cena,
    pares,
    titulos,
)

# The product of air, copied from section 8: machine k starts at 101 + 5(k - 1).
# O produto de ar, copiado da seção 8: a máquina k começa em 101 + 5(k - 1).
AR = {
    **{101 + 5 * (k - 1): (k, "ligado") for k in range(1, 9)},
    **{102 + 5 * (k - 1): (k, "temperatura") for k in range(1, 9)},
    **{103 + 5 * (k - 1): (k, "modo") for k in range(1, 9)},
    **{104 + 5 * (k - 1): (k, "vento") for k in range(1, 9)},
    171: (0, "cena"),
    172: (0, "online"),
    173: (0, "nomes"),
    174: (0, "nomes_cenas"),
    175: (0, "nomes_cenas"),
}

# The product of audio and video: ligado at 100 + n, nivel at 120 + n, the installation at 141.
# O produto de áudio e vídeo: ligado em 100 + n, nível em 120 + n, a instalação no 141.
AV = {
    **{100 + n: (n, "ligado") for n in range(1, 13)},
    **{120 + n: (n, "nivel") for n in range(1, 13)},
    141: (0, "cena"),
    142: (0, "grupo"),
    143: (0, "comando"),
    144: (0, "online"),
    145: (0, "mudos"),
    146: (0, "entradas"),
    147: (0, "modos"),
    148: (0, "titulos"),
    149: (0, "perfis"),
    150: (0, "perfis"),
    151: (0, "perfis"),
    152: (0, "perfis"),
    153: (0, "perfis"),
    154: (0, "nomes_cenas"),
    155: (0, "nomes_cenas"),
}

SOMENTE_ENVIO = {PRODUTO_AR: (171,), PRODUTO_AV: (141, 143)}
SOMENTE_REPORTE = {
    PRODUTO_AR: (172, 173, 174, 175),
    PRODUTO_AV: tuple(range(144, 156)),
}
FORA_DO_CONTRATO = {
    PRODUTO_AR: (0, 1, 100, 105, 110, 140, 170, 176, 200, -101),
    PRODUTO_AV: (0, 1, 100, 113, 120, 133, 140, 156, 200, -101),
}


def test_as_tabelas_sao_as_da_secao_8():
    assert {dp.dpid: (dp.numero, dp.funcao) for dp in mapa.tabela(PRODUTO_AR)} == AR
    assert {dp.dpid: (dp.numero, dp.funcao) for dp in mapa.tabela(PRODUTO_AV)} == AV


def test_os_produtos_e_os_numeros_sao_os_do_documento():
    assert mapa.PRODUTOS == ("ar", "av")
    assert NUMEROS == {"ar": 8, "av": 12}
    assert CENAS == 32
    assert len(mapa.tabela(PRODUTO_AR)) == 37
    assert len(mapa.tabela(PRODUTO_AV)) == 39


def test_o_numero_vai_e_volta():
    for produto, tabela in ((PRODUTO_AR, AR), (PRODUTO_AV, AV)):
        for dpid, (numero, funcao) in tabela.items():
            dp = de_dp(produto, dpid)
            assert dp is not None and dp.produto == produto
            if funcao in ("perfis", "nomes_cenas"):
                assert dp_de(produto, funcao, numero, dp.indice) == dpid
            else:
                assert dp_de(produto, funcao, numero) == dpid


def test_as_partes_de_uma_string_espalhada_tem_indice():
    assert [dp.indice for dp in dps_de(PRODUTO_AV, "perfis")] == [1, 2, 3, 4, 5]
    assert [dp.indice for dp in dps_de(PRODUTO_AR, "nomes_cenas")] == [1, 2]
    assert dp_de(PRODUTO_AV, "perfis", indice=3) == 151


@pytest.mark.parametrize(
    ("produto", "funcao", "numero"),
    [
        (PRODUTO_AR, "ligado", 0),
        (PRODUTO_AR, "ligado", 9),
        (PRODUTO_AR, "nivel", 1),
        (PRODUTO_AV, "temperatura", 1),
        (PRODUTO_AV, "nivel", 13),
        ("tv", "ligado", 1),
    ],
)
def test_dp_de_recusa_uma_combinacao_fora_da_tabela(produto, funcao, numero):
    with pytest.raises(ValueError):
        dp_de(produto, funcao, numero)


@pytest.mark.parametrize("produto", [PRODUTO_AR, PRODUTO_AV])
def test_de_dp_recusa_um_numero_fora_do_contrato(produto):
    for dpid in FORA_DO_CONTRATO[produto]:
        assert de_dp(produto, dpid) is None, (produto, dpid)


@pytest.mark.parametrize("dpid", [True, "101", 101.0, None, [101]])
def test_de_dp_recusa_o_que_nao_e_um_inteiro(dpid):
    assert de_dp(PRODUTO_AV, dpid) is None


def test_de_dp_recusa_um_produto_que_o_contrato_nao_nomeia():
    assert de_dp("tv", 101) is None
    assert de_dp(None, 101) is None


@pytest.mark.parametrize("produto", [PRODUTO_AR, PRODUTO_AV])
def test_o_chip_nunca_ecoa_entao_cena_e_comando_nao_sao_reportaveis(produto):
    for dpid in SOMENTE_ENVIO[produto]:
        dp = de_dp(produto, dpid)
        assert dp.sentido is Sentido.ENVIO and not dp.reportavel and dp.ajustavel


@pytest.mark.parametrize("produto", [PRODUTO_AR, PRODUTO_AV])
def test_o_que_nasce_de_estado_real_nunca_aceita_um_set(produto):
    for dpid in SOMENTE_REPORTE[produto]:
        dp = de_dp(produto, dpid)
        assert dp.sentido is Sentido.REPORTE and dp.reportavel and not dp.ajustavel


def test_o_que_a_automacao_alcanca_vai_nos_dois_sentidos():
    for dp in (*mapa.tabela(PRODUTO_AR), *mapa.tabela(PRODUTO_AV)):
        if dp.funcao in ("ligado", "temperatura", "modo", "vento", "nivel", "grupo"):
            assert dp.sentido is Sentido.RW, dp
            assert dp.tipo in (Tipo.BOOL, Tipo.VALOR, Tipo.ENUM), dp


def test_os_tipos_sao_os_da_secao_8():
    assert de_dp(PRODUTO_AR, 101).tipo is Tipo.BOOL
    assert de_dp(PRODUTO_AR, 102).tipo is Tipo.VALOR
    assert (de_dp(PRODUTO_AR, 102).minimo, de_dp(PRODUTO_AR, 102).maximo) == (16, 30)
    assert de_dp(PRODUTO_AR, 103).valores == ("auto", "frio", "quente", "vento", "seco")
    assert de_dp(PRODUTO_AR, 104).valores == ("auto", "baixo", "medio", "alto")
    assert de_dp(PRODUTO_AV, 121).tipo is Tipo.VALOR
    assert (de_dp(PRODUTO_AV, 121).minimo, de_dp(PRODUTO_AV, 121).maximo) == (0, 100)
    assert (de_dp(PRODUTO_AV, 141).minimo, de_dp(PRODUTO_AV, 141).maximo) == (1, 32)
    assert (de_dp(PRODUTO_AV, 142).minimo, de_dp(PRODUTO_AV, 142).maximo) == (0, 12)
    assert de_dp(PRODUTO_AV, 143).tipo is Tipo.TEXTO
    assert de_dp(PRODUTO_AV, 144).maximo == (1 << 12) - 1
    assert de_dp(PRODUTO_AR, 172).maximo == (1 << 8) - 1


def test_nenhum_enum_passa_do_teto_de_dez_valores():
    for dp in (*mapa.tabela(PRODUTO_AR), *mapa.tabela(PRODUTO_AV)):
        if dp.tipo is Tipo.ENUM:
            assert 0 < len(dp.valores) <= ENUM_MAXIMO
            assert all(len(v) <= 15 and v.replace("_", "").isalnum() for v in dp.valores)


# Why: the report policy of section 8 reads the class of every data point, so a data point
# of the wrong class would be pushed at the wrong cadence; the classes are the document's.
# Por que: a política de reports da seção 8 lê a classe de todo data point, então um data
# point da classe errada seria empurrado na cadência errada; as classes são as do documento.
def test_as_classes_de_report_sao_as_do_documento():
    classes = {dp.funcao: dp.classe for dp in (*mapa.tabela(PRODUTO_AR), *mapa.tabela(PRODUTO_AV))}
    assert classes["ligado"] is Classe.A
    assert classes["temperatura"] is Classe.A
    assert classes["modo"] is Classe.A
    assert classes["vento"] is Classe.A
    assert classes["nivel"] is Classe.A
    assert classes["grupo"] is Classe.A
    assert classes["online"] is Classe.A
    assert classes["mudos"] is Classe.B
    assert classes["entradas"] is Classe.B
    assert classes["modos"] is Classe.B
    assert classes["titulos"] is Classe.C
    assert classes["perfis"] is Classe.C
    assert classes["nomes"] is Classe.C
    assert classes["nomes_cenas"] is Classe.C
    assert mapa.JANELAS_S == {Classe.A: 2.0, Classe.B: 10.0, Classe.C: 0.0}


def test_so_o_titulo_nunca_e_empurrado():
    nunca = [dp.funcao for dp in mapa.tabela(PRODUTO_AV) if not dp.empurrado]
    assert nunca == ["titulos"]
    assert all(dp.empurrado for dp in mapa.tabela(PRODUTO_AR))


def test_a_politica_de_reports_e_a_da_secao_8():
    assert mapa.REPORTS_POR_DIA == 300
    assert mapa.AVISO_DO_DIA == 250
    assert mapa.JANELA_APERTADA_S == 30.0


def test_o_numero_de_cena_e_um_inteiro_de_um_a_trinta_e_dois():
    assert numero_de_cena(1) == 1
    assert numero_de_cena(32) == 32
    for fora in (0, 33, True, "7", 7.0, None):
        assert numero_de_cena(fora) is None


def test_os_bits_poem_o_numero_n_no_bit_n_menos_um():
    assert bits([]) == 0
    assert bits([1]) == 1
    assert bits([1, 3, 12]) == 0b1000_0000_0101
    assert bits([0, -1]) == 0


def test_os_pares_saem_na_ordem_dos_numeros():
    assert pares({}) == ""
    assert pares({3: 2, 1: 1}) == "1=1;3=2"


def test_os_titulos_cabem_em_dezoito_caracteres_e_nunca_carregam_o_separador():
    texto = titulos({1: "Bohemian Rhapsody, Queen; 1975", 2: "a=b"})
    assert texto == "1=Bohemian Rhapsody,;2=a b"
    assert len(texto.split(";")[0]) == 2 + 18


def test_os_titulos_de_doze_equipamentos_cabem_nos_255_bytes():
    texto = titulos({n: "ã" * 18 for n in range(1, 13)})
    assert len(texto.encode("utf-8")) <= TEXTO_MAXIMO_BYTES
    assert texto.count(";") < 12


def test_empacotar_enche_cada_string_ate_os_255_bytes_e_preenche_as_vagas():
    perfis = [f"{n}|au|Caixa {n}|Wi-Fi,Bluetooth|Radio 1,Radio 2||NMEP" for n in range(1, 13)]
    partes = empacotar(perfis)
    assert len(partes) == 5
    assert all(len(parte.encode("utf-8")) <= TEXTO_MAXIMO_BYTES for parte in partes)
    assert desempacotar(partes) == tuple(perfis)
    assert partes[-1] == ""


def test_empacotar_recusa_o_que_nao_cabe_em_cinco_strings():
    with pytest.raises(NomesInvalidos) as erro:
        empacotar(["z" * 200] * 12)
    assert erro.value.codigo == "perfis_longos"


def test_empacotar_recusa_um_perfil_com_o_separador_ou_maior_que_uma_string():
    with pytest.raises(NomesInvalidos):
        empacotar(["1|au|a;b||||N"])
    with pytest.raises(NomesInvalidos):
        empacotar(["z" * 256])


def test_os_nomes_das_cenas_vao_em_duas_strings_de_dezesseis():
    nomes = [f"Cena {n}" for n in range(1, 33)]
    primeira, segunda = nomes_das_cenas(nomes)
    assert json.loads(primeira) == {"c": nomes[:16]}
    assert json.loads(segunda) == {"c": nomes[16:]}
    assert nomes_das_cenas([]) == ('{"c":[]}', '{"c":[]}')
    assert nomes_cabem(nomes)


def test_mais_de_trinta_e_duas_cenas_e_recusado():
    with pytest.raises(NomesInvalidos) as erro:
        nomes_das_cenas([""] * 33)
    assert erro.value.codigo == "nomes_demais"


def test_dezesseis_nomes_que_nao_cabem_nos_255_bytes_sao_recusados_com_codigo():
    with pytest.raises(NomesInvalidos) as erro:
        nomes_das_cenas(["z" * 20] * 16)
    assert erro.value.codigo == "nomes_longos"
    assert not nomes_cabem(["z" * 20] * 16)


def test_um_nome_que_o_utf_8_nao_escreve_e_recusado_com_codigo():
    with pytest.raises(NomesInvalidos) as erro:
        nomes_das_cenas(["\ud800"])
    assert erro.value.codigo == "nome_nao_gravavel"


def test_os_nomes_das_maquinas_sao_compactos_e_mantem_o_acento():
    assert nomes_das_maquinas(["Suíte", ""]) == '{"m":["Suíte",""]}'
    with pytest.raises(NomesInvalidos):
        nomes_das_maquinas([""] * 9)


def test_um_texto_livre_e_encurtado_sem_partir_um_caractere():
    texto = mapa.texto_de_dp("ã" * 200)
    assert len(texto.encode("utf-8")) <= TEXTO_MAXIMO_BYTES
    assert texto == "ã" * 127
    assert mapa.texto_de_dp("abc") == "abc"
    assert mapa.texto_de_dp("a\ud800b") == "ab"


def test_os_codigos_de_nomes_sao_estaveis_e_nenhum_e_uma_frase():
    assert mapa.CODIGOS_DE_NOMES == (
        "nomes_demais",
        "nomes_longos",
        "nome_nao_gravavel",
        "perfis_longos",
    )
    assert all(codigo.replace("_", "").isalnum() for codigo in mapa.CODIGOS_DE_NOMES)


def test_um_titulo_com_surrogado_solto_nunca_chega_ao_fio():
    """A device may answer a lone surrogate in a title, which is the one thing a str holds
    that UTF-8 cannot write; it is dropped so the snapshot of the licence always encodes.

    Um aparelho pode responder um surrogado solto num título, que é a única coisa que um str
    guarda e o UTF-8 não escreve; ele cai para o snapshot da licença sempre codificar.
    """
    texto = titulos({1: "Ab\ud800c", 2: "\udfff"})
    assert texto == "1=Abc;2="
    texto.encode("utf-8")
