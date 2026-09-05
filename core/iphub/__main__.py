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
        # Why: section 9, the claim is public while there is no password, so the hub belongs to
        # whoever opens the panel first; the log says where that is and that it is still open,
        # because the only defence left is configuring it now.
        # Por que: seção 9, a posse é pública enquanto não há senha, então o hub é de quem
        # abrir o painel primeiro; o log diz onde isso é e que ainda está aberto, porque a
        # única defesa que resta é configurar agora.
        log.info(
            "not configured yet: anyone reaching http://%s:%d can set the panel password",
            amb.bind,
            amb.porta,
        )
    return criar_app(amb, config=cfg, segredos=segredos)


def main() -> int:
    # Why: the diary of the panel wants every command a driver wrote, which is DEBUG, and the
    # log of the container wants the lines a human reads while it boots, which is INFO. The
    # logger goes down to DEBUG so the diary sees everything, and the handler that writes to
    # the terminal keeps its own floor so the container log stays readable.
    # Por que: o diário do painel quer todo comando que um driver escreveu, que é DEBUG, e o
    # log do container quer as linhas que um humano lê enquanto ele sobe, que é INFO. O logger
    # desce para DEBUG para o diário ver tudo, e o handler que escreve no terminal guarda o
    # piso dele para o log do container seguir legível.
    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.INFO)
    try:
        amb = Ambiente.do_ambiente()
        app = preparar(amb)
    except Exception as erro:
        # Why: a traceback in a container log teaches the integrator nothing; the reason for
        # the refusal and a status of 1 do. The boot walks the driver catalog, so the failure
        # can now be anything a module raises while it is imported, not only a file or a value.
        # Por que: um traceback no log do container não ensina nada ao integrador; o motivo da
        # recusa e um status 1 ensinam. O boot percorre o catálogo de drivers, então a falha
        # pode ser qualquer coisa que um módulo levante ao ser importado, não só arquivo ou
        # valor.
        log.error("refusing to boot: %s: %s", type(erro).__name__, erro)
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
