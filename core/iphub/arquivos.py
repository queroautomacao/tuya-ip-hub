# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Files of the data directory: every write is atomic and every file is born 0600.

Arquivos do diretório de dados: toda escrita é atômica e todo arquivo nasce 0600.
"""

import errno
import json
import os
import secrets
from pathlib import Path

MODO_SEGREDO = 0o600
MODO_DIRETORIO = 0o700

# Why: O_NOFOLLOW answers ELOOP on Linux and EMLINK on part of the BSD family, and both say
# the same thing here: the name is a symlink, and this daemon does not read through it.
# Por que: o O_NOFOLLOW responde ELOOP no Linux e EMLINK em parte da família BSD, e os dois
# dizem a mesma coisa aqui: o nome é um link simbólico, e este daemon não lê através dele.
_ERROS_DE_LINK = (errno.ELOOP, errno.EMLINK)


def _modo_alvo(caminho: Path) -> int:
    """Never widens: a file already tighter than 0600 keeps everything it denies.

    Nunca alarga: um arquivo já mais fechado que 0600 mantém tudo que ele nega.
    """
    try:
        atual = caminho.stat().st_mode & 0o777
    except OSError:
        return MODO_SEGREDO
    return atual & MODO_SEGREDO


def _caminho_temporario(caminho: Path) -> Path:
    # Why: the temporary file lives in the same directory, so os.replace stays inside one
    # filesystem, which is what makes it atomic.
    # Por que: o temporário fica no mesmo diretório, então o os.replace fica dentro de um só
    # sistema de arquivos, que é o que o torna atômico.
    return caminho.with_name(f".{caminho.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")


def _remover(caminho: Path) -> None:
    try:
        caminho.unlink(missing_ok=True)
    except OSError:
        pass


def _sincronizar_diretorio(diretorio: Path) -> None:
    """Makes the rename itself durable, not only the bytes of the temporary file.

    Torna a própria troca de nome durável, não só os bytes do arquivo temporário.
    """
    # Why: the appliance goes down by losing power, and a directory entry that never reached
    # the disk takes config.json back to the previous content, or to none at all.
    # Por que: o appliance cai por falta de energia, e uma entrada de diretório que nunca
    # chegou ao disco leva o config.json de volta ao conteúdo anterior, ou a nenhum.
    try:
        descritor = os.open(diretorio, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descritor)
    except OSError:
        # Why: fsync over a directory is not portable, and a platform that refuses it must not
        # turn a write that already succeeded into a failure.
        # Por que: fsync sobre diretório não é portável, e uma plataforma que o recusa não pode
        # transformar uma escrita que já deu certo em falha.
        pass
    finally:
        os.close(descritor)


def garantir_diretorio(caminho: Path) -> None:
    """Data directory 0700 on creation, and tightened when it was already there.

    Diretório de dados 0700 ao criar, e fechado quando já estava lá.
    """
    # Why: this directory holds every secret of section 9, and the process umask of the host
    # is usually 022, which would let any local user list the names of those files.
    # Por que: este diretório guarda todo segredo da seção 9, e o umask do hospedeiro costuma
    # ser 022, o que deixaria qualquer usuário local listar os nomes desses arquivos.
    caminho.mkdir(parents=True, exist_ok=True, mode=MODO_DIRETORIO)
    atual = caminho.stat().st_mode & 0o777
    alvo = atual & MODO_DIRETORIO
    if alvo != atual:
        caminho.chmod(alvo)


def escrever_texto(caminho: Path, texto: str) -> None:
    """Replaces the whole content through a temporary file in the same directory, mode 0600.

    Substitui o conteúdo inteiro por um arquivo temporário no mesmo diretório, modo 0600.
    """
    modo = _modo_alvo(caminho)
    temporario = _caminho_temporario(caminho)
    descritor = os.open(temporario, os.O_CREAT | os.O_EXCL | os.O_WRONLY, modo)
    try:
        with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
            # Why: os.open filters the mode through the umask, which can hand the secret to
            # the group; fchmod states the mode the file must end up with.
            # Por que: o os.open filtra o modo pelo umask, que pode entregar o segredo ao
            # grupo; o fchmod declara o modo com que o arquivo tem de terminar.
            os.fchmod(arquivo.fileno(), modo)
            arquivo.write(texto)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        _sincronizar_diretorio(caminho.parent)
    except BaseException:
        # Why: a half written secret must never be left behind for a reader to find.
        # Por que: um segredo escrito pela metade nunca pode ficar para um leitor achar.
        _remover(temporario)
        raise


def escrever_json(caminho: Path, dados: dict) -> None:
    escrever_texto(caminho, json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def ler_texto(caminho: Path) -> str | None:
    """Reads the name itself, never what a symlink points at; absent file gives None.

    Lê o próprio nome, nunca o que um link simbólico aponta; arquivo ausente devolve None.
    """
    try:
        descritor = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as erro:
        # Why: a link planted in the data directory would turn the daemon into a reader of any
        # file the container can open, and the ownership code goes to the log.
        # Por que: um link plantado no diretório de dados transformaria o daemon num leitor de
        # qualquer arquivo que o container abra, e o código de posse vai para o log.
        if erro.errno in _ERROS_DE_LINK:
            return None
        raise
    with os.fdopen(descritor, encoding="utf-8") as arquivo:
        return arquivo.read()


def ler_json(caminho: Path) -> dict | None:
    """The object is the only accepted shape; a list or a number is a corrupt file.

    O objeto é a única forma aceita; uma lista ou um número é arquivo corrompido.
    """
    texto = ler_texto(caminho)
    if texto is None:
        return None
    dados = json.loads(texto)
    if not isinstance(dados, dict):
        raise ValueError(f"{caminho.name} must hold a JSON object, found {type(dados).__name__}")
    return dados


def modo_de(caminho: Path) -> int:
    return caminho.stat().st_mode & 0o777
