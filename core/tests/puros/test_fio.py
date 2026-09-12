# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""And 9 under attack: the transcript says everything and never a secret."""

import logging

from iphub.drivers import fio


def test_um_segredo_nunca_cai_na_transcricao_seja_qual_for_a_forma():
    assert fio.redigir({"client-key": "abc", "id": 1}) == '{"client-key":"***","id":1}'
    assert fio.redigir('{"payload":{"token":"t0k","x":2}}') == '{"payload":{"token":"***","x":2}}'
    assert fio.redigir("Authorization: Bearer abcdef") == "Authorization: ***"
    assert fio.redigir(b"psk=segredo&cmd=on") == "psk=***&cmd=on"
    assert fio.redigir("PWRON") == "PWRON"


def test_a_linha_e_uma_linha_e_cabe_no_teto():
    aparada = fio.aparar("a\r\n b\x00c" + "x" * 1000)
    assert "\n" not in aparada and "\x00" not in aparada
    assert aparada.startswith("a b")
    assert aparada.endswith("chars)")
    assert len(aparada) < 450


def test_o_poll_de_rotina_e_escrito_uma_vez_e_de_novo_depois_de_uma_falha(caplog):
    log = logging.getLogger("iphub.drivers.nativos.teste")
    caplog.set_level(logging.DEBUG, logger="iphub.drivers.nativos.teste")
    transcricao = fio.Fio(log, "uuid-1")
    for _ in range(3):
        transcricao.enviado("GET /estado", rotina=True)
        transcricao.recebido("200 {}", rotina=True)
    assert [r.getMessage() for r in caplog.records] == ["uuid-1 -> GET /estado", "uuid-1 <- 200 {}"]
    caplog.clear()
    transcricao.enviado("PWRON")
    transcricao.recebido("PWRON")
    transcricao.falhou("GET /estado", TimeoutError())
    transcricao.enviado("GET /estado", rotina=True)
    transcricao.recebido("200 {}", rotina=True)
    mensagens = [r.getMessage() for r in caplog.records]
    assert mensagens == [
        "uuid-1 -> PWRON",
        "uuid-1 <- PWRON",
        "uuid-1: GET /estado failed: TimeoutError",
        "uuid-1 -> GET /estado",
        "uuid-1 <- 200 {}",
    ]


def test_uma_recusa_do_aparelho_e_resposta_e_nao_aviso(caplog):
    log = logging.getLogger("iphub.drivers.nativos.teste")
    caplog.set_level(logging.DEBUG, logger="iphub.drivers.nativos.teste")
    fio.Fio(log, "uuid-1").recusado("request x/y", "nao_suportado")
    (registro,) = caplog.records
    assert registro.levelno == logging.DEBUG
    assert registro.getMessage() == "uuid-1 <- refused request x/y: nao_suportado"
