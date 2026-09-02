# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: the ownership code and the api_token, generated on first boot and kept 0600.

Seção 9: o código de posse e o api_token, gerados no primeiro boot e guardados 0600.
"""

import os
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from iphub import arquivos, segredos

FORMATO_CODIGO = re.compile(r"[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){3}")
FORMATO_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,}")
CODIGO_REAL = "ABCD-EFGH-JKLM-NPQR"


@pytest.fixture
def dir_data(tmp_path: Path) -> Path:
    caminho = tmp_path / "data"
    caminho.mkdir()
    return caminho


@pytest.fixture
def umask_aberto():
    """The widest umask a container can inherit; nothing may reach the group or the world.

    O umask mais aberto que um container pode herdar; nada pode chegar ao grupo ou ao mundo.
    """
    anterior = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(anterior)


def test_codigo_tem_o_formato_ditavel():
    assert FORMATO_CODIGO.fullmatch(segredos.gerar_codigo_de_posse())


@pytest.mark.parametrize("ambiguo", ["I", "O", "0", "1"])
def test_codigo_nao_usa_caractere_ambiguo(ambiguo):
    # Why: the code is dictated over the phone; a code with I and 1 in it is typed wrong.
    # Por que: o código é ditado por telefone; um código com I e 1 é digitado errado.
    assert ambiguo not in segredos.CODIGO_ALFABETO
    assert ambiguo not in "".join(segredos.gerar_codigo_de_posse() for _ in range(50))


def test_codigo_nao_se_repete():
    assert len({segredos.gerar_codigo_de_posse() for _ in range(50)}) == 50


def test_api_token_e_aleatorio_e_urlsafe():
    tokens = {segredos.gerar_api_token() for _ in range(20)}
    assert len(tokens) == 20
    assert all(FORMATO_TOKEN.fullmatch(t) for t in tokens)


def test_abrir_gera_os_dois_arquivos_0600(dir_data: Path):
    segredo = segredos.abrir(dir_data)
    for nome in (segredos.ARQUIVO_CODIGO, segredos.ARQUIVO_TOKEN):
        assert arquivos.modo_de(dir_data / nome) == 0o600
    assert FORMATO_CODIGO.fullmatch(segredo.codigo_de_posse)
    assert FORMATO_TOKEN.fullmatch(segredo.api_token)


def test_abrir_cria_o_diretorio_de_dados(tmp_path: Path):
    segredo = segredos.abrir(tmp_path / "data" / "novo")
    assert (tmp_path / "data" / "novo" / segredos.ARQUIVO_TOKEN).is_file()
    assert segredo.api_token


def test_abrir_de_novo_mantem_os_mesmos_segredos(dir_data: Path):
    primeiro = segredos.abrir(dir_data)
    assert segredos.abrir(dir_data) == primeiro


def test_arquivo_do_codigo_guarda_so_o_codigo(dir_data: Path):
    segredo = segredos.abrir(dir_data)
    conteudo = (dir_data / segredos.ARQUIVO_CODIGO).read_text(encoding="utf-8")
    assert conteudo.strip() == segredo.codigo_de_posse
    assert segredo.api_token not in conteudo


@pytest.mark.parametrize("sujeira", ["", "\n", "   \n"])
def test_token_de_exemplo_impede_o_boot(dir_data: Path, sujeira):
    # Why: the example value ships in the repository, so a hub booting with it hands the
    # DP-bus to anyone who has read the source.
    # Por que: o valor de exemplo viaja no repositório, então um hub que suba com ele entrega
    # o DP-bus a qualquer um que tenha lido o fonte.
    (dir_data / segredos.ARQUIVO_TOKEN).write_text(
        segredos.TOKEN_EXEMPLO + sujeira, encoding="utf-8"
    )
    with pytest.raises(ValueError, match=segredos.ARQUIVO_TOKEN):
        segredos.abrir(dir_data)


@pytest.mark.parametrize("nome", [segredos.ARQUIVO_CODIGO, segredos.ARQUIVO_TOKEN])
@pytest.mark.parametrize("conteudo", ["", "\n", "  \t \n"])
def test_arquivo_vazio_impede_o_boot(dir_data: Path, nome, conteudo):
    # Why: an empty secret file must never turn into an empty secret that compares equal.
    # Por que: um arquivo de segredo vazio nunca pode virar um segredo vazio que compara igual.
    (dir_data / nome).write_text(conteudo, encoding="utf-8")
    with pytest.raises(ValueError, match=nome):
        segredos.abrir(dir_data)


def test_rotacionar_troca_o_token_e_mantem_o_modo(dir_data: Path):
    antigo = segredos.abrir(dir_data)
    novo = segredos.rotacionar_api_token(dir_data)
    assert novo != antigo.api_token
    assert arquivos.modo_de(dir_data / segredos.ARQUIVO_TOKEN) == 0o600
    depois = segredos.abrir(dir_data)
    assert depois.api_token == novo
    assert depois.codigo_de_posse == antigo.codigo_de_posse


@pytest.mark.parametrize(
    "informado",
    [
        CODIGO_REAL,
        CODIGO_REAL.lower(),
        "abcd efgh jklm npqr",
        "abcdefghjklmnpqr",
        " ABCD-EFGH-JKLM-NPQR ",
        "ABCD--EFGH-JKLM-NPQR",
    ],
)
def test_codigo_ditado_de_qualquer_jeito_confere(informado):
    assert segredos.conferir_codigo(informado, CODIGO_REAL)


@pytest.mark.parametrize(
    "informado",
    [
        "",
        "   ",
        "----",
        "ABCD-EFGH-JKLM-NPQS",
        "ABCD-EFGH-JKLM",
        "ABCD-EFGH-JKLM-NPQR-XYZW",
        "ABCD-EFGH-JKLM-NPQRX",
        "XABCD-EFGH-JKLM-NPQR",
    ],
)
def test_codigo_errado_e_recusado(informado):
    assert not segredos.conferir_codigo(informado, CODIGO_REAL)


@pytest.mark.parametrize("informado", ["código-de-posse", "ABCD-EFGH-JKLM-NPQÃ", " ABCD"])
def test_codigo_com_nao_ascii_e_recusado_sem_estourar(informado):
    # Why: compare_digest raises on non-ASCII text, and this value arrives from the network.
    # Por que: o compare_digest estoura com texto não-ASCII, e este valor chega pela rede.
    assert not segredos.conferir_codigo(informado, CODIGO_REAL)


@pytest.mark.parametrize("real", ["", "   ", "---"])
@pytest.mark.parametrize("informado", ["", "   ", "qualquer"])
def test_codigo_real_vazio_nunca_confere(informado, real):
    # Why: without this a data directory wiped by hand would accept an empty code as owner.
    # Por que: sem isto um diretório de dados apagado na mão aceitaria código vazio como dono.
    assert not segredos.conferir_codigo(informado, real)


def test_segredos_e_imutavel():
    segredo = segredos.Segredos(codigo_de_posse=CODIGO_REAL, api_token="t")
    with pytest.raises(FrozenInstanceError):
        segredo.api_token = "outro"  # type: ignore[misc]


@pytest.mark.parametrize("real", [CODIGO_REAL, "CÓDIGO", "", "   "])
@pytest.mark.parametrize(
    "informado", ["\ud800", "ABCD-\ud800", "\udfff\ud800", "ABCD\N{PILE OF POO}"]
)
def test_codigo_com_surrogate_solto_e_recusado_sem_estourar(informado, real):
    # Why: json.loads accepts "\ud800" as a string, and a strict utf-8 encode raises on it, so
    # POST /api/posse would answer 500 to an unauthenticated caller while the hub has no owner.
    # Por que: o json.loads aceita "\ud800" como string, e um encode utf-8 estrito estoura nele,
    # então o POST /api/posse responderia 500 a quem não autenticou, com o hub ainda sem dono.
    assert not segredos.conferir_codigo(informado, real)


def test_codigo_real_com_surrogate_nao_estoura_nem_confere():
    assert not segredos.conferir_codigo(CODIGO_REAL, "\ud800")
    assert segredos.conferir_codigo("\ud800", "\ud800")


def test_abrir_fecha_o_diretorio_de_dados(tmp_path: Path, umask_aberto):
    # Why: the directory holds every secret of section 9; born with umask 022 it would let any
    # local user list the names of those files.
    # Por que: o diretório guarda todo segredo da seção 9; nascido com umask 022 ele deixaria
    # qualquer usuário local listar os nomes desses arquivos.
    caminho = tmp_path / "data"
    segredos.abrir(caminho)
    assert arquivos.modo_de(caminho) == 0o700


def test_abrir_fecha_um_diretorio_de_dados_que_ja_estava_aberto(dir_data: Path):
    dir_data.chmod(0o755)
    segredos.abrir(dir_data)
    assert arquivos.modo_de(dir_data) == 0o700


def test_rotacionar_o_codigo_troca_o_valor_e_mantem_o_modo(dir_data: Path):
    antigo = segredos.abrir(dir_data)
    novo = segredos.rotacionar_codigo_de_posse(dir_data)
    assert novo != antigo.codigo_de_posse
    assert FORMATO_CODIGO.fullmatch(novo)
    assert arquivos.modo_de(dir_data / segredos.ARQUIVO_CODIGO) == 0o600
    depois = segredos.abrir(dir_data)
    assert depois.codigo_de_posse == novo
    assert depois.api_token == antigo.api_token


def test_o_codigo_antigo_nao_confere_depois_da_rotacao(dir_data: Path):
    # Why: the code is printed in the container log on the first boot; without spending it on
    # ownership, an old log line takes a hub whose config.json was wiped by hand a second time.
    # Por que: o código é impresso no log do container no primeiro boot; sem gastá-lo na posse,
    # uma linha antiga de log toma pela segunda vez um hub cujo config.json foi apagado na mão.
    antigo = segredos.abrir(dir_data).codigo_de_posse
    novo = segredos.rotacionar_codigo_de_posse(dir_data)
    real = segredos.abrir(dir_data).codigo_de_posse
    assert not segredos.conferir_codigo(antigo, real)
    assert segredos.conferir_codigo(novo, real)
