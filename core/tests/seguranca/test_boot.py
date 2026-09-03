# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: the boot says the claim is open while there is no password, and nothing after.

Seção 9: o boot diz que a posse está aberta enquanto não há senha, e nada depois disso.
"""

import logging

import pytest
from aiohttp import web

from iphub.__main__ import main, preparar
from iphub.arquivos import escrever_texto
from iphub.auth import gerar_hash
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Config, salvar
from iphub.segredos import ARQUIVO_TOKEN, TOKEN_EXEMPLO

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


def test_hub_sem_dono_avisa_que_a_posse_esta_aberta_e_onde(amb, caplog):
    """Section 9: the claim is public, so the boot says the panel is up for grabs and where.

    Seção 9: a posse é pública, então o boot diz que o painel está aberto e onde.
    """
    caplog.set_level(logging.INFO, logger="iphub")
    preparar(amb)
    assert "not configured yet" in caplog.text
    assert f"{amb.bind}:{amb.porta}" in caplog.text


def test_hub_sem_dono_nao_deixa_arquivo_de_codigo_no_disco(amb):
    # Why: the ownership code left section 9; a file still appearing would mean dead code is
    # writing a secret nobody reads.
    # Por que: o código de posse saiu da seção 9; um arquivo ainda aparecendo significaria
    # código morto escrevendo um segredo que ninguém lê.
    preparar(amb)
    assert not (amb.dir_data / "codigo-de-posse.txt").exists()
    assert sorted(p.name for p in amb.dir_data.iterdir()) == [ARQUIVO_TOKEN]


def test_hub_com_dono_nao_avisa_nada_sobre_posse(amb, caplog):
    preparar(amb)
    _com_dono(amb)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="iphub")
    preparar(amb)
    # Why: the invitation only makes sense while the hub has no password; repeating it to an
    # owned hub would tell a reader of the log that it is still open, which is false.
    # Por que: o convite só faz sentido enquanto o hub não tem senha; repeti-lo num hub com
    # dono diria a quem lê o log que ele ainda está aberto, o que é falso.
    assert "not configured yet" not in caplog.text


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
