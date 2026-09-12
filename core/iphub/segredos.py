# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Secret of the installation: the machine credential (api_token) the DP-bus uses."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from iphub import arquivos, cofre

ARQUIVO_TOKEN = "api-token.txt"

TAMANHO_TOKEN_BYTES = 32
TOKEN_EXEMPLO = "troque-este-token-de-exemplo"


@dataclass(frozen=True)
class Segredos:
    api_token: str


def gerar_api_token() -> str:
    return secrets.token_urlsafe(TAMANHO_TOKEN_BYTES)


def abrir(dir_data: Path) -> Segredos:
    """Reads the token file, generating and persisting it when it is missing."""
    arquivos.garantir_diretorio(dir_data)
    chaveiro = cofre.abrir(dir_data)
    token = _ler_ou_gerar(dir_data / ARQUIVO_TOKEN, gerar_api_token, chaveiro)
    if token == TOKEN_EXEMPLO:
        # The example value ships in the repository, so it is public; a hub booting with
        # it would hand the DP-bus to anyone who read the source.
        raise ValueError(
            f"{ARQUIVO_TOKEN} still holds the example value; erase the file so a real "
            f"api_token is generated on the next boot"
        )
    return Segredos(api_token=token)


def rotacionar_api_token(dir_data: Path) -> str:
    novo = gerar_api_token()
    _gravar(dir_data / ARQUIVO_TOKEN, novo, cofre.abrir(dir_data))
    return novo


def _gravar(caminho: Path, valor: str, chaveiro: cofre.Cofre) -> None:
    arquivos.escrever_texto(caminho, chaveiro.cifrar(valor) + "\n")


def _ler_ou_gerar(caminho: Path, gerar: Callable[[], str], chaveiro: cofre.Cofre) -> str:
    texto = arquivos.ler_texto(caminho)
    if texto is None:
        valor = gerar()
        _gravar(caminho, valor, chaveiro)
        return valor
    escrito = texto.strip()
    if not escrito:
        raise ValueError(f"{caminho.name} is empty; erase it so a new value is generated")
    valor = chaveiro.decifrar(escrito)
    if not valor:
        # A token this vault cannot open is a token nothing can authenticate with; a new
        # one is written, and the bridge pairs again, which is what a swapped board costs.
        valor = gerar()
        _gravar(caminho, valor, chaveiro)
    elif not cofre.cifrado(escrito):
        # Written in clear by an older daemon: sealed on the first boot that has a vault.
        _gravar(caminho, valor, chaveiro)
    return valor
