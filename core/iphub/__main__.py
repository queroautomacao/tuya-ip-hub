# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Entry point: python -m iphub."""

import asyncio
import logging
import signal
import sys

from aiohttp import web

from iphub.ambiente import Ambiente
from iphub.api.comum import NOMES_MDNS
from iphub.app import criar_app
from iphub.arquivos import garantir_diretorio
from iphub.config import carregar as carregar_config
from iphub.segredos import abrir as abrir_segredos
from iphub.versao import VERSAO

log = logging.getLogger("iphub")


def preparar(amb: Ambiente) -> web.Application:
    """Opens the data directory, generating the secrets on the very first boot."""
    garantir_diretorio(amb.dir_data)
    segredos = abrir_segredos(amb.dir_data)
    cfg = carregar_config(amb.dir_data)
    if not cfg.configurado:
        # The claim is public while there is no password, so the hub belongs to
        # whoever opens the panel first; the log says where that is and that it is still open,
        # because the only defence left is configuring it now.
        log.info(
            "not configured yet: anyone reaching http://%s:%d can set the panel password",
            amb.bind,
            amb.porta,
        )
    return criar_app(amb, config=cfg, segredos=segredos)


def main() -> int:
    # The diary of the panel wants every command a driver wrote, which is DEBUG, and the
    # log of the container wants the lines a human reads while it boots, which is INFO. The
    # logger goes down to DEBUG so the diary sees everything, and the handler that writes to
    # the terminal keeps its own floor so the container log stays readable.
    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.INFO)
    try:
        amb = Ambiente.do_ambiente()
        app = preparar(amb)
    except Exception as erro:
        # A traceback in a container log teaches the integrator nothing; the reason for
        # the refusal and a status of 1 do. The boot walks the driver catalog, so the failure
        # can now be anything a module raises while it is imported, not only a file or a value.
        log.error("refusing to boot: %s: %s", type(erro).__name__, erro)
        return 1
    log.info(
        "Tuya IP Hub %s listening on http://%s:%d (panel: %s; names %s)",
        VERSAO,
        amb.bind,
        amb.porta,
        amb.dir_painel,
        " and ".join(app[NOMES_MDNS]),
    )
    servir_ate_o_fim(app, amb)
    return 0


def servir_ate_o_fim(app: web.Application, amb: Ambiente) -> None:
    """Runs the daemon until a stop signal; a test replaces this to look at the boot alone."""
    asyncio.run(servir(app, amb))


async def servir(app: web.Application, amb: Ambiente) -> None:
    """The application on its port, until the clean stop of the container or a restart."""
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, amb.bind, amb.porta).start()
        parar = asyncio.Event()
        laco = asyncio.get_running_loop()
        for sinal in (signal.SIGTERM, signal.SIGINT):
            # The clean stop of the container and of the restart route: the routes drain,
            # the bus and the drivers close, and the process ends with a zero.
            laco.add_signal_handler(sinal, parar.set)
        await parar.wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    sys.exit(main())
