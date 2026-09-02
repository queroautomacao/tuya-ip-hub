# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Secrets of the installation: the ownership code and the machine credential (api_token).

Segredos da instalação: o código de posse e a credencial de máquina (api_token).
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from iphub import arquivos

# Why: the code is dictated out loud, so I, O, 0 and 1 are out of the alphabet.
# Por que: o código é ditado em voz alta, então I, O, 0 e 1 ficam fora do alfabeto.
CODIGO_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODIGO_GRUPOS = 4
CODIGO_TAMANHO_GRUPO = 4

ARQUIVO_CODIGO = "codigo-de-posse.txt"
ARQUIVO_TOKEN = "api-token.txt"

TAMANHO_TOKEN_BYTES = 32
TOKEN_EXEMPLO = "troque-este-token-de-exemplo"


@dataclass(frozen=True)
class Segredos:
    codigo_de_posse: str
    api_token: str


def gerar_codigo_de_posse() -> str:
    """Groups of four, as in "ABCD-EFGH-JKLM-NPQR", to be read and typed without error.

    Grupos de quatro, como em "ABCD-EFGH-JKLM-NPQR", para ler e digitar sem erro.
    """
    grupos = [
        "".join(secrets.choice(CODIGO_ALFABETO) for _ in range(CODIGO_TAMANHO_GRUPO))
        for _ in range(CODIGO_GRUPOS)
    ]
    return "-".join(grupos)


def gerar_api_token() -> str:
    return secrets.token_urlsafe(TAMANHO_TOKEN_BYTES)


def abrir(dir_data: Path) -> Segredos:
    """Reads both files, generating and persisting whichever one is missing.

    Lê os dois arquivos, gerando e gravando o que estiver faltando.
    """
    arquivos.garantir_diretorio(dir_data)
    codigo = _ler_ou_gerar(dir_data / ARQUIVO_CODIGO, gerar_codigo_de_posse)
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
    return Segredos(codigo_de_posse=codigo, api_token=token)


def rotacionar_api_token(dir_data: Path) -> str:
    novo = gerar_api_token()
    arquivos.escrever_texto(dir_data / ARQUIVO_TOKEN, novo + "\n")
    return novo


def rotacionar_codigo_de_posse(dir_data: Path) -> str:
    """Spends the code: whoever takes ownership burns the value the log still carries.

    Gasta o código: quem toma posse queima o valor que o log ainda carrega.
    """
    # Why: the code is printed in the container log on the first boot, and a data directory
    # whose config.json was wiped by hand becomes an unconfigured hub again; without rotating,
    # an old log line takes ownership of it a second time.
    # Por que: o código é impresso no log do container no primeiro boot, e um diretório de
    # dados com o config.json apagado na mão volta a ser um hub sem dono; sem rotacionar, uma
    # linha antiga de log toma posse dele uma segunda vez.
    novo = gerar_codigo_de_posse()
    arquivos.escrever_texto(dir_data / ARQUIVO_CODIGO, novo + "\n")
    return novo


def conferir_codigo(informado: str, real: str) -> bool:
    """Constant time over the normalized forms: case, hyphens and blanks do not matter.

    Tempo constante sobre as formas normalizadas: caixa, hifens e brancos não importam.
    """
    alvo = _normalizar(real)
    if not alvo:
        # Why: an installation with no code on disk must not accept an empty code.
        # Por que: uma instalação sem código em disco não pode aceitar um código vazio.
        return False
    # Why: compare_digest refuses text with non-ASCII, and the informed value comes from the
    # network, so the comparison is made over bytes.
    # Por que: o compare_digest recusa texto com não-ASCII, e o valor informado vem da rede,
    # então a comparação é feita sobre bytes.
    return secrets.compare_digest(_bytes(_normalizar(informado)), _bytes(alvo))


def _normalizar(codigo: str) -> str:
    return "".join(c for c in codigo.upper() if c != "-" and not c.isspace())


def _bytes(codigo: str) -> bytes:
    # Why: a lone surrogate such as "\ud800" is accepted by json.loads and has no plain utf-8
    # form, so a strict encode would raise out of an unauthenticated route and answer 500
    # while the hub is still up for ownership. surrogatepass has a form for every string.
    # Por que: um surrogate solto como "\ud800" é aceito pelo json.loads e não tem forma em
    # utf-8 estrito, então um encode estrito estouraria numa rota sem autenticação e
    # responderia 500 com o hub ainda aberto para posse. O surrogatepass tem forma para
    # qualquer string.
    return codigo.encode("utf-8", errors="surrogatepass")


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
