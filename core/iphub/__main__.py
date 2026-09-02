# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Entry point: python -m iphub.

Ponto de entrada: python -m iphub.
"""

import logging

from aiohttp import web

from iphub.ambiente import Ambiente
from iphub.app import criar_app
from iphub.versao import VERSAO


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    amb = Ambiente.do_ambiente()
    app = criar_app(amb, hosts_permitidos=frozenset())
    logging.getLogger("iphub").info(
        "Tuya IP Hub %s listening on http://%s:%d (panel: %s)",
        VERSAO,
        amb.bind,
        amb.porta,
        amb.dir_painel,
    )
    web.run_app(app, host=amb.bind, port=amb.porta, print=None)


if __name__ == "__main__":
    main()
