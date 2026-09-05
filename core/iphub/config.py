# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Configuration of the installation: one file, read whole, written whole.

Configuração da instalação: um arquivo, lido inteiro, escrito inteiro.
"""

import ipaddress
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from iphub import arquivos
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

# The lists a registration of section 8 carries, their ceilings and the rule of one item live
# with the vocabulary of section 6, because a manifest suggests items for the same lists; they
# are re-exported here because the registration is what carries them.
# As listas que um cadastro da seção 8 carrega, os tetos delas e a regra de um item vivem com o
# vocabulário da seção 6, porque um manifesto sugere itens para as mesmas listas; são
# reexportados aqui porque o cadastro é quem as carrega.
LISTAS = manifesto.LISTAS
LISTAS_MAXIMO = manifesto.LISTAS_MAXIMO
ROTULO_MAXIMO = manifesto.ROTULO_MAXIMO
VALOR_DE_LISTA_MAXIMO = manifesto.VALOR_DE_LISTA_MAXIMO
item_valido = manifesto.item_valido

# Why: the id of a licence is a key of config.json and part of a route path, so it stays in
# the alphabet a JSON key and a URL segment share.
# Por que: o id de uma licença é chave do config.json e parte de um caminho de rota, então
# fica no alfabeto que uma chave JSON e um segmento de URL compartilham.
ID_DE_LICENCA = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")

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
    # Why: the inputs, the shortcuts and the modes of an equipment are pairs of a label the
    # customer reads and a value the driver takes, chosen by the integrator; the profile of
    # section 8 is built from them, so they live with the registration and nowhere else.
    # Por que: as entradas, os atalhos e os modos de um equipamento são pares de um rótulo que
    # o cliente lê e um valor que o driver recebe, escolhidos pelo integrador; o perfil da
    # seção 8 nasce deles, então eles vivem com o cadastro e em nenhum outro lugar.
    listas: dict[str, tuple["Item", ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    """One entry of a list of the registration: the label the app shows, the value the
    driver takes.

    Uma entrada de uma lista do cadastro: o rótulo que o app mostra, o valor que o driver
    recebe.
    """

    rotulo: str
    valor: str


@dataclass(frozen=True)
class Licenca:
    """One licence of section 8: a device on the platform, with the identity the bridge of
    that product uses. The chave never leaves the daemon.

    Uma licença da seção 8: um dispositivo na plataforma, com a identidade que a ponte
    daquele produto usa. A chave nunca sai do daemon.
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
    equipamentos: tuple[Cadastro, ...] = ()
    licencas: tuple[Licenca, ...] = ()
    # Why: section 8 makes the numbers of a licence an ORDER over identities already
    # registered as equipment, so there is no second registry: the position IS the number on
    # the app, and an empty string is a number nobody occupies. A removal empties the slot
    # instead of shifting the rest, because a shift would silently move an equipment from
    # number 2 to number 1 in every automation the customer already built on the platform.
    # Por que: a seção 8 faz dos números de uma licença uma ORDEM sobre identidades já
    # cadastradas como equipamento, então não existe segundo cadastro: a posição É o número no
    # app, e uma string vazia é um número que ninguém ocupa. Uma remoção esvazia a vaga em vez
    # de empurrar o resto, porque empurrar moveria em silêncio um equipamento do número 2 para
    # o número 1 em toda automação que o cliente já montou na plataforma.
    numeros: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Why: a scene is data of section 8 and the position of one is its number, the same way a
    # number of a licence is a position; the module that owns the format decides what a scene
    # is, and this file only says that the installation carries up to thirty two of them.
    # Por que: uma cena é dado da seção 8 e a posição de uma é o número dela, do mesmo jeito
    # que um número de licença é uma posição; o módulo dono do formato decide o que é uma
    # cena, e este arquivo só diz que a instalação carrega até trinta e duas delas.
    cenas: tuple[Cena, ...] = ()

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
        licencas=_licencas(dados),
        numeros=_numeros(dados),
        cenas=_cenas(dados, dir_data),
    )


def salvar(cfg: Config, dir_data: Path) -> None:
    arquivos.escrever_json(dir_data / ARQUIVO, {"schema_version": SCHEMA_VERSION, **asdict(cfg)})


def _conserto(dir_data: Path) -> str:
    return (
        f"Section 2.4 of DECISOES.md forbids migration code, so the fix is to erase the data "
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


def _licencas(dados: dict) -> tuple[Licenca, ...]:
    """The licences of section 8, each one a device of one of the two products.

    As licenças da seção 8, cada uma um dispositivo de um dos dois produtos.
    """
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

    Os números de cada licença: identidades de equipamento cadastrado, vazia para um número
    livre, e a mesma identidade nunca em dois números da instalação.
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
        # Why: one equipment in two numbers would answer two data points on the bus, and the
        # bridge would read a device that contradicts itself.
        # Por que: um equipamento em dois números responderia dois data points no barramento,
        # e a ponte leria um aparelho que se contradiz.
        raise ConfigIncompativel(
            f"{ARQUIVO}: key {CHAVE_NUMEROS!r} repeats the identidade {repetidas}"
        )
    return numeros


def _cenas(dados: dict, dir_data: Path) -> tuple[Cena, ...]:
    """The scenes as typed data, judged by the validation the panel route faces.

    As cenas como dado tipado, julgadas pela validação que a rota do painel enfrenta.
    """
    # Why: the route that saves a scene is one door into this field and this file is the
    # other, the same as the ip of a registration; a step hand edited here to write a report
    # only data point would be run by the bus and publish a state no device ever confirmed.
    # Por que: a rota que salva uma cena é uma porta para este campo e este arquivo é a outra,
    # igual ao ip de um cadastro; um passo editado na mão aqui para escrever um data point de
    # só report seria executado pelo barramento e publicaria estado que aparelho nenhum
    # confirmou.
    try:
        return validar_cenas(dados.get(CHAVE_CENAS, ()))
    except CenasInvalidas as erro:
        raise ConfigIncompativel(
            f"{ARQUIVO}: key {CHAVE_CENAS!r} is not a list of scenes ({erro}). "
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


def _listas_de(item: dict, onde: str) -> dict[str, tuple[Item, ...]]:
    """The lists of a registration, each a list of {rotulo, valor} within its ceiling.

    As listas de um cadastro, cada uma uma lista de {rotulo, valor} dentro do teto dela.
    """
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
