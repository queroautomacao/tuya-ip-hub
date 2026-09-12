# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Configuration of the installation: one file, read whole, written whole."""

import ipaddress
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from iphub import agenda, arquivos, cofre
from iphub.cenas import Cena, CenasInvalidas
from iphub.cenas import validar as validar_cenas
from iphub.dpbus import mapa
from iphub.drivers import manifesto
from iphub.versao import SCHEMA_VERSION

ARQUIVO = "config.json"

CHAVE_EQUIPAMENTOS = "equipamentos"
CHAVE_LICENCAS = "licencas"
CHAVE_NUMEROS = "numeros"
CHAVE_CENAS = "cenas"
CHAVE_AGENDAMENTOS = "agendamentos"

# The lists a registration carries, their ceilings and the rule of one item live
# with the vocabulary, because a manifest suggests items for the same lists; they
# are re-exported here because the registration is what carries them.
LISTAS = manifesto.LISTAS
LISTAS_MAXIMO = manifesto.LISTAS_MAXIMO
ROTULO_MAXIMO = manifesto.ROTULO_MAXIMO
VALOR_DE_LISTA_MAXIMO = manifesto.VALOR_DE_LISTA_MAXIMO
item_valido = manifesto.item_valido

# The id of a licence is a key of config.json and part of a route path, so it stays in
# the alphabet a JSON key and a URL segment share.
ID_DE_LICENCA = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")

# Senha_iteracoes is handed straight to pbkdf2_hmac on every login, so a hand edited
# huge value hangs the daemon and a tiny one makes the stored hash cheap to crack.
ITERACOES_MINIMAS = 1_000
ITERACOES_MAXIMAS = 2_000_000

# The ceiling of a level is a percentage of the scale every driver speaks.
NIVEL_MAXIMO = 100

# 45 characters is the longest textual IPv6, so anything longer is not an address and is
# refused before the parser of the standard library is asked to look at it.
IP_MAXIMO = 45


class ConfigIncompativel(ValueError):
    """The file on disk is not the format this daemon speaks."""


@dataclass(frozen=True)
class Cadastro:
    """One registered device: the identity is the key and the ip is only today's address."""

    # Makes the identity the key (uuid, MAC or serial) because the ip changes
    # with the lease and the discovery re-resolves it; a config keyed by ip would lose the
    # device on the next reboot of the router.
    identidade: str
    tipo: str
    nome: str = ""
    ip: str = ""
    # Frozen holds for the two maps by convention only, so nobody mutates them in place;
    # a driver that needs a different value gets a new Cadastro from the gestor.
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    # The loudest this equipment may be set to, from the panel, the app, a scene or the
    # bus: a child's room or a showroom gets a ceiling, and a set above it lands on it.
    nivel_maximo: int = NIVEL_MAXIMO
    # The inputs, the shortcuts and the modes of an equipment are pairs of a label the
    # customer reads and a value the driver takes, chosen by the integrator; the profile of
    # is built from them, so they live with the registration and nowhere else.
    listas: dict[str, tuple["Item", ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    """One entry of a list of the registration: the label the app shows, the value the
    driver takes.
    """

    rotulo: str
    valor: str


@dataclass(frozen=True)
class Licenca:
    """One licence : a device on the platform, with the identity the bridge of
    that product uses. The chave never leaves the daemon.
    """

    id: str
    produto: str
    nome: str = ""
    uuid: str = ""
    pid: str = ""
    chave: str = ""


@dataclass(frozen=True)
class Config:
    nome_instalacao: str = ""
    idioma: str = "pt"
    hosts_permitidos: tuple[str, ...] = ()
    proxies_confiaveis: tuple[str, ...] = ()
    senha_salt: str = ""
    senha_hash: str = ""
    senha_iteracoes: int = 0
    # Whether the stored password meets the current minimum. It cannot be derived from a
    # hash, so it is learned on the login that knows the plaintext, and an installation from
    # before the rule keeps working while the panel asks its owner to change it.
    senha_forte: bool = False
    # The remote door: the address of the relay this hub dials out to, for a box whose
    # environment carries none (IPHUB_REMOTO_RELAY wins whenever it is set). It is the same
    # for a whole fleet and it is not a credential: the relay authenticates no hub, and
    # everything about who may enter is checked here.
    remoto_relay: str = ""
    # The home of the app this installation belongs to, written by the miniApp of the maker
    # through the bus (DP 184). It is what the relay checks against the registry of the
    # maker, and without it the remote door does not exist on this hub.
    remoto_home: str = ""
    # The shared secret of the authenticator, and the last step it spent, which is what
    # keeps a code that was read over somebody's shoulder from being used twice.
    otp_segredo: str = ""
    otp_passo: int = 0
    equipamentos: tuple[Cadastro, ...] = ()
    licencas: tuple[Licenca, ...] = ()
    # Makes the numbers of a licence an ORDER over identities already
    # registered as equipment, so there is no second registry: the position IS the number on
    # the app, and an empty string is a number nobody occupies. A removal empties the slot
    # instead of shifting the rest, because a shift would silently move an equipment from
    # number 2 to number 1 in every automation the customer already built on the platform.
    numeros: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # A scene is data and the position of one is its number, the same way a
    # number of a licence is a position; the module that owns the format decides what a scene
    # is, and this file only says that the installation carries up to thirty two of them.
    cenas: tuple[Cena, ...] = ()
    # When a scene runs on its own: a time of day and the days of the week, in the zone of
    # the installation; an empty zone is the zone of the box.
    fuso: str = ""
    agendamentos: tuple[agenda.Agendamento, ...] = ()

    @property
    def configurado(self) -> bool:
        return bool(self.senha_hash and self.senha_salt)


PADRAO = Config()


def ip_literal(texto: object) -> str | None:
    """The address in canonical form, or None for anything that is not an IP literal."""
    # Whatever reaches a device takes an address and never a name, a URL or a
    # host with a port; anything else would make the hub a proxy into the LAN of the client,
    # resolving and reaching whatever was written. A scope id (fe80::1%eth0) names an interface
    # of this host and not an address on the segment, so it goes out with the names.
    if not isinstance(texto, str) or not texto or len(texto) > IP_MAXIMO or "%" in texto:
        return None
    try:
        return str(ipaddress.ip_address(texto))
    except ValueError:
        return None


def carregar(dir_data: Path) -> Config:
    """Absent file: a hub that has never been configured. Wrong format: refuses to guess."""
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
    return _abrir_segredos(de_dados(dados, dir_data), cofre.abrir(dir_data))


def de_dados(dados: dict, dir_data: Path = Path("backup")) -> Config:
    """A configuration from the object of the file, judged field by field; the directory
    only names where the error came from."""
    return Config(
        nome_instalacao=_texto(dados, "nome_instalacao", PADRAO.nome_instalacao),
        idioma=_texto(dados, "idioma", PADRAO.idioma),
        hosts_permitidos=_lista(dados, "hosts_permitidos", PADRAO.hosts_permitidos),
        proxies_confiaveis=_lista(dados, "proxies_confiaveis", PADRAO.proxies_confiaveis),
        senha_salt=_texto(dados, "senha_salt", PADRAO.senha_salt),
        senha_hash=_texto(dados, "senha_hash", PADRAO.senha_hash),
        senha_iteracoes=_iteracoes(dados, dir_data),
        senha_forte=_booleano(dados, "senha_forte", PADRAO.senha_forte),
        remoto_relay=_texto(dados, "remoto_relay", PADRAO.remoto_relay),
        remoto_home=_texto(dados, "remoto_home", PADRAO.remoto_home),
        otp_segredo=_texto(dados, "otp_segredo", PADRAO.otp_segredo),
        otp_passo=_inteiro(dados, "otp_passo", PADRAO.otp_passo),
        equipamentos=_equipamentos(dados, dir_data),
        licencas=_licencas(dados),
        numeros=_numeros(dados),
        cenas=_cenas(dados, dir_data),
        fuso=_fuso(dados),
        agendamentos=_agendamentos(dados, dir_data),
    )


def salvar(cfg: Config, dir_data: Path) -> None:
    selada = _selar_segredos(cfg, cofre.abrir(dir_data))
    arquivos.escrever_json(dir_data / ARQUIVO, {"schema_version": SCHEMA_VERSION, **asdict(selada)})


def _selar_segredos(cfg: Config, chaveiro: cofre.Cofre) -> Config:
    """The same configuration with every secret in the form the disk gets."""
    return _mapear_segredos(cfg, chaveiro.cifrar)


def _abrir_segredos(cfg: Config, chaveiro: cofre.Cofre) -> Config:
    return _mapear_segredos(cfg, chaveiro.decifrar)


def _mapear_segredos(cfg: Config, mudar) -> Config:
    # These four are the secrets of an installation: what activates a device on the
    # platform, what logs into an equipment, what the authenticator shares, and nothing else.
    return replace(
        cfg,
        otp_segredo=mudar(cfg.otp_segredo),
        licencas=tuple(replace(lic, chave=mudar(lic.chave)) for lic in cfg.licencas),
        equipamentos=tuple(
            replace(eq, segredos={nome: mudar(valor) for nome, valor in eq.segredos.items()})
            for eq in cfg.equipamentos
        ),
    )


def _conserto(dir_data: Path) -> str:
    return (
        f"There is no migration code, so the fix is to erase the data directory ({dir_data}) "
        f"and configure the hub again."
    )


def _conferir_schema(encontrado, dir_data: Path) -> None:
    # The JSON true equals 1 for Python, and it is not a schema version.
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


def _booleano(dados: dict, chave: str, padrao: bool) -> bool:
    valor = dados.get(chave, padrao)
    if not isinstance(valor, bool):
        raise _erro_tipo(chave, "a boolean", valor)
    return valor


def _lista(dados: dict, chave: str, padrao: tuple[str, ...]) -> tuple[str, ...]:
    valor = dados.get(chave, list(padrao))
    if not isinstance(valor, list) or not all(isinstance(item, str) for item in valor):
        raise _erro_tipo(chave, "a list of strings", valor)
    return tuple(valor)


def _iteracoes(dados: dict, dir_data: Path) -> int:
    """Inside the band, or the default of a hub that has no password yet."""
    valor = _inteiro(dados, "senha_iteracoes", PADRAO.senha_iteracoes)
    if valor != PADRAO.senha_iteracoes and not ITERACOES_MINIMAS <= valor <= ITERACOES_MAXIMAS:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key 'senha_iteracoes' must be between {ITERACOES_MINIMAS} and "
            f"{ITERACOES_MAXIMAS}, found {valor}. {_conserto(dir_data)}"
        )
    return valor


def _equipamentos(dados: dict, dir_data: Path) -> tuple[Cadastro, ...]:
    """A list of objects, or the empty tuple of a hub that has nothing registered yet."""
    valor = dados.get(CHAVE_EQUIPAMENTOS, [])
    if not isinstance(valor, list):
        raise _erro_tipo(CHAVE_EQUIPAMENTOS, "a list of objects", valor)
    cadastros = tuple(_cadastro(item, indice, dir_data) for indice, item in enumerate(valor))
    identidades = [cadastro.identidade for cadastro in cadastros]
    repetidas = sorted({i for i in identidades if identidades.count(i) > 1})
    if repetidas:
        # The identity is the key of the equipment everywhere, so two rows sharing one
        # would silently become a single device and the integrator would lose a registration.
        raise ConfigIncompativel(
            f"{ARQUIVO}: key {CHAVE_EQUIPAMENTOS!r} repeats the identidade {repetidas}"
        )
    return cadastros


def _licencas(dados: dict) -> tuple[Licenca, ...]:
    """The licences, each one a device of one of the two products."""
    valor = dados.get(CHAVE_LICENCAS, [])
    if not isinstance(valor, list):
        raise _erro_tipo(CHAVE_LICENCAS, "a list of objects", valor)
    licencas = []
    for indice, item in enumerate(valor):
        onde = f"{CHAVE_LICENCAS}[{indice}]"
        if not isinstance(item, dict):
            raise _erro_tipo(onde, "an object", item)
        licenca = Licenca(
            id=_texto_de(item, "id", onde),
            produto=_texto_de(item, "produto", onde),
            nome=_texto_de(item, "nome", onde),
            uuid=_texto_de(item, "uuid", onde),
            pid=_texto_de(item, "pid", onde),
            chave=_texto_de(item, "chave", onde),
        )
        if not ID_DE_LICENCA.fullmatch(licenca.id):
            raise ConfigIncompativel(f"{ARQUIVO}: key '{onde}.id' must be a short lowercase name")
        if licenca.produto not in mapa.PRODUTOS:
            raise ConfigIncompativel(
                f"{ARQUIVO}: key '{onde}.produto' must be one of {list(mapa.PRODUTOS)}, "
                f"found {licenca.produto!r}"
            )
        licencas.append(licenca)
    ids = [licenca.id for licenca in licencas]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ConfigIncompativel(f"{ARQUIVO}: key {CHAVE_LICENCAS!r} repeats the id {repetidos}")
    return tuple(licencas)


def _numeros(dados: dict) -> dict[str, tuple[str, ...]]:
    """The numbers of every licence: identities of registered equipment, empty for a free
    number, and the same identity never in two numbers of the installation.
    """
    valor = dados.get(CHAVE_NUMEROS, {})
    if not isinstance(valor, dict):
        raise _erro_tipo(CHAVE_NUMEROS, "an object of lists", valor)
    licencas = {licenca.id: licenca for licenca in _licencas(dados)}
    numeros: dict[str, tuple[str, ...]] = {}
    ocupadas: list[str] = []
    for chave, lista in valor.items():
        onde = f"{CHAVE_NUMEROS}.{chave}"
        if chave not in licencas:
            raise ConfigIncompativel(f"{ARQUIVO}: key {onde!r} names a licence that does not exist")
        if not isinstance(lista, list) or not all(isinstance(item, str) for item in lista):
            raise _erro_tipo(onde, "a list of strings", lista)
        teto = mapa.NUMEROS[licencas[chave].produto]
        if len(lista) > teto:
            raise ConfigIncompativel(
                f"{ARQUIVO}: key {onde!r} carries {len(lista)} numbers, the product has {teto}"
            )
        numeros[chave] = tuple(lista)
        ocupadas.extend(identidade for identidade in lista if identidade)
    repetidas = sorted({i for i in ocupadas if ocupadas.count(i) > 1})
    if repetidas:
        # One equipment in two numbers would answer two data points on the bus, and the
        # bridge would read a device that contradicts itself.
        raise ConfigIncompativel(
            f"{ARQUIVO}: key {CHAVE_NUMEROS!r} repeats the identidade {repetidas}"
        )
    return numeros


def _cenas(dados: dict, dir_data: Path) -> tuple[Cena, ...]:
    """The scenes as typed data, judged by the validation the panel route faces."""
    # The route that saves a scene is one door into this field and this file is the
    # other, the same as the ip of a registration; a step hand edited here to write a report
    # only data point would be run by the bus and publish a state no device ever confirmed.
    try:
        return validar_cenas(dados.get(CHAVE_CENAS, ()))
    except CenasInvalidas as erro:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key {CHAVE_CENAS!r} is not a list of scenes ({erro}). "
            f"{_conserto(dir_data)}"
        ) from erro


def _fuso(dados: dict) -> str:
    valor = _texto(dados, "fuso", PADRAO.fuso)
    if not agenda.fuso_valido(valor):
        raise ConfigIncompativel(f"{ARQUIVO}: key 'fuso' is not a time zone this box knows")
    return valor


def _agendamentos(dados: dict, dir_data: Path) -> tuple[agenda.Agendamento, ...]:
    try:
        return agenda.validar(dados.get(CHAVE_AGENDAMENTOS, []))
    except agenda.AgendamentosInvalidos as erro:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key {CHAVE_AGENDAMENTOS!r} is not a list of schedules ({erro}). "
            f"{_conserto(dir_data)}"
        ) from erro


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
        listas=_listas_de(item, onde),
        nivel_maximo=_nivel_maximo_de(item, onde),
    )
    for chave in ("identidade", "tipo"):
        if not getattr(cadastro, chave).strip():
            raise ConfigIncompativel(f"{ARQUIVO}: key '{onde}.{chave}' must be a non empty string")
    # The write routes take an IP literal and this file is the other door into
    # the same field; a hand edited hostname here would be dialled by the action route and turn
    # the hub into a proxy into the LAN. An empty ip is a registration whose address is not
    # known yet, which is a normal state.
    if cadastro.ip and ip_literal(cadastro.ip) is None:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key '{onde}.ip' must be an IP literal, found {cadastro.ip!r}. "
            f"{_conserto(dir_data)}"
        )
    return cadastro


def _listas_de(item: dict, onde: str) -> dict[str, tuple[Item, ...]]:
    """The lists of a registration, each a list of {rotulo, valor} within its ceiling."""
    bruto = item.get("listas", {})
    if not isinstance(bruto, dict):
        raise _erro_tipo(f"{onde}.listas", "an object of lists", bruto)
    listas: dict[str, tuple[Item, ...]] = {}
    for nome, entradas in bruto.items():
        campo = f"{onde}.listas.{nome}"
        if nome not in LISTAS:
            raise ConfigIncompativel(f"{ARQUIVO}: key {campo!r} is not one of {list(LISTAS)}")
        if not isinstance(entradas, list):
            raise _erro_tipo(campo, "a list of objects", entradas)
        if len(entradas) > LISTAS_MAXIMO[nome]:
            raise ConfigIncompativel(
                f"{ARQUIVO}: key {campo!r} carries {len(entradas)} items, "
                f"the ceiling is {LISTAS_MAXIMO[nome]}"
            )
        itens = []
        for indice, entrada in enumerate(entradas):
            if not isinstance(entrada, dict):
                raise _erro_tipo(f"{campo}[{indice}]", "an object", entrada)
            rotulo = _texto_de(entrada, "rotulo", f"{campo}[{indice}]")
            valor = _texto_de(entrada, "valor", f"{campo}[{indice}]")
            if not item_valido(rotulo, valor):
                raise ConfigIncompativel(
                    f"{ARQUIVO}: key '{campo}[{indice}]' must carry a label of 1 to "
                    f"{ROTULO_MAXIMO} printable characters and a value"
                )
            itens.append(Item(rotulo=rotulo, valor=valor))
        listas[nome] = tuple(itens)
    return listas


def _nivel_maximo_de(item: dict, onde: str) -> int:
    valor = item.get("nivel_maximo", NIVEL_MAXIMO)
    if type(valor) is not int or not 1 <= valor <= NIVEL_MAXIMO:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key '{onde}.nivel_maximo' must be an integer from 1 to {NIVEL_MAXIMO}"
        )
    return valor


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
    # JSON true passes isinstance(valor, int), and it is not an iteration count.
    if type(valor) is not int:
        raise _erro_tipo(chave, "an integer", valor)
    return valor
