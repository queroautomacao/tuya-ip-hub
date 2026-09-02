# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: the panel session, its idle validity, its absolute cap and its revocations.

Seção 9: a sessão do painel, a validade ociosa, o teto absoluto e as revogações.
"""

import asyncio

import pytest

from iphub.limite import Limite
from iphub.sessoes import TETO_S, VALIDADE_S, Sessoes

OUTRA_SENHA = "outra-senha-boa"
INVALIDA = {"ok": False, "code": "sessao_invalida"}
COM_SESSAO = (("POST", "/api/sair"), ("GET", "/api/sessao"), ("POST", "/api/senha"))


async def _falar_no_socket(cliente, pedido: bytes) -> bytes:
    """Speaks HTTP over a raw socket, the only way to put bytes outside UTF-8 on the wire.

    Fala HTTP num socket cru, o único jeito de pôr bytes fora do UTF-8 no fio.
    """
    leitor, escritor = await asyncio.open_connection(cliente.host, cliente.port)
    escritor.write(pedido)
    await escritor.drain()
    resposta = await leitor.read()
    escritor.close()
    await escritor.wait_closed()
    return resposta


@pytest.fixture
async def cliente_relogio(fabrica_cliente, amb, relogio):
    """A hub whose clock the test moves by hand, sessions and rate limit included.

    Um hub cujo relógio o teste move na mão, sessões e limite incluídos.
    """
    return await fabrica_cliente(
        sessoes=Sessoes(amb.dir_data, agora=relogio), limite=Limite(agora=relogio)
    )


async def test_uso_regular_mantem_a_sessao_viva_alem_da_validade(
    cliente_relogio, posse, bearer, relogio
):
    token = await posse(cliente_relogio)
    for _ in range(7):
        relogio.avancar(VALIDADE_S - 60)
        resposta = await cliente_relogio.get("/api/sessao", headers=bearer(token))
        assert resposta.status == 200


async def test_sessao_ociosa_morre_depois_da_validade(cliente_relogio, posse, bearer, relogio):
    token = await posse(cliente_relogio)
    relogio.avancar(VALIDADE_S + 1)
    resposta = await cliente_relogio.get("/api/sessao", headers=bearer(token))
    assert resposta.status == 401
    assert await resposta.json() == INVALIDA


async def test_o_teto_mata_a_sessao_mesmo_em_uso_constante(cliente_relogio, posse, bearer, relogio):
    token = await posse(cliente_relogio)
    fim = relogio.agora + TETO_S
    while relogio.agora + VALIDADE_S - 60 < fim:
        relogio.avancar(VALIDADE_S - 60)
        assert (await cliente_relogio.get("/api/sessao", headers=bearer(token))).status == 200
    # Why: a token stolen from a browser that never closes would otherwise live forever.
    # Por que: um token roubado de um navegador que nunca fecha viveria para sempre.
    relogio.avancar(fim - relogio.agora + 1)
    resposta = await cliente_relogio.get("/api/sessao", headers=bearer(token))
    assert resposta.status == 401
    assert await resposta.json() == INVALIDA


async def test_sair_revoga_so_a_sessao_de_quem_chamou(cliente, posse, senha, bearer):
    primeiro = await posse(cliente)
    segundo = (await (await cliente.post("/api/entrar", json={"senha": senha})).json())["token"]
    assert (await cliente.post("/api/sair", headers=bearer(primeiro))).status == 200
    assert (await cliente.get("/api/sessao", headers=bearer(primeiro))).status == 401
    assert (await cliente.get("/api/sessao", headers=bearer(segundo))).status == 200


async def test_sair_duas_vezes_com_o_mesmo_token_nao_volta_a_valer(cliente, posse, bearer):
    token = await posse(cliente)
    assert (await cliente.post("/api/sair", headers=bearer(token))).status == 200
    resposta = await cliente.post("/api/sair", headers=bearer(token))
    assert resposta.status == 401
    assert await resposta.json() == INVALIDA


async def test_trocar_a_senha_revoga_todas_as_sessoes(cliente, posse, senha, bearer):
    primeiro = await posse(cliente)
    segundo = (await (await cliente.post("/api/entrar", json={"senha": senha})).json())["token"]
    resposta = await cliente.post(
        "/api/senha",
        headers=bearer(segundo),
        json={"senha_atual": senha, "senha_nova": OUTRA_SENHA},
    )
    assert resposta.status == 200
    novo = (await resposta.json())["token"]
    # Why: the password is changed exactly when a session may be in the wrong hands, so no
    # session that existed before the change survives it.
    # Por que: a senha é trocada justamente quando uma sessão pode estar em mãos erradas,
    # então nenhuma sessão anterior à troca sobrevive a ela.
    for antigo in (primeiro, segundo):
        assert (await cliente.get("/api/sessao", headers=bearer(antigo))).status == 401
    assert (await cliente.get("/api/sessao", headers=bearer(novo))).status == 200


async def test_token_de_outro_hub_nao_serve(cliente, posse, bearer, tmp_path):
    await posse(cliente)
    outro_hub = tmp_path / "outro-hub"
    outro_hub.mkdir()
    token, _ = Sessoes(outro_hub).criar()
    resposta = await cliente.get("/api/sessao", headers=bearer(token))
    assert resposta.status == 401
    assert await resposta.json() == INVALIDA


async def test_sessao_criada_antes_da_posse_nao_existe(cliente, bearer):
    # Why: no route may hand a token before the password exists, so nothing can be presented.
    # Por que: nenhuma rota entrega token antes de a senha existir, então nada pode ser
    # apresentado.
    resposta = await cliente.get("/api/sessao", headers=bearer("qualquer-coisa"))
    assert resposta.status == 401
    assert await resposta.json() == INVALIDA


@pytest.mark.parametrize(("metodo", "caminho"), COM_SESSAO)
async def test_authorization_com_bytes_fora_do_utf8_e_sessao_invalida(cliente, metodo, caminho):
    pedido = (
        b"%s %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nContent-Length: 0\r\n"
        b"Authorization: Bearer \xff\xfe\r\n\r\n"
        % (metodo.encode("ascii"), caminho.encode("ascii"))
    )
    resposta = await _falar_no_socket(cliente, pedido)
    # Why: those bytes reach the handler as lone surrogates, and hashing one raised out of the
    # guard, so an unauthenticated request answered 500 where the rule says no session matches.
    # Por que: esses bytes chegam ao handler como surrogates soltos, e passar um por hash
    # estourava na guarda, então uma requisição sem autenticação respondia 500 onde a regra diz
    # que nenhuma sessão casa.
    assert resposta.startswith(b"HTTP/1.1 401 Unauthorized"), resposta[:200]
    assert b'"code": "sessao_invalida"' in resposta
    assert b"Traceback" not in resposta
