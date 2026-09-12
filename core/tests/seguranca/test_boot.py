# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The boot says the claim is open while there is no password, and nothing after."""

import logging

import pytest

from iphub import __main__ as modulo
from iphub.__main__ import main, preparar
from iphub.arquivos import escrever_texto
from iphub.auth import gerar_hash
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Config, salvar
from iphub.segredos import ARQUIVO_TOKEN, TOKEN_EXEMPLO

SENHA_DO_DONO = "senha-de-teste"


@pytest.fixture
def sem_servir(monkeypatch):
    """Keeps main() from taking the port; the boot is what these tests look at."""
    chamadas: list[dict] = []

    def registrar(app, amb):
        chamadas.append({"host": amb.bind, "port": amb.porta})

    monkeypatch.setattr(modulo, "servir_ate_o_fim", registrar)
    return chamadas


@pytest.fixture
def no_ambiente(monkeypatch, amb):
    monkeypatch.setenv("IPHUB_DATA", str(amb.dir_data))
    monkeypatch.setenv("IPHUB_PAINEL", str(amb.dir_painel))
    monkeypatch.setenv("IPHUB_BIND", amb.bind)
    monkeypatch.setenv("IPHUB_PORTA", str(amb.porta))
    return amb


def _com_dono(amb) -> None:
    salt, hash_senha, iteracoes = gerar_hash(SENHA_DO_DONO)
    salvar(Config(senha_salt=salt, senha_hash=hash_senha, senha_iteracoes=iteracoes), amb.dir_data)


def test_hub_sem_dono_avisa_que_a_posse_esta_aberta_e_onde(amb, caplog):
    """The claim is public, so the boot says the panel is up for grabs and where."""
    caplog.set_level(logging.INFO, logger="iphub")
    preparar(amb)
    assert "not configured yet" in caplog.text
    assert f"{amb.bind}:{amb.porta}" in caplog.text


def test_hub_sem_dono_nao_deixa_arquivo_de_codigo_no_disco(amb):
    # The ownership code left; a file still appearing would mean dead code is
    # writing a secret nobody reads.
    preparar(amb)
    assert not (amb.dir_data / "codigo-de-posse.txt").exists()
    # The vault leaves its salt and, on a machine with no serial to read, its key file.
    assert sorted(p.name for p in amb.dir_data.iterdir()) == [
        ARQUIVO_TOKEN,
        "cofre.chave",
        "cofre.sal",
    ]


def test_hub_com_dono_nao_avisa_nada_sobre_posse(amb, caplog):
    preparar(amb)
    _com_dono(amb)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="iphub")
    preparar(amb)
    # The invitation only makes sense while the hub has no password; repeating it to an
    # owned hub would tell a reader of the log that it is still open, which is false.
    assert "not configured yet" not in caplog.text


def test_token_de_exemplo_recusa_o_boot(no_ambiente, sem_servir, caplog):
    caplog.set_level(logging.ERROR, logger="iphub")
    no_ambiente.dir_data.mkdir(parents=True, exist_ok=True)
    escrever_texto(no_ambiente.dir_data / ARQUIVO_TOKEN, TOKEN_EXEMPLO + "\n")
    # The example value travels in the repository, so a hub booting with it would hand
    # the DP-bus to whoever read the source.
    assert main() == 1
    assert sem_servir == []
    assert ARQUIVO_TOKEN in caplog.text


def test_config_de_outro_schema_recusa_o_boot(no_ambiente, sem_servir, caplog):
    caplog.set_level(logging.ERROR, logger="iphub")
    no_ambiente.dir_data.mkdir(parents=True, exist_ok=True)
    (no_ambiente.dir_data / ARQUIVO_CONFIG).write_text('{"schema_version": 99}', encoding="utf-8")
    assert main() == 1
    assert sem_servir == []
    assert "schema_version" in caplog.text


def test_boot_bom_sobe_o_daemon(no_ambiente, sem_servir, caplog):
    caplog.set_level(logging.INFO, logger="iphub")
    assert main() == 0
    assert len(sem_servir) == 1
    assert sem_servir[0]["port"] == no_ambiente.porta
    assert sem_servir[0]["host"] == no_ambiente.bind
