# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""First use and session routes: state, ownership, login, logout and password change."""

import asyncio
import re
import time
from dataclasses import replace

from aiohttp import web

from iphub import totp
from iphub.api.comum import (
    AMBIENTE,
    LIMITE,
    REMOTO,
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
from iphub.auth import forte as auth_forte
from iphub.dpbus.socket import BARRAMENTO
from iphub.portao import CABECALHO_DO_CLIENTE, CHAVE_REMOTO, ip_do_pedido, resposta_erro
from iphub.segredos import rotacionar_api_token
from iphub.versao import SCHEMA_VERSION, VERSAO


def _ip(request: web.Request) -> str:
    return ip_do_pedido(request, frozenset(config_de(request.app).proxies_confiaveis))


def _quem_tenta(request: web.Request) -> str:
    """What the rate limit counts against: the address, or the browser the relay named.

    Every request over the relay arrives from the socket of this same daemon, so counting by
    address would put every integrator in one bucket and let the five typos of one of them
    lock the remote door for all the others. The relay writes where the browser came from
    and erases the same header if the browser sent one, so it is not a name the caller chose.
    """
    if request.get(CHAVE_REMOTO) is None:
        return _ip(request)
    # Over the relay there is no identity, because the relay authenticates nobody; what it
    # writes is where the browser came from, and that is enough to keep the typos of one
    # person away from the door of the others.
    cliente = request.headers.get(CABECALHO_DO_CLIENTE, "").strip()
    return f"relay:{cliente}" if cliente else _ip(request)


def _sessao_nova(request: web.Request, fatores: int = 1) -> web.Response:
    # Through the relay the session is good for this window and no other: the code of the
    # window is checked once, here, so a token that outlived the window would be a way back
    # in with no code asked on the next one.
    valida_ate = request.app[REMOTO].janela.fim() if request.get(CHAVE_REMOTO) else 0.0
    token, expira_em_s = request.app[SESSOES].criar(fatores, valida_ate)
    return resposta_ok(token=token, expira_em_s=expira_em_s, fatores=fatores)


async def _guardar_senha(app: web.Application, nova: str) -> None:
    """Raises SenhaCurta when the password is under the minimum."""
    # Two hundred thousand iterations take tenths of a second on the reference ARM board,
    # and on the event loop that is every other request of the panel waiting in line.
    salt, hash_senha, iteracoes = await asyncio.to_thread(gerar_hash, nova)
    trocar_config(
        app,
        replace(
            config_de(app),
            senha_salt=salt,
            senha_hash=hash_senha,
            senha_iteracoes=iteracoes,
            # A password that was just accepted meets the minimum of today, and this is the
            # only moment anyone knows that: a hash does not say how long the word was.
            senha_forte=True,
        ),
    )


def _anotar_forca(app: web.Application, informada: str) -> None:
    """The login is where a password from before the current rule is recognised as short."""
    forte = auth_forte(informada)
    cfg = config_de(app)
    if cfg.senha_forte != forte:
        trocar_config(app, replace(cfg, senha_forte=forte))


async def _codigo_confere(app: web.Application, codigo: str) -> bool:
    """The code of the authenticator, and the step it spent, which is never spent twice."""
    cfg = config_de(app)
    passo = totp.conferir(cfg.otp_segredo, codigo, time.time(), cfg.otp_passo)
    if passo is None:
        return False
    trocar_config(app, replace(config_de(app), otp_passo=passo))
    return True


async def _senha_confere(app: web.Application, informada: str) -> bool:
    cfg = config_de(app)
    return await asyncio.to_thread(
        conferir_senha, informada, cfg.senha_salt, cfg.senha_hash, cfg.senha_iteracoes
    )


def _renovar_posse(app: web.Application) -> None:
    """Taking ownership ends the previous owner: every session and the machine credential."""
    # A data directory whose config.json was erased by hand is an unconfigured hub that
    # still holds live sessions and the old api_token of whoever owned it before.
    app[SESSOES].revogar_todas()
    app[SEGREDOS].valor = replace(
        segredos_de(app), api_token=rotacionar_api_token(app[AMBIENTE].dir_data)
    )


async def _revogar_barramento(app: web.Application) -> None:
    """Rotating the machine credential closes the sockets it authenticated."""
    barramento = app.get(BARRAMENTO)
    if barramento is not None:
        await barramento.revogar()


# The name reaches the bridge inside the JSON of a string data point and the top of
# every panel, so it is short and carries no control character; anything else is the
# integrator's, in any alphabet.
NOME_INSTALACAO_MAXIMO = 60
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


@com_sessao
async def instalacao(request: web.Request) -> web.Response:
    """Renames the installation; the name is the only thing about it the owner edits."""
    app = request.app
    dados = await ler_corpo(request)
    nome = campo(dados, "nome") if dados is not None else None
    if nome is None:
        return resposta_erro(400, "corpo_invalido")
    nome = nome.strip()
    if len(nome) > NOME_INSTALACAO_MAXIMO or _CONTROLE.search(nome):
        return resposta_erro(400, "nome_invalido")
    trocar_config(app, replace(config_de(app), nome_instalacao=nome))
    return resposta_ok(nome_instalacao=nome)


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
    # The claim is public now, so the check that a password does not exist yet
    # and the write of the first one are one step; two racers must not become two owners.
    async with app[TRAVA_POSSE]:
        if config_de(app).configurado:
            return resposta_erro(409, "ja_configurado")
        # No credential is checked here, so there is nothing to block an address for; the
        # global ceiling stays because the route still spends a PBKDF2 on the new password.
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
    ip = _quem_tenta(request)
    if not limite.permitido(ip):
        return resposta_erro(429, "muitas_tentativas")
    de_fora = bool(request.get(CHAVE_REMOTO))
    if de_fora:
        # The code of the window is the FIRST lock from outside, before the password is even
        # looked at: it says this is the person who armed the window in the app of the
        # platform, which is the identity this hub has of the integrator. Checked first, it
        # costs nothing, and whoever does not hold it learns nothing about the password and
        # spends none of the global window this hub has for PBKDF2.
        acesso = campo(dados, "acesso")
        if acesso is None:
            return resposta_erro(401, "acesso_exigido")
        if not app[REMOTO].janela.confere(acesso):
            limite.registrar_falha(ip)
            return resposta_erro(401, "acesso_invalido")
        if not config_de(app).otp_segredo:
            # The door cannot be armed without a second factor, so this is a hub whose
            # factor was removed while somebody was outside; the answer is no, not a
            # password prompt.
            return resposta_erro(403, "acesso_negado")
    # The global window is justified by the cost of one PBKDF2, so only the check of a
    # real secret spends it; otherwise any malformed request locks the owner out of the hub.
    limite.registrar_tentativa()
    if not await _senha_confere(app, informada):
        limite.registrar_falha(ip)
        return resposta_erro(401, "senha_invalida")
    _anotar_forca(app, informada)
    codigo = campo(dados, "codigo")
    fatores = 1
    if config_de(app).otp_segredo:
        # Through the relay the code is not optional. On the LAN it is offered and taken:
        # an integrator who types it gets a session that also works from outside.
        if codigo is None:
            if de_fora:
                return resposta_erro(401, "otp_exigido")
        elif not await _codigo_confere(app, codigo):
            limite.registrar_falha(ip)
            return resposta_erro(401, "codigo_invalido")
        else:
            fatores = 2
    limite.registrar_sucesso(ip)
    return _sessao_nova(request, fatores)


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
    # This route spends two PBKDF2 per call, the same cost the global window exists to
    # bound; the caller already holds a session, so no block per IP is needed on top of it.
    app[LIMITE].registrar_tentativa()
    if not await _senha_confere(app, atual):
        return resposta_erro(401, "senha_invalida")
    try:
        await _guardar_senha(app, nova)
    except SenhaCurta:
        return resposta_erro(400, "senha_curta")
    # Wants the old panel sessions and the old machine credential dead the
    # moment the password changes, because the password change may be the answer to a leak.
    app[SESSOES].revogar_todas()
    app[SEGREDOS].valor = replace(
        segredos_de(app), api_token=rotacionar_api_token(app[AMBIENTE].dir_data)
    )
    await _revogar_barramento(app)
    return _sessao_nova(request)
