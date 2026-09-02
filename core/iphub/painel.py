# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Serves the built panel (index.html and hashed assets) from a directory.

Serve o painel construído (index.html e assets com hash) a partir de um diretório.
"""

import os
from pathlib import Path

from aiohttp import web

from iphub.portao import TrataExpect, resposta_erro, rota_get


def registrar_painel(app: web.Application, dir_painel: Path, tratar_expect: TrataExpect) -> None:
    indice = dir_painel / "index.html"
    raiz_assets = (dir_painel / "assets").resolve()

    if not indice.is_file():

        async def painel_ausente(request: web.Request) -> web.Response:
            return resposta_erro(503, "painel_ausente")

        rota_get(app, "/", painel_ausente, tratar_expect)
        return

    async def indice_painel(request: web.Request) -> web.StreamResponse:
        # Why: a rebuild empties dist before writing, so the file is checked on every request.
        # Por que: um rebuild esvazia o dist antes de escrever, então o arquivo é checado a
        # cada pedido.
        if not indice.is_file():
            return resposta_erro(503, "painel_ausente")
        # Why: after an image upgrade the browser must fetch the index that names the new assets.
        # Por que: depois de atualizar, o navegador precisa do index que cita os assets novos.
        return web.FileResponse(indice, headers={"Cache-Control": "no-cache"})

    async def asset(request: web.Request) -> web.FileResponse:
        # Why: the stock static route only notices a missing file while writing the response,
        # past the middleware that turns errors into JSON, so the check happens here. A name
        # longer than the filesystem allows raises here too, and is just as much a 404.
        # Por que: a rota estática padrão só nota arquivo ausente ao escrever a resposta,
        # depois do middleware que converte erro em JSON, então a checagem fica aqui. Um nome
        # maior do que o sistema de arquivos aceita também estoura aqui, e é 404 do mesmo jeito.
        caminho = request.match_info["caminho"]
        if "\x00" in caminho:
            raise web.HTTPNotFound()
        try:
            arquivo = (raiz_assets / caminho).resolve()
            if raiz_assets not in arquivo.parents or not os.path.isfile(arquivo):
                raise web.HTTPNotFound()
        except (OSError, RuntimeError, ValueError):
            raise web.HTTPNotFound() from None
        return web.FileResponse(arquivo)

    rota_get(app, "/", indice_painel, tratar_expect)
    rota_get(app, "/assets/{caminho:.+}", asset, tratar_expect)
