# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Forgetting the panel password, from the machine that holds the data directory.

Section 9 gives the hub no password recovery over the network, on purpose: there is no mail,
no second factor and no cloud to prove who the owner is, so a route that reset the password
would be the way in, not the way back. What proves ownership of an appliance is reaching its
data directory, which means the box itself or the host that runs the container.

This clears the password and hands the hub back to the first access of section 9, keeping the
equipment, their numbers on the app and the scenes: erasing config.json would take those with
it. Every session dies and the api_token is rotated, the same way changing the password does,
because whoever could not get in must not keep a credential that was issued before.

Esquecer a senha do painel, da máquina que tem o diretório de dados.

A seção 9 não dá ao hub recuperação de senha pela rede, de propósito: não há e-mail, nem
segundo fator, nem nuvem que prove quem é o dono, então uma rota que zerasse a senha seria a
porta de entrada, não a de volta. O que prova posse de um appliance é alcançar o diretório de
dados dele, o que significa a própria caixa ou o host que roda o container.

Isto apaga a senha e devolve o hub ao primeiro acesso da seção 9, mantendo os equipamentos, os
números no app e as cenas: apagar o config.json levaria tudo isso junto. Toda sessão morre e o
api_token é rotacionado, do mesmo jeito que a troca de senha faz, porque quem não conseguia
entrar não pode ficar com uma credencial emitida antes.
"""

import sys
from dataclasses import replace
from pathlib import Path

from iphub.ambiente import Ambiente
from iphub.config import carregar, salvar
from iphub.segredos import rotacionar_api_token
from iphub.sessoes import Sessoes


def esquecer(dir_data: Path) -> bool:
    """Clears the password of the installation; True when there was one to clear.

    Apaga a senha da instalação; True quando havia uma para apagar.
    """
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
        print("this hub has no password: it is already at the first access of section 9")
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
