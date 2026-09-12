# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Forgetting the panel password, from the machine that holds the data directory.

 gives the hub no password recovery over the network, on purpose: there is no mail,
no second factor and no cloud to prove who the owner is, so a route that reset the password
would be the way in, not the way back. What proves ownership of an appliance is reaching its
data directory, which means the box itself or the host that runs the container.

This clears the password and hands the hub back to the first access, keeping the
equipment, their numbers on the app and the scenes: erasing config.json would take those with
it. Every session dies and the api_token is rotated, the same way changing the password does,
because whoever could not get in must not keep a credential that was issued before.
"""

import sys
from dataclasses import replace
from pathlib import Path

from iphub.ambiente import Ambiente
from iphub.config import carregar, salvar
from iphub.segredos import rotacionar_api_token
from iphub.sessoes import Sessoes


def esquecer(dir_data: Path) -> bool:
    """Clears the password of the installation; True when there was one to clear."""
    cfg = carregar(dir_data)
    if not cfg.configurado:
        return False
    salvar(replace(cfg, senha_salt="", senha_hash="", senha_iteracoes=0), dir_data)
    Sessoes(dir_data).revogar_todas()
    rotacionar_api_token(dir_data)
    return True


def main() -> int:
    amb = Ambiente.do_ambiente()
    if not esquecer(amb.dir_data):
        print("this hub has no password: it is already at the first access")
        return 0
    print(
        "password cleared. Restart the hub and open the panel: whoever reaches it first "
        "becomes the owner, so do it now.\n"
        "senha apagada. Reinicie o hub e abra o painel: quem chegar primeiro vira o dono, "
        "entao faca isso agora."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
