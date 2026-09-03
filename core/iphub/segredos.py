# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Secret of the installation: the machine credential (api_token) the DP-bus uses.

Segredo da instalação: a credencial de máquina (api_token) que o DP-bus usa.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from iphub import arquivos

ARQUIVO_TOKEN = "api-token.txt"

TAMANHO_TOKEN_BYTES = 32
TOKEN_EXEMPLO = "troque-este-token-de-exemplo"


@dataclass(frozen=True)
class Segredos:
    api_token: str


def gerar_api_token() -> str:
    return secrets.token_urlsafe(TAMANHO_TOKEN_BYTES)


def abrir(dir_data: Path) -> Segredos:
    """Reads the token file, generating and persisting it when it is missing.

    Lê o arquivo do token, gerando e gravando quando ele está faltando.
    """
    arquivos.garantir_diretorio(dir_data)
    token = _ler_ou_gerar(dir_data / ARQUIVO_TOKEN, gerar_api_token)
    if token == TOKEN_EXEMPLO:
        # Why: the example value ships in the repository, so it is public; a hub booting with
        # it would hand the DP-bus to anyone who read the source.
        # Por que: o valor de exemplo viaja no repositório, então é público; um hub que subisse
        # com ele entregaria o DP-bus a qualquer um que tivesse lido o fonte.
        raise ValueError(
            f"{ARQUIVO_TOKEN} still holds the example value; erase the file so a real "
            f"api_token is generated on the next boot"
        )
    return Segredos(api_token=token)


def rotacionar_api_token(dir_data: Path) -> str:
    novo = gerar_api_token()
    arquivos.escrever_texto(dir_data / ARQUIVO_TOKEN, novo + "\n")
    return novo


def _ler_ou_gerar(caminho: Path, gerar: Callable[[], str]) -> str:
    texto = arquivos.ler_texto(caminho)
    if texto is None:
        valor = gerar()
        arquivos.escrever_texto(caminho, valor + "\n")
        return valor
    valor = texto.strip()
    if not valor:
        raise ValueError(f"{caminho.name} is empty; erase it so a new value is generated")
    return valor
