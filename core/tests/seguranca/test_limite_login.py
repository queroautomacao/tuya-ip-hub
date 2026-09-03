# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: five failures block the IP for fifteen minutes, and sixty tries a minute cap all.

Seção 9: cinco falhas bloqueiam o IP por quinze minutos, e sessenta tentativas por minuto
limitam todos.
"""

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
    """A hub whose clock the test moves and that takes the loopback as a declared proxy.

    Um hub cujo relógio o teste move e que toma o loopback como proxy declarado.
    """
    return await fabrica_cliente(
        config=Config(proxies_confiaveis=("127.0.0.1",)),
        sessoes=Sessoes(amb.dir_data, agora=relogio),
        limite=Limite(agora=relogio),
    )


def _de(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


def _de_duas_linhas(primeira: str, segunda: str) -> list[tuple[str, str]]:
    """The header sent as two lines, which is what a client does to hide the proxy hop.

    O cabeçalho enviado em duas linhas, que é o que um cliente faz para esconder o salto do proxy.
    """
    return [("X-Forwarded-For", primeira), ("X-Forwarded-For", segunda)]


async def _errar(cliente, vezes: int, cabecalhos: Cabecalhos | None = None) -> None:
    for _ in range(vezes):
        resposta = await cliente.post(
            "/api/entrar", json={"senha": ERRADA}, headers=cabecalhos or {}
        )
        assert resposta.status == 401, await resposta.text()


async def _errar_uma(cliente, cabecalhos: Cabecalhos | None = None):
    """One real check of a secret, which is what the global ceiling exists to count.

    Uma conferência real de segredo, que é o que o teto global existe para contar.
    """
    return await cliente.post("/api/entrar", json={"senha": ERRADA}, headers=cabecalhos or {})


async def test_cinco_senhas_erradas_bloqueiam_o_ip(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    await _errar(cliente_limitado, FALHAS_ATE_BLOQUEIO)
    # Why: the block has to hold even for the right password, or an attacker would use the
    # refusal to tell a good guess from a bad one.
    # Por que: o bloqueio vale mesmo para a senha certa, senão o atacante usaria a recusa
    # para separar um chute bom de um ruim.
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
    # Why: anyone can write this header, so honouring it from an undeclared peer would let a
    # single attacker spend the block of every address but its own.
    # Por que: qualquer um escreve este cabeçalho, então honrá-lo vindo de par não declarado
    # deixaria um único atacante gastar o bloqueio de todo endereço menos o dele.
    for numero in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente, 1, _de(f"192.0.2.{numero}"))
    resposta = await cliente.post("/api/entrar", json={"senha": senha}, headers=_de("192.0.2.200"))
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_a_posse_paga_na_janela_global_mesmo_sem_conferir_segredo(
    cliente_limitado, posse, senha
):
    """Section 9: the claim spends a PBKDF2 hashing the new password, so it costs a slot.

    Seção 9: a posse gasta um PBKDF2 derivando a senha nova, então ela custa uma vaga.
    """
    # Why: the claim checks no credential now, so there is nothing to block an address for;
    # what remains is the ceiling that exists because the derivation costs CPU on an ARM board.
    # Por que: a posse não confere credencial nenhuma agora, então não há o que bloquear por
    # endereço; o que resta é o teto que existe porque a derivação custa CPU numa placa ARM.
    await posse(cliente_limitado)
    for numero in range(TETO_GLOBAL - 1):
        assert (await _errar_uma(cliente_limitado, _de(f"192.0.2.{numero}"))).status == 401
    resposta = await _errar_uma(cliente_limitado, _de("192.0.2.200"))
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_teto_global_vale_mesmo_trocando_de_ip(cliente_limitado, posse, relogio):
    # Why: each attempt that reaches a secret costs one PBKDF2 of two hundred thousand
    # iterations on an ARM board, so an attacker rotating addresses could keep the daemon busy
    # without the global window; the attempts that fill it here are all real checks of a secret.
    # Por que: cada tentativa que chega a um segredo custa um PBKDF2 de duzentas mil iterações
    # numa placa ARM, então um atacante trocando de endereço prenderia o daemon sem a janela
    # global; as tentativas que a enchem aqui são todas conferências reais de segredo.
    # Why: the claim itself hashes the new password, so it takes the first slot of the window.
    # Por que: a própria posse deriva a senha nova, então ela toma a primeira vaga da janela.
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
    # Why: an attacker that keeps knocking after the block would starve the owner of the sixty
    # a minute, and the owner would be refused for attempts that never checked a secret.
    # Por que: um atacante que segue batendo depois do bloqueio esvaziaria as sessenta por
    # minuto do dono, que seria recusado por tentativas que nunca conferiram segredo nenhum.
    # Why: the claim itself hashes the new password, so it takes the first slot of the window.
    # Por que: a própria posse deriva a senha nova, então ela toma a primeira vaga da janela.
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
    # Why: the usual reverse proxy appends what it saw, so the entry on the left is text the
    # client wrote; keying the block by it means the attacker is never blocked, it just writes
    # another value on every try.
    # Por que: o proxy reverso comum anexa o que viu, então a entrada da esquerda é texto que o
    # cliente escreveu; chavear o bloqueio por ela é nunca bloquear o atacante, que só escreve
    # outro valor a cada tentativa.
    for numero in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, _de(f"203.0.113.{numero}, {ATACANTE}"))
    resposta = await cliente_limitado.post(
        "/api/entrar", json={"senha": senha}, headers=_de(f"203.0.113.99, {ATACANTE}")
    )
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_a_entrada_forjada_a_esquerda_nao_bloqueia_o_dono(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    # Why: the attacker writes the owner address on the left, so keying the block by it hands
    # the attacker a fifteen minute lockout of the owner, renewable forever.
    # Por que: o atacante escreve o endereço do dono à esquerda, então chavear o bloqueio por
    # ela entrega ao atacante um bloqueio de quinze minutos do dono, renovável para sempre.
    for _ in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, _de(f"{DONO}, {ATACANTE}"))
    resposta = await cliente_limitado.post("/api/entrar", json={"senha": senha}, headers=_de(DONO))
    assert resposta.status == 200, await resposta.text()


async def test_o_encaminhado_que_nao_e_ip_cai_no_par(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    # Why: a value that is never parsed becomes a dictionary key the attacker chooses, and a
    # fresh key is a fresh set of five failures to spend.
    # Por que: um valor que nunca é analisado vira chave de dicionário que o atacante escolhe, e
    # chave nova é um novo conjunto de cinco falhas para gastar.
    for numero in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, _de(f"nao-e-ip-{numero}"))
    resposta = await cliente_limitado.post(
        "/api/entrar", json={"senha": senha}, headers=_de("outro-lixo")
    )
    assert resposta.status == 429
    assert await resposta.json() == DEMAIS


async def test_a_segunda_linha_do_encaminhado_nao_e_ignorada(cliente_limitado, posse, senha):
    await posse(cliente_limitado)
    # Why: reading a single value drops the repeated line, so the client hides the hop the proxy
    # wrote behind a line of its own and picks whose block it spends.
    # Por que: ler um valor só descarta a linha repetida, então o cliente esconde atrás de uma
    # linha sua o salto que o proxy escreveu e escolhe qual bloqueio gasta.
    forjado = _de_duas_linhas(DONO, ATACANTE)
    for _ in range(FALHAS_ATE_BLOQUEIO):
        await _errar(cliente_limitado, 1, forjado)
    dono = await cliente_limitado.post("/api/entrar", json={"senha": senha}, headers=_de(DONO))
    assert dono.status == 200, await dono.text()
    atacante = await cliente_limitado.post("/api/entrar", json={"senha": senha}, headers=forjado)
    assert atacante.status == 429
