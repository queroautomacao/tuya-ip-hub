# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""First use and session routes: state, ownership, login, logout and password change.

Rotas de primeiro uso e de sessão: estado, posse, entrada, saída e troca de senha.
"""

import asyncio
from dataclasses import replace

from aiohttp import web

from iphub.api.comum import (
    AMBIENTE,
    LIMITE,
    SEGREDOS,
    SESSOES,
    TRAVA_POSSE,
    campo,
    com_sessao,
    config_de,
    ler_corpo,
    resposta_ok,
    segredos_de,
    token_da_sessao,
    trocar_config,
)
from iphub.auth import SenhaCurta, gerar_hash
from iphub.auth import conferir as conferir_senha
from iphub.dpbus.socket import BARRAMENTO
from iphub.portao import ip_do_pedido, resposta_erro
from iphub.segredos import rotacionar_api_token
from iphub.versao import SCHEMA_VERSION, VERSAO


def _ip(request: web.Request) -> str:
    return ip_do_pedido(request, frozenset(config_de(request.app).proxies_confiaveis))


def _sessao_nova(request: web.Request) -> web.Response:
    token, expira_em_s = request.app[SESSOES].criar()
    return resposta_ok(token=token, expira_em_s=expira_em_s)


async def _guardar_senha(app: web.Application, nova: str) -> None:
    """Raises SenhaCurta when the password is under the minimum of section 9.

    Levanta SenhaCurta quando a senha está abaixo do mínimo da seção 9.
    """
    # Why: two hundred thousand iterations take tenths of a second on the reference ARM board,
    # and on the event loop that is every other request of the panel waiting in line.
    # Por que: duzentas mil iterações levam décimos de segundo na placa ARM de referência, e no
    # laço de eventos isso é toda outra requisição do painel esperando na fila.
    salt, hash_senha, iteracoes = await asyncio.to_thread(gerar_hash, nova)
    trocar_config(
        app,
        replace(config_de(app), senha_salt=salt, senha_hash=hash_senha, senha_iteracoes=iteracoes),
    )


async def _senha_confere(app: web.Application, informada: str) -> bool:
    cfg = config_de(app)
    return await asyncio.to_thread(
        conferir_senha, informada, cfg.senha_salt, cfg.senha_hash, cfg.senha_iteracoes
    )


def _renovar_posse(app: web.Application) -> None:
    """Taking ownership ends the previous owner: every session and the machine credential.

    Tomar posse encerra o dono anterior: toda sessão e a credencial de máquina.
    """
    # Why: a data directory whose config.json was erased by hand is an unconfigured hub that
    # still holds live sessions and the old api_token of whoever owned it before.
    # Por que: um diretório de dados com o config.json apagado na mão é um hub sem dono que
    # ainda guarda sessões vivas e o api_token antigo de quem foi dono antes.
    app[SESSOES].revogar_todas()
    app[SEGREDOS].valor = replace(
        segredos_de(app), api_token=rotacionar_api_token(app[AMBIENTE].dir_data)
    )


async def _revogar_barramento(app: web.Application) -> None:
    """Section 9: rotating the machine credential closes the sockets it authenticated.

    Seção 9: rotacionar a credencial de máquina fecha os sockets que ela autenticou.
    """
    barramento = app.get(BARRAMENTO)
    if barramento is not None:
        await barramento.revogar()


async def estado(request: web.Request) -> web.Response:
    cfg = config_de(request.app)
    return resposta_ok(
        configurado=cfg.configurado,
        versao=VERSAO,
        schema_version=SCHEMA_VERSION,
        nome_instalacao=cfg.nome_instalacao,
    )


async def posse(request: web.Request) -> web.Response:
    app = request.app
    limite = app[LIMITE]
    dados = await ler_corpo(request)
    informada = campo(dados, "senha") if dados is not None else None
    if informada is None:
        return resposta_erro(400, "corpo_invalido")
    # Why: section 9, the claim is public now, so the check that a password does not exist yet
    # and the write of the first one are one step; two racers must not become two owners.
    # Por que: seção 9, a posse agora é pública, então a checagem de que ainda não há senha e a
    # escrita da primeira são um passo só; dois concorrentes não podem virar dois donos.
    async with app[TRAVA_POSSE]:
        if config_de(app).configurado:
            return resposta_erro(409, "ja_configurado")
        # Why: no credential is checked here, so there is nothing to block an address for; the
        # global ceiling stays because the route still spends a PBKDF2 on the new password.
        # Por que: nenhuma credencial é conferida aqui, então não há o que bloquear por
        # endereço; o teto global fica porque a rota ainda gasta um PBKDF2 na senha nova.
        if not limite.permitido(_ip(request)):
            return resposta_erro(429, "muitas_tentativas")
        limite.registrar_tentativa()
        try:
            await _guardar_senha(app, informada)
        except SenhaCurta:
            return resposta_erro(400, "senha_curta")
        _renovar_posse(app)
        await _revogar_barramento(app)
        return _sessao_nova(request)


async def entrar(request: web.Request) -> web.Response:
    app = request.app
    limite = app[LIMITE]
    dados = await ler_corpo(request)
    informada = campo(dados, "senha") if dados is not None else None
    if informada is None:
        return resposta_erro(400, "corpo_invalido")
    if not config_de(app).configurado:
        return resposta_erro(409, "nao_configurado")
    ip = _ip(request)
    if not limite.permitido(ip):
        return resposta_erro(429, "muitas_tentativas")
    # Why: the global window is justified by the cost of one PBKDF2, so only the check of a
    # real secret spends it; otherwise any malformed request locks the owner out of the hub.
    # Por que: a janela global se justifica pelo custo de um PBKDF2, então só a conferência de
    # um segredo real a gasta; senão qualquer requisição malformada tranca o dono fora do hub.
    limite.registrar_tentativa()
    if not await _senha_confere(app, informada):
        limite.registrar_falha(ip)
        return resposta_erro(401, "senha_invalida")
    limite.registrar_sucesso(ip)
    return _sessao_nova(request)


@com_sessao
async def sair(request: web.Request) -> web.Response:
    request.app[SESSOES].revogar(token_da_sessao(request))
    return resposta_ok()


@com_sessao
async def sessao(request: web.Request) -> web.Response:
    expira_em_s = request.app[SESSOES].expira_em_s(token_da_sessao(request))
    return resposta_ok(expira_em_s=expira_em_s)


@com_sessao
async def senha(request: web.Request) -> web.Response:
    app = request.app
    dados = await ler_corpo(request)
    atual = campo(dados, "senha_atual") if dados is not None else None
    nova = campo(dados, "senha_nova") if dados is not None else None
    if atual is None or nova is None:
        return resposta_erro(400, "corpo_invalido")
    # Why: this route spends two PBKDF2 per call, the same cost the global window exists to
    # bound; the caller already holds a session, so no block per IP is needed on top of it.
    # Por que: esta rota gasta dois PBKDF2 por chamada, o mesmo custo que a janela global
    # existe para limitar; quem chama já tem sessão, então nenhum bloqueio por IP é preciso.
    app[LIMITE].registrar_tentativa()
    if not await _senha_confere(app, atual):
        return resposta_erro(401, "senha_invalida")
    try:
        await _guardar_senha(app, nova)
    except SenhaCurta:
        return resposta_erro(400, "senha_curta")
    # Why: section 9 wants the old panel sessions and the old machine credential dead the
    # moment the password changes, because the password change may be the answer to a leak.
    # Por que: a seção 9 quer as sessões antigas do painel e a credencial de máquina antiga
    # mortas assim que a senha muda, porque a troca pode ser a resposta a um vazamento.
    app[SESSOES].revogar_todas()
    app[SEGREDOS].valor = replace(
        segredos_de(app), api_token=rotacionar_api_token(app[AMBIENTE].dir_data)
    )
    await _revogar_barramento(app)
    return _sessao_nova(request)
