# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The routes of the remote door: the window and the second factor.

Three locks, and all three have to open before anyone reaches this hub from the internet:

1. the window. Twenty four hours, opened by the owner from the panel or by the integrator
   from the app, and closed by itself. Outside it there is no socket and the address of the
   relay is not even in the allowlist of the gate, so a request on it dies at the Host check;
2. the code of the window, drawn when it is armed and published to the app of the customer,
   so whoever gets in from outside is whoever armed it there. The relay carries bytes and
   authenticates nobody, which is why this code is what authorises;
3. the login of the hub, with the password AND the code of the authenticator. A session
   opened on the LAN with the password alone does not pass through the relay.

Arming asks for all three to be POSSIBLE first: a relay to dial, a second factor enrolled and
a password that meets the current minimum. Refusing at the moment of arming, with a code that
says which one is missing, is the difference between a door that will not open and a door
that opens onto nothing.
"""

import time
from dataclasses import replace

from aiohttp import web

from iphub import totp
from iphub.api.comum import (
    OTP_PENDENTE,
    REMOTO,
    SESSOES,
    campo,
    com_sessao,
    config_de,
    ler_corpo,
    relay_de,
    resposta_ok,
    trocar_config,
)
from iphub.auth import conferir as conferir_senha
from iphub.portao import resposta_erro

SEM_HOME = "sem_home"
SEM_RELAY = "sem_relay"
SEM_OTP = "sem_otp"
SENHA_FRACA = "senha_fraca"


def faltando(app: web.Application) -> list[str]:
    """What the door is still missing before it can be armed, in the order it is fixed."""
    cfg = config_de(app)
    falta = []
    # The door is a service of the maker for the installations the maker registers, and
    # what names an installation is the home of the app, written by the miniApp of the
    # maker. Without it the panel does not even show the door; with it, the relay asks the
    # registry of the maker for real, which is a check this code cannot forge.
    if not cfg.remoto_home:
        falta.append(SEM_HOME)
    # The address of the relay is a setting of the FLEET and comes from the environment of
    # the appliance, so it is not something the owner types here; when it is missing, what
    # is missing is on the box and not on this screen.
    if not relay_de(app):
        falta.append(SEM_RELAY)
    if not cfg.otp_segredo:
        falta.append(SEM_OTP)
    if not cfg.senha_forte:
        falta.append(SENHA_FRACA)
    return falta


async def fechar_janela(app: web.Application) -> None:
    """Closes the window, drops the socket, and ends every session the window let in."""
    servico = app[REMOTO]
    servico.janela.desarmar()
    # A session opened through the relay was good for the window it came in by; a window
    # closed by hand takes those with it, the way the one that expired does.
    app[SESSOES].revogar_remotas()
    # The window is the truth and the process follows it; doing it here instead of waiting
    # for the next tick is what makes the panel show a door that is really closed.
    await servico.reconciliar()


def _situacao(request: web.Request) -> web.Response:
    cfg = config_de(request.app)
    servico = request.app[REMOTO]
    falta = faltando(request.app)
    return resposta_ok(
        disponivel=SEM_HOME not in falta,
        home=cfg.remoto_home,
        armado=servico.janela.ativa(),
        restante_s=servico.janela.restante_s(),
        # The code of the open window travels to whoever already holds a session of this
        # hub; it is what the integrator types from the app, and it dies with the window.
        codigo=servico.janela.codigo(),
        url=servico.url() if servico.janela.ativa() else "",
        conectado=servico.aberto(),
        # The relay asked the registry of the maker and the home of this hub was not there.
        recusado=servico.recusado(),
        otp_ativo=bool(cfg.otp_segredo),
        pronto=not falta,
        faltando=falta,
    )


@com_sessao
async def situacao(request: web.Request) -> web.Response:
    return _situacao(request)


@com_sessao
async def armar(request: web.Request) -> web.Response:
    """Opens or closes the window, and makes the socket agree with it before answering."""
    app = request.app
    dados = await ler_corpo(request)
    if dados is None or not isinstance(dados.get("armar"), bool):
        return resposta_erro(400, "corpo_invalido")
    if not dados["armar"]:
        await fechar_janela(app)
        return _situacao(request)
    falta = faltando(app)
    if falta:
        return resposta_erro(409, falta[0])
    servico = app[REMOTO]
    servico.janela.armar()
    # The window is the truth and the process follows it; doing it here instead of waiting
    # for the next tick is what makes the panel show a door that is really open.
    await servico.reconciliar()
    return _situacao(request)


@com_sessao
async def otp_comecar(request: web.Request) -> web.Response:
    """Hands over a fresh secret ONCE, while the owner points the phone at it.

    Nothing is written yet: a secret that was shown and never confirmed is a secret nobody
    holds, and it dies with this daemon or with the next enrolment.

    A hub that already has a second factor refuses a new one here: replacing it would be a
    way for whoever holds a session, and not the password, to move the factor to a phone of
    their own. Removing asks for the password, so replacing goes through removing.
    """
    app = request.app
    if config_de(app).otp_segredo:
        return resposta_erro(409, "otp_ja_ativo")
    segredo = totp.gerar_segredo()
    app[OTP_PENDENTE].valor = segredo
    cfg = config_de(app)
    conta = cfg.nome_instalacao or "Tuya IP Hub"
    return resposta_ok(segredo=segredo, uri=totp.uri(segredo, conta))


@com_sessao
async def otp_confirmar(request: web.Request) -> web.Response:
    """Turns a secret that was shown into the second factor, by proving the phone has it."""
    app = request.app
    dados = await ler_corpo(request)
    codigo = campo(dados, "codigo") if dados is not None else None
    if codigo is None:
        return resposta_erro(400, "corpo_invalido")
    pendente = app[OTP_PENDENTE].valor
    if not pendente:
        return resposta_erro(409, "sem_pareamento")
    passo = totp.conferir(pendente, codigo, time.time(), ultimo_passo=0)
    if passo is None:
        return resposta_erro(401, "codigo_invalido")
    trocar_config(app, replace(config_de(app), otp_segredo=pendente, otp_passo=passo))
    app[OTP_PENDENTE].valor = ""
    return resposta_ok(ativo=True)


@com_sessao
async def otp_remover(request: web.Request) -> web.Response:
    """Takes the second factor away, which takes the remote door with it."""
    app = request.app
    dados = await ler_corpo(request)
    senha = campo(dados, "senha") if dados is not None else None
    if senha is None:
        return resposta_erro(400, "corpo_invalido")
    cfg = config_de(app)
    if not conferir_senha(senha, cfg.senha_salt, cfg.senha_hash, cfg.senha_iteracoes):
        return resposta_erro(401, "senha_invalida")
    # A door whose second lock was removed is not a door this hub leaves open.
    app[REMOTO].janela.desarmar()
    trocar_config(app, replace(cfg, otp_segredo="", otp_passo=0))
    app[OTP_PENDENTE].valor = ""
    # Every session that showed two factors showed one that no longer exists.
    app[SESSOES].revogar_todas()
    await app[REMOTO].reconciliar()
    return resposta_ok(ativo=False)
