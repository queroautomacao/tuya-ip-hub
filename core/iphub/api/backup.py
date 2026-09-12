# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Over HTTP: exporting the installation to a file and restoring one.

Exporting asks for a session and a password for the file. Restoring is the one route that a
hub with no owner answers, the same way it answers the claim of ownership: a new board with
a backup in hand is an installation coming back, and the password of the file is what proves
it. A hub that already has an owner asks for the session of that owner first.

A restore ends with the daemon restarting itself: the equipment, the licences, the scenes and
the numbers are mounted at boot, and mounting them twice is the kind of code that has two
truths.
"""

import asyncio
import base64
import logging
import re
import time
from dataclasses import replace

from aiohttp import web

from iphub import backup as modulo
from iphub.api.comum import (
    AMBIENTE,
    CONFIG,
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
)
from iphub.api.setup import _ip, _revogar_barramento
from iphub.api.sistema import ATRASO_REINICIO_S, ENCERRAR
from iphub.auth import SENHA_MINIMA
from iphub.config import salvar as salvar_config
from iphub.portao import resposta_erro
from iphub.segredos import rotacionar_api_token

log = logging.getLogger("iphub.api.backup")

# A backup of a full installation is a few hundred kilobytes; two megabytes leaves room and
# keeps a body that is not a backup from being read whole into memory.
CORPO_MAXIMO_BACKUP = 2 * 1024 * 1024
_NOME_DE_ARQUIVO = re.compile(r"[^a-z0-9]+")


def nome_do_arquivo(nome_instalacao: str, agora: float) -> str:
    """tuya-ip-hub-<installation>-<date>.iphub, in the characters every system accepts."""
    limpo = _NOME_DE_ARQUIVO.sub("-", nome_instalacao.lower()).strip("-")[:40]
    data = time.strftime("%Y-%m-%d", time.localtime(agora))
    return f"tuya-ip-hub-{limpo or 'backup'}-{data}{modulo.EXTENSAO}"


@com_sessao
async def exportar(request: web.Request) -> web.Response:
    app = request.app
    dados = await ler_corpo(request)
    senha = campo(dados, "senha") if dados is not None else None
    if senha is None:
        return resposta_erro(400, "corpo_invalido")
    if len(senha) < SENHA_MINIMA:
        return resposta_erro(400, "senha_curta")
    # The derivation costs three PBKDF2 of the panel, so it spends the same global window.
    app[LIMITE].registrar_tentativa()
    cfg = config_de(app)
    bruto = await asyncio.to_thread(modulo.exportar, cfg, senha)
    nome = nome_do_arquivo(cfg.nome_instalacao, time.time())
    return web.Response(
        body=bruto,
        content_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "Cache-Control": "no-store",
        },
    )


async def restaurar(request: web.Request) -> web.Response:
    """With an owner, the owner restores; without one, whoever holds the file and its password."""
    if config_de(request.app).configurado:
        return await _restaurar_do_dono(request)
    return await _restaurar(request)


@com_sessao
async def _restaurar_do_dono(request: web.Request) -> web.Response:
    return await _restaurar(request)


async def _restaurar(request: web.Request) -> web.Response:
    app = request.app
    limite = app[LIMITE]
    dados = await ler_corpo(request, maximo=CORPO_MAXIMO_BACKUP)
    senha = campo(dados, "senha") if dados is not None else None
    arquivo = campo(dados, "arquivo") if dados is not None else None
    if senha is None or arquivo is None:
        return resposta_erro(400, "corpo_invalido")
    try:
        bruto = base64.b64decode(arquivo, validate=True)
    except (ValueError, TypeError):
        return resposta_erro(400, "backup_invalido")
    ip = _ip(request)
    if not limite.permitido(ip):
        return resposta_erro(429, "muitas_tentativas")
    limite.registrar_tentativa()
    try:
        novo = await asyncio.to_thread(modulo.importar, bruto, senha)
    except modulo.SenhaErrada:
        limite.registrar_falha(ip)
        return resposta_erro(401, "senha_invalida")
    except modulo.BackupInvalido as erro:
        log.warning("a backup was refused: %s", erro)
        return resposta_erro(400, "backup_invalido")
    # Serialized with the claim of ownership: a restore and a claim racing on a virgin hub
    # must not produce two owners either.
    async with app[TRAVA_POSSE]:
        # The whole file replaces the whole configuration; what the box had is gone, which
        # is what a restore is. The vault of THIS board seals the secrets on the way in.
        salvar_config(novo, app[AMBIENTE].dir_data)
        app[CONFIG].valor = novo
        # Every session was opened against the installation that is no more, and the
        # machine credential belongs to the box, not to the backup.
        app[SESSOES].revogar_todas()
        app[SEGREDOS].valor = replace(
            segredos_de(app), api_token=rotacionar_api_token(app[AMBIENTE].dir_data)
        )
        await _revogar_barramento(app)
    limite.registrar_sucesso(ip)
    log.warning("the installation was restored from a backup; restarting")
    asyncio.get_running_loop().call_later(ATRASO_REINICIO_S, app[ENCERRAR])
    return resposta_ok(reiniciando=True, nome_instalacao=novo.nome_instalacao)
