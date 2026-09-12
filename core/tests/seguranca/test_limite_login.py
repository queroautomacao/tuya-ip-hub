# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Five failures block the IP for fifteen minutes, and sixty tries a minute cap all."""

import pytest

from iphub.config import Config
from iphub.limite import BLOQUEIO_S, FALHAS_ATE_BLOQUEIO, JANELA_GLOBAL_S, TETO_GLOBAL, Limite
from iphub.sessoes import Sessoes

ERRADA = "nao-e-a-senha"
DEMAIS = {"ok": False, "code": "muitas_tentativas"}
ATACANTE = "198.51.100.9"
DONO = "192.0.2.50"

Cabecalhos = dict[str, str] | list[tuple[str, str]]


@pytest.fixture
async def cliente_limitado(fabrica_cliente, amb, relogio):
    """A hub whose clock the test moves and that takes the loopback as a declared proxy."""
    return await fabrica_cliente(
        config=Config(proxies_confiaveis=("127.0.0.1",)),
        sessoes=Sessoes(amb.dir_data, agora=relogio),
        limite=Limite(agora=relogio),
    )


def _de(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


def _de_duas_linhas(primeira: str, segunda: str) -> list[tuple[str, str]]:
    """The header sent as two lines, which is what a client does to hide the proxy hop."""
    return [("X-Forwarded-For", primeira), ("X-Forwarded-For", segunda)]


async def _errar(cliente, vezes: int, cabecalhos: Cabecalhos | None = None) -> None:
    for _ in range(vezes):
        resposta = await cliente.post(
            "/api/entrar", json={"senha": ERRADA}, headers=cabecalhos or {}
        )
        assert resposta.status == 401, await resposta.text()


async def _errar_uma(cliente, cabecalhos: Cabecalhos | None = None):
    """One real check of a secret, which is what the global ceiling exists to count."""
    return await cliente.post("/api/entrar", json={"senha": ERRADA}, headers=cabecalhos or {})


async def test_cinco_senhas_erradas_bloqueiam_o_ip(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    await _errar(cliente_limitado, FALHAS_ATE_BLOQUEIO)
    # The block has to hold even for the right password, or an attacker would use the
    # refusal to tell a good guess from a bad one.
    resposta = await cliente_limitado.post("/api/entrar", json={"senha": senha})
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_quatro_falhas_ainda_deixam_entrar(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    await _errar(cliente_limitado, FALHAS_ATE_BLOQUEIO - 1)
    assert (await cliente_limitado.post("/api/entrar", json={"senha": senha})).status == 200


async def test_o_bloqueio_cai_depois_da_janela(cliente_limitado, posse, senha, relogio):
    await posse(cliente_limitado)
    await _errar(cliente_limitado, FALHAS_ATE_BLOQUEIO)
    assert (await cliente_limitado.post("/api/entrar", json={"senha": senha})).status == 429
    relogio.avancar(BLOQUEIO_S + 1)
    assert (await cliente_limitado.post("/api/entrar", json={"senha": senha})).status == 200


async def test_bloquear_um_ip_nao_bloqueia_outro(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    await _errar(cliente_limitado, FALHAS_ATE_BLOQUEIO, _de("192.0.2.10"))
    bloqueado = await cliente_limitado.post(
        "/api/entrar", json={"senha": senha}, headers=_de("192.0.2.10")
    )
    assert bloqueado.status == 429
    outro = await cliente_limitado.post(
        "/api/entrar", json={"senha": senha}, headers=_de("192.0.2.11")
    )
    assert outro.status == 200


async def test_x_forwarded_for_de_par_nao_declarado_e_ignorado(cliente, posse, senha):
    await posse(cliente)
    # Anyone can write this header, so honouring it from an undeclared peer would let a
    # single attacker spend the block of every address but its own.
    for numero in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente, 1, _de(f"192.0.2.{numero}"))
    resposta = await cliente.post("/api/entrar", json={"senha": senha}, headers=_de("192.0.2.200"))
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_a_posse_paga_na_janela_global_mesmo_sem_conferir_segredo(
    cliente_limitado, posse, senha
):
    """The claim spends a PBKDF2 hashing the new password, so it costs a slot."""
    # The claim checks no credential now, so there is nothing to block an address for;
    # what remains is the ceiling that exists because the derivation costs CPU on an ARM board.
    await posse(cliente_limitado)
    for numero in range(TETO_GLOBAL - 1):
        assert (await _errar_uma(cliente_limitado, _de(f"192.0.2.{numero}"))).status == 401
    resposta = await _errar_uma(cliente_limitado, _de("192.0.2.200"))
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_teto_global_vale_mesmo_trocando_de_ip(cliente_limitado, posse, relogio):
    # Each attempt that reaches a secret costs one PBKDF2 of two hundred thousand
    # iterations on an ARM board, so an attacker rotating addresses could keep the daemon busy
    # without the global window; the attempts that fill it here are all real checks of a secret.
    # The claim itself hashes the new password, so it takes the first slot of the window.
    await posse(cliente_limitado)
    for numero in range(TETO_GLOBAL - 1):
        resposta = await _errar_uma(cliente_limitado, _de(f"192.0.2.{numero}"))
        assert resposta.status == 401, numero
    resposta = await _errar_uma(cliente_limitado, _de("192.0.2.200"))
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS
    relogio.avancar(JANELA_GLOBAL_S)
    seguinte = await _errar_uma(cliente_limitado, _de("192.0.2.200"))
    assert seguinte.status == 401


async def test_o_ip_bloqueado_nao_gasta_a_janela_global(cliente_limitado, posse):
    # An attacker that keeps knocking after the block would starve the owner of the sixty
    # a minute, and the owner would be refused for attempts that never checked a secret.
    # The claim itself hashes the new password, so it takes the first slot of the window.
    await posse(cliente_limitado)
    for _ in range(FALHAS_ATE_BLOQUEIO):
        assert (await _errar_uma(cliente_limitado, _de(ATACANTE))).status == 401
    for _ in range(10):
        assert (await _errar_uma(cliente_limitado, _de(ATACANTE))).status == 429
    for numero in range(TETO_GLOBAL - FALHAS_ATE_BLOQUEIO - 1):
        resposta = await _errar_uma(cliente_limitado, _de(f"192.0.2.{numero}"))
        assert resposta.status == 401, numero


async def test_cinco_falhas_atras_do_proxy_bloqueiam_o_cliente_real(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    # The usual reverse proxy appends what it saw, so the entry on the left is text the
    # client wrote; keying the block by it means the attacker is never blocked, it just writes
    # another value on every try.
    for numero in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, _de(f"203.0.113.{numero}, {ATACANTE}"))
    resposta = await cliente_limitado.post(
        "/api/entrar", json={"senha": senha}, headers=_de(f"203.0.113.99, {ATACANTE}")
    )
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_a_entrada_forjada_a_esquerda_nao_bloqueia_o_dono(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    # The attacker writes the owner address on the left, so keying the block by it hands
    # the attacker a fifteen minute lockout of the owner, renewable forever.
    for _ in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, _de(f"{DONO}, {ATACANTE}"))
    resposta = await cliente_limitado.post("/api/entrar", json={"senha": senha}, headers=_de(DONO))
    assert resposta.status == 200, await resposta.text()


async def test_o_encaminhado_que_nao_e_ip_cai_no_par(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    # A value that is never parsed becomes a dictionary key the attacker chooses, and a
    # fresh key is a fresh set of five failures to spend.
    for numero in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, _de(f"nao-e-ip-{numero}"))
    resposta = await cliente_limitado.post(
        "/api/entrar", json={"senha": senha}, headers=_de("outro-lixo")
    )
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_a_segunda_linha_do_encaminhado_nao_e_ignorada(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    # Reading a single value drops the repeated line, so the client hides the hop the proxy
    # wrote behind a line of its own and picks whose block it spends.
    forjado = _de_duas_linhas(DONO, ATACANTE)
    for _ in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, forjado)
    dono = await cliente_limitado.post("/api/entrar", json={"senha": senha}, headers=_de(DONO))
    assert dono.status == 200, await dono.text()
    atacante = await cliente_limitado.post("/api/entrar", json={"senha": senha}, headers=forjado)
    assert atacante.status == 429
