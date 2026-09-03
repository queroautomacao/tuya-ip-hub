# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Configuration of the installation: one file, read whole, written whole.

Configuração da instalação: um arquivo, lido inteiro, escrito inteiro.
"""

import ipaddress
from dataclasses import asdict, dataclass, field
from pathlib import Path

from iphub import arquivos
from iphub.versao import SCHEMA_VERSION

ARQUIVO = "config.json"

CHAVE_EQUIPAMENTOS = "equipamentos"

# Why: senha_iteracoes is handed straight to pbkdf2_hmac on every login, so a hand edited
# huge value hangs the daemon and a tiny one makes the stored hash cheap to crack.
# Por que: senha_iteracoes vai direto para o pbkdf2_hmac em cada entrada, então um valor
# enorme editado na mão trava o daemon e um valor minúsculo barateia o hash guardado.
ITERACOES_MINIMAS = 1_000
ITERACOES_MAXIMAS = 2_000_000

# Why: 45 characters is the longest textual IPv6, so anything longer is not an address and is
# refused before the parser of the standard library is asked to look at it.
# Por que: 45 caracteres é o maior IPv6 textual, então algo maior não é endereço e é recusado
# antes de o analisador da biblioteca padrão ser chamado.
IP_MAXIMO = 45


class ConfigIncompativel(ValueError):
    """The file on disk is not the format this daemon speaks.

    O arquivo em disco não está no formato que este daemon fala.
    """


@dataclass(frozen=True)
class Cadastro:
    """One registered device: the identity is the key and the ip is only today's address.

    Um equipamento cadastrado: a identidade é a chave e o ip é só o endereço de hoje.
    """

    # Why: section 6 makes the identity the key (uuid, MAC or serial) because the ip changes
    # with the lease and the discovery re-resolves it; a config keyed by ip would lose the
    # device on the next reboot of the router.
    # Por que: a seção 6 faz da identidade a chave (uuid, MAC ou serial) porque o ip muda com
    # a concessão e a descoberta o re-resolve; uma config chaveada por ip perderia o aparelho
    # no próximo reboot do roteador.
    identidade: str
    tipo: str
    nome: str = ""
    ip: str = ""
    # Why: frozen holds for the two maps by convention only, so nobody mutates them in place;
    # a driver that needs a different value gets a new Cadastro from the gestor.
    # Por que: o frozen vale para os dois mapas só por convenção, então ninguém os altera no
    # lugar; um driver que precisa de outro valor recebe um Cadastro novo do gestor.
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    nome_instalacao: str = ""
    idioma: str = "pt"
    hosts_permitidos: tuple[str, ...] = ()
    proxies_confiaveis: tuple[str, ...] = ()
    senha_salt: str = ""
    senha_hash: str = ""
    senha_iteracoes: int = 0
    equipamentos: tuple[Cadastro, ...] = ()

    @property
    def configurado(self) -> bool:
        return bool(self.senha_hash and self.senha_salt)


PADRAO = Config()


def ip_literal(texto: object) -> str | None:
    """The address in canonical form, or None for anything that is not an IP literal.

    O endereço na forma canônica, ou None para o que não for um IP literal.
    """
    # Why: section 9, whatever reaches a device takes an address and never a name, a URL or a
    # host with a port; anything else would make the hub a proxy into the LAN of the client,
    # resolving and reaching whatever was written. A scope id (fe80::1%eth0) names an interface
    # of this host and not an address on the segment, so it goes out with the names.
    # Por que: seção 9, o que alcança um aparelho recebe endereço e nunca nome, URL ou host com
    # porta; o resto faria do hub um proxy para a LAN do cliente, resolvendo e alcançando o que
    # foi escrito. Um escopo (fe80::1%eth0) nomeia uma interface deste host e não um endereço
    # no segmento, então ele sai junto com os nomes.
    if not isinstance(texto, str) or not texto or len(texto) > IP_MAXIMO or "%" in texto:
        return None
    try:
        return str(ipaddress.ip_address(texto))
    except ValueError:
        return None


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
        equipamentos=_equipamentos(dados, dir_data),
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


def _equipamentos(dados: dict, dir_data: Path) -> tuple[Cadastro, ...]:
    """A list of objects, or the empty tuple of a hub that has nothing registered yet.

    Uma lista de objetos, ou a tupla vazia de um hub que ainda não tem nada cadastrado.
    """
    valor = dados.get(CHAVE_EQUIPAMENTOS, [])
    if not isinstance(valor, list):
        raise _erro_tipo(CHAVE_EQUIPAMENTOS, "a list of objects", valor)
    cadastros = tuple(_cadastro(item, indice, dir_data) for indice, item in enumerate(valor))
    identidades = [cadastro.identidade for cadastro in cadastros]
    repetidas = sorted({i for i in identidades if identidades.count(i) > 1})
    if repetidas:
        # Why: the identity is the key of the equipment everywhere, so two rows sharing one
        # would silently become a single device and the integrator would lose a registration.
        # Por que: a identidade é a chave do equipamento em todo lugar, então duas linhas
        # dividindo uma virariam um só aparelho e o integrador perderia um cadastro.
        raise ConfigIncompativel(
            f"{ARQUIVO}: key {CHAVE_EQUIPAMENTOS!r} repeats the identidade {repetidas}"
        )
    return cadastros


def _cadastro(item: object, indice: int, dir_data: Path) -> Cadastro:
    onde = f"{CHAVE_EQUIPAMENTOS}[{indice}]"
    if not isinstance(item, dict):
        raise _erro_tipo(onde, "an object", item)
    cadastro = Cadastro(
        identidade=_texto_de(item, "identidade", onde),
        tipo=_texto_de(item, "tipo", onde),
        nome=_texto_de(item, "nome", onde),
        ip=_texto_de(item, "ip", onde),
        campos=_mapa_de(item, "campos", onde),
        segredos=_mapa_de(item, "segredos", onde),
    )
    for chave in ("identidade", "tipo"):
        if not getattr(cadastro, chave).strip():
            raise ConfigIncompativel(f"{ARQUIVO}: key '{onde}.{chave}' must be a non empty string")
    # Why: the write routes take an IP literal (section 9) and this file is the other door into
    # the same field; a hand edited hostname here would be dialled by the action route and turn
    # the hub into a proxy into the LAN. An empty ip is a registration whose address is not
    # known yet, which is a normal state.
    # Por que: as rotas de escrita recebem um IP literal (seção 9) e este arquivo é a outra
    # porta para o mesmo campo; um nome de host editado na mão aqui seria discado pela rota de
    # ação e faria do hub um proxy para a LAN. Um ip vazio é um cadastro cujo endereço ainda
    # não se conhece, que é estado normal.
    if cadastro.ip and ip_literal(cadastro.ip) is None:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key '{onde}.ip' must be an IP literal, found {cadastro.ip!r}. "
            f"{_conserto(dir_data)}"
        )
    return cadastro


def _texto_de(item: dict, chave: str, onde: str) -> str:
    valor = item.get(chave, "")
    if not isinstance(valor, str):
        raise _erro_tipo(f"{onde}.{chave}", "a string", valor)
    return valor


def _mapa_de(item: dict, chave: str, onde: str) -> dict[str, str]:
    valor = item.get(chave, {})
    ok = isinstance(valor, dict) and all(
        isinstance(nome, str) and isinstance(conteudo, str) for nome, conteudo in valor.items()
    )
    if not ok:
        raise _erro_tipo(f"{onde}.{chave}", "an object of strings", valor)
    return dict(valor)


def _inteiro(dados: dict, chave: str, padrao: int) -> int:
    valor = dados.get(chave, padrao)
    # Why: JSON true passes isinstance(valor, int), and it is not an iteration count.
    # Por que: o true do JSON passa em isinstance(valor, int), e não é contagem de iterações.
    if type(valor) is not int:
        raise _erro_tipo(chave, "an integer", valor)
    return valor
