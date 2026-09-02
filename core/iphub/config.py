# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Configuration of the installation: one file, read whole, written whole.

Configuração da instalação: um arquivo, lido inteiro, escrito inteiro.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from iphub import arquivos
from iphub.versao import SCHEMA_VERSION

ARQUIVO = "config.json"

# Why: senha_iteracoes is handed straight to pbkdf2_hmac on every login, so a hand edited
# huge value hangs the daemon and a tiny one makes the stored hash cheap to crack.
# Por que: senha_iteracoes vai direto para o pbkdf2_hmac em cada entrada, então um valor
# enorme editado na mão trava o daemon e um valor minúsculo barateia o hash guardado.
ITERACOES_MINIMAS = 1_000
ITERACOES_MAXIMAS = 2_000_000


class ConfigIncompativel(ValueError):
    """The file on disk is not the format this daemon speaks.

    O arquivo em disco não está no formato que este daemon fala.
    """


@dataclass(frozen=True)
class Config:
    nome_instalacao: str = ""
    idioma: str = "pt"
    hosts_permitidos: tuple[str, ...] = ()
    proxies_confiaveis: tuple[str, ...] = ()
    senha_salt: str = ""
    senha_hash: str = ""
    senha_iteracoes: int = 0

    @property
    def configurado(self) -> bool:
        return bool(self.senha_hash and self.senha_salt)


PADRAO = Config()


def carregar(dir_data: Path) -> Config:
    """Absent file: a hub that has never been configured. Wrong format: refuses to guess.

    Arquivo ausente: um hub que nunca foi configurado. Formato errado: recusa adivinhar.
    """
    caminho = dir_data / ARQUIVO
    try:
        dados = arquivos.ler_json(caminho)
    except ValueError as erro:
        raise ConfigIncompativel(
            f"{ARQUIVO} is not readable: {erro}. {_conserto(dir_data)}"
        ) from erro
    if dados is None:
        return PADRAO
    _conferir_schema(dados.get("schema_version"), dir_data)
    return Config(
        nome_instalacao=_texto(dados, "nome_instalacao", PADRAO.nome_instalacao),
        idioma=_texto(dados, "idioma", PADRAO.idioma),
        hosts_permitidos=_lista(dados, "hosts_permitidos", PADRAO.hosts_permitidos),
        proxies_confiaveis=_lista(dados, "proxies_confiaveis", PADRAO.proxies_confiaveis),
        senha_salt=_texto(dados, "senha_salt", PADRAO.senha_salt),
        senha_hash=_texto(dados, "senha_hash", PADRAO.senha_hash),
        senha_iteracoes=_iteracoes(dados, dir_data),
    )


def salvar(cfg: Config, dir_data: Path) -> None:
    arquivos.escrever_json(dir_data / ARQUIVO, {"schema_version": SCHEMA_VERSION, **asdict(cfg)})


def _conserto(dir_data: Path) -> str:
    return (
        f"Section 2.4 of CLAUDE.md forbids migration code, so the fix is to erase the data "
        f"directory ({dir_data}) and configure the hub again."
    )


def _conferir_schema(encontrado, dir_data: Path) -> None:
    # Why: the JSON true equals 1 for Python, and it is not a schema version.
    # Por que: o true do JSON é igual a 1 para o Python, e não é uma versão de schema.
    if type(encontrado) is not int or encontrado != SCHEMA_VERSION:
        raise ConfigIncompativel(
            f"{ARQUIVO} has schema_version {encontrado!r}, this daemon expects "
            f"{SCHEMA_VERSION}. {_conserto(dir_data)}"
        )


def _erro_tipo(chave: str, esperado: str, valor) -> ConfigIncompativel:
    return ConfigIncompativel(
        f"{ARQUIVO}: key {chave!r} must be {esperado}, found {type(valor).__name__}"
    )


def _texto(dados: dict, chave: str, padrao: str) -> str:
    valor = dados.get(chave, padrao)
    if not isinstance(valor, str):
        raise _erro_tipo(chave, "a string", valor)
    return valor


def _lista(dados: dict, chave: str, padrao: tuple[str, ...]) -> tuple[str, ...]:
    valor = dados.get(chave, list(padrao))
    if not isinstance(valor, list) or not all(isinstance(item, str) for item in valor):
        raise _erro_tipo(chave, "a list of strings", valor)
    return tuple(valor)


def _iteracoes(dados: dict, dir_data: Path) -> int:
    """Inside the band of section 9, or the default of a hub that has no password yet.

    Dentro da faixa da seção 9, ou o padrão de um hub que ainda não tem senha.
    """
    valor = _inteiro(dados, "senha_iteracoes", PADRAO.senha_iteracoes)
    if valor != PADRAO.senha_iteracoes and not ITERACOES_MINIMAS <= valor <= ITERACOES_MAXIMAS:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key 'senha_iteracoes' must be between {ITERACOES_MINIMAS} and "
            f"{ITERACOES_MAXIMAS}, found {valor}. {_conserto(dir_data)}"
        )
    return valor


def _inteiro(dados: dict, chave: str, padrao: int) -> int:
    valor = dados.get(chave, padrao)
    # Why: JSON true passes isinstance(valor, int), and it is not an iteration count.
    # Por que: o true do JSON passa em isinstance(valor, int), e não é contagem de iterações.
    if type(valor) is not int:
        raise _erro_tipo(chave, "an integer", valor)
    return valor
