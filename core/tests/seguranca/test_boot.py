# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: the first boot shows the ownership code, and a hub with an owner never does.

Seção 9: o primeiro boot mostra o código de posse, e um hub com dono nunca mostra.
"""

import logging

import pytest
from aiohttp import web

from iphub.__main__ import main, preparar
from iphub.arquivos import escrever_texto
from iphub.auth import gerar_hash
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Config, salvar
from iphub.segredos import ARQUIVO_CODIGO, ARQUIVO_TOKEN, TOKEN_EXEMPLO

SENHA_DO_DONO = "senha-de-teste"


@pytest.fixture
def sem_servir(monkeypatch):
    """Keeps main() from taking the port; the boot is what these tests look at.

    Impede que o main() tome a porta; o boot é o que estes testes olham.
    """
    chamadas: list[dict] = []

    def registrar(app, **argumentos):
        chamadas.append(argumentos)

    monkeypatch.setattr(web, "run_app", registrar)
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


def test_primeiro_boot_gera_o_codigo_e_mostra_onde_ele_esta(amb, caplog):
    caplog.set_level(logging.INFO, logger="iphub")
    preparar(amb)
    codigo = (amb.dir_data / ARQUIVO_CODIGO).read_text(encoding="utf-8").strip()
    assert codigo
    assert codigo in caplog.text
    assert ARQUIVO_CODIGO in caplog.text


def test_hub_com_dono_nunca_mostra_o_codigo_no_log(amb, caplog):
    preparar(amb)
    codigo = (amb.dir_data / ARQUIVO_CODIGO).read_text(encoding="utf-8").strip()
    _com_dono(amb)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="iphub")
    preparar(amb)
    # Why: the code is spent once the password exists, and a log is a file nobody protects.
    # Por que: o código está gasto quando a senha existe, e um log é um arquivo que ninguém
    # protege.
    assert codigo not in caplog.text


def test_token_de_exemplo_recusa_o_boot(no_ambiente, sem_servir, caplog):
    caplog.set_level(logging.ERROR, logger="iphub")
    no_ambiente.dir_data.mkdir(parents=True, exist_ok=True)
    escrever_texto(no_ambiente.dir_data / ARQUIVO_TOKEN, TOKEN_EXEMPLO + "\n")
    # Why: the example value travels in the repository, so a hub booting with it would hand
    # the DP-bus to whoever read the source.
    # Por que: o valor de exemplo viaja no repositório, então um hub que subisse com ele
    # entregaria o DP-bus a quem tivesse lido o fonte.
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
