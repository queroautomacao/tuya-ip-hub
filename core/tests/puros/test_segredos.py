# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The api_token, generated on the first boot and kept 0600."""

import os
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from iphub import arquivos, segredos

FORMATO_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,}")


@pytest.fixture
def dir_data(tmp_path: Path) -> Path:
    caminho = tmp_path / "data"
    caminho.mkdir()
    return caminho


@pytest.fixture
def umask_aberto():
    """The widest umask a container can inherit; nothing may reach the group or the world."""
    anterior = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(anterior)


def test_api_token_e_aleatorio_e_urlsafe():
    tokens = {segredos.gerar_api_token() for _ in range(20)}
    assert len(tokens) == 20
    assert all(FORMATO_TOKEN.fullmatch(t) for t in tokens)


def test_abrir_gera_os_dois_arquivos_0600(dir_data: Path):
    segredo = segredos.abrir(dir_data)
    assert arquivos.modo_de(dir_data / segredos.ARQUIVO_TOKEN) == 0o600
    assert FORMATO_TOKEN.fullmatch(segredo.api_token)
    # The ownership code was removed; nothing may keep writing its file.
    assert not (dir_data / "codigo-de-posse.txt").exists()


def test_abrir_cria_o_diretorio_de_dados(tmp_path: Path):
    segredo = segredos.abrir(tmp_path / "data" / "novo")
    assert (tmp_path / "data" / "novo" / segredos.ARQUIVO_TOKEN).is_file()
    assert segredo.api_token


def test_abrir_de_novo_mantem_os_mesmos_segredos(dir_data: Path):
    primeiro = segredos.abrir(dir_data)
    assert segredos.abrir(dir_data) == primeiro


@pytest.mark.parametrize("sujeira", ["", "\n", "   \n"])
def test_token_de_exemplo_impede_o_boot(dir_data: Path, sujeira):
    # The example value ships in the repository, so a hub booting with it hands the
    # DP-bus to anyone who has read the source.
    (dir_data / segredos.ARQUIVO_TOKEN).write_text(
        segredos.TOKEN_EXEMPLO + sujeira, encoding="utf-8"
    )
    with pytest.raises(ValueError, match=segredos.ARQUIVO_TOKEN):
        segredos.abrir(dir_data)


@pytest.mark.parametrize("conteudo", ["", "\n", "  \t \n"])
def test_arquivo_vazio_impede_o_boot(dir_data: Path, conteudo):
    # An empty secret file must never turn into an empty secret that compares equal.
    (dir_data / segredos.ARQUIVO_TOKEN).write_text(conteudo, encoding="utf-8")
    with pytest.raises(ValueError, match=segredos.ARQUIVO_TOKEN):
        segredos.abrir(dir_data)


def test_rotacionar_troca_o_token_e_mantem_o_modo(dir_data: Path):
    antigo = segredos.abrir(dir_data)
    novo = segredos.rotacionar_api_token(dir_data)
    assert novo != antigo.api_token
    assert arquivos.modo_de(dir_data / segredos.ARQUIVO_TOKEN) == 0o600
    depois = segredos.abrir(dir_data)
    assert depois.api_token == novo


def test_segredos_e_imutavel():
    segredo = segredos.Segredos(api_token="t")
    with pytest.raises(FrozenInstanceError):
        segredo.api_token = "outro"  # type: ignore[misc]


def test_abrir_fecha_o_diretorio_de_dados(tmp_path: Path, umask_aberto):
    # The directory holds every secret; born with umask 022 it would let any
    # local user list the names of those files.
    caminho = tmp_path / "data"
    segredos.abrir(caminho)
    assert arquivos.modo_de(caminho) == 0o700


def test_abrir_fecha_um_diretorio_de_dados_que_ja_estava_aberto(dir_data: Path):
    dir_data.chmod(0o755)
    segredos.abrir(dir_data)
    assert arquivos.modo_de(dir_data) == 0o700
