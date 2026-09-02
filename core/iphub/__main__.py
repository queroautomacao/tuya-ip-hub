# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Entry point: python -m iphub.

Ponto de entrada: python -m iphub.
"""

import logging
import sys

from aiohttp import web

from iphub.ambiente import Ambiente
from iphub.app import criar_app
from iphub.arquivos import garantir_diretorio
from iphub.config import carregar as carregar_config
from iphub.segredos import ARQUIVO_CODIGO
from iphub.segredos import abrir as abrir_segredos
from iphub.versao import VERSAO

log = logging.getLogger("iphub")


def preparar(amb: Ambiente) -> web.Application:
    """Opens the data directory, generating the secrets on the very first boot.

    Abre o diretório de dados, gerando os segredos no primeiro boot.
    """
    garantir_diretorio(amb.dir_data)
    segredos = abrir_segredos(amb.dir_data)
    cfg = carregar_config(amb.dir_data)
    if not cfg.configurado:
        # Why: the integrator reads the code from the container log to take ownership; once a
        # password exists the code is spent, and logging it would leave a live secret in a
        # file that nobody protects.
        # Por que: o integrador lê o código no log do container para tomar posse; com a senha
        # definida o código está gasto, e registrá-lo deixaria um segredo vivo num arquivo que
        # ninguém protege.
        log.info(
            "not configured yet: ownership code %s (also in %s)",
            segredos.codigo_de_posse,
            amb.dir_data / ARQUIVO_CODIGO,
        )
    return criar_app(amb, config=cfg, segredos=segredos)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        amb = Ambiente.do_ambiente()
        app = preparar(amb)
    except (OSError, ValueError) as erro:
        # Why: a traceback in a container log teaches the integrator nothing; the reason for
        # the refusal and a status of 1 do.
        # Por que: um traceback no log do container não ensina nada ao integrador; o motivo da
        # recusa e um status 1 ensinam.
        log.error("refusing to boot: %s", erro)
        return 1
    log.info(
        "Tuya IP Hub %s listening on http://%s:%d (panel: %s)",
        VERSAO,
        amb.bind,
        amb.porta,
        amb.dir_painel,
    )
    web.run_app(app, host=amb.bind, port=amb.porta, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
