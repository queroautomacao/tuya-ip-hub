# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""GET /health: liveness with version stamps and uptime.

GET /health: vitalidade com carimbos de versão e tempo no ar.
"""

import time

from aiohttp import web

from iphub.versao import SCHEMA_VERSION, VERSAO

INICIO = web.AppKey("inicio", float)


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "code": None,
            "versao": VERSAO,
            "schema_version": SCHEMA_VERSION,
            "uptime_s": int(time.monotonic() - request.app[INICIO]),
        }
    )
