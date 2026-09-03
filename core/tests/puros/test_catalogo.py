# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6 under attack: nobody edits a list by hand and a broken manifest never ships.

Seção 6 sob ataque: ninguém edita lista na mão e um manifesto quebrado nunca embarca.
"""

import importlib
import pkgutil
import secrets
import sys
from pathlib import Path
from types import ModuleType

import pytest

from iphub.drivers import catalogo
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto, ManifestoInvalido, validar


def _fonte(tipo: str, classe: str = "Exemplo", categoria: str = "receiver") -> str:
    """A native driver module as a contributor would write it, minus the license header.

    Um módulo de driver nativo como um contribuidor escreveria, menos o cabeçalho de licença.
    """
    return (
        "from iphub.drivers.base import Driver\n"
        "from iphub.drivers.manifesto import Manifesto\n"
        "\n"
        "\n"
        f"class {classe}(Driver):\n"
        "    MANIFESTO = Manifesto(\n"
        f'        tipo="{tipo}",\n'
        '        rotulo=dict(pt="Exemplo", en="Example"),\n'
        f'        categoria="{categoria}",\n'
        '        capacidades=("ligar",),\n'
        '        textos=dict(pt=dict(descricao="Exemplo"), en=dict(descricao="Example")),\n'
        "    )\n"
    )


@pytest.fixture(autouse=True)
def sem_cache():
    """The catalog is cached per process, and a test must never inherit another one.

    O catálogo tem cache por processo, e um teste nunca pode herdar o de outro.
    """
    catalogo.esquecer()
    yield
    catalogo.esquecer()


@pytest.fixture
def fabrica_pacote(tmp_path: Path, monkeypatch):
    """Builds a throwaway package of driver modules and imports it, then forgets it.

    Constrói um pacote descartável de módulos de driver e o importa, depois o esquece.
    """
    monkeypatch.syspath_prepend(str(tmp_path))
    criados: list[str] = []

    def criar(**modulos: str) -> ModuleType:
        nome = f"drivers_de_teste_{secrets.token_hex(4)}"
        pasta = tmp_path / nome
        pasta.mkdir()
        (pasta / "__init__.py").write_text("", encoding="utf-8")
        for arquivo, fonte in modulos.items():
            (pasta / f"{arquivo}.py").write_text(fonte, encoding="utf-8")
        importlib.invalidate_caches()
        criados.append(nome)
        return importlib.import_module(nome)

    yield criar
    for raiz in criados:
        for chave in [c for c in sys.modules if c == raiz or c.startswith(f"{raiz}.")]:
            del sys.modules[chave]


def test_o_catalogo_e_varrido_e_chaveado_por_tipo(fabrica_pacote):
    pacote = fabrica_pacote(
        um=_fonte("receiver_um", classe="Um"),
        dois=_fonte("caixa_dois", classe="Dois", categoria="multiroom"),
    )
    encontrado = catalogo.carregar_pacote(pacote)
    assert sorted(encontrado) == ["caixa_dois", "receiver_um"]
    assert all(issubclass(classe, Driver) for classe in encontrado.values())
    assert encontrado["receiver_um"].MANIFESTO.tipo == "receiver_um"


def test_dois_modulos_com_o_mesmo_tipo_e_erro_de_teste(fabrica_pacote):
    """Section 6: an ambiguity is caught in the suite, never resolved at runtime.

    Seção 6: uma ambiguidade é pega na suite, nunca resolvida em runtime.
    """
    pacote = fabrica_pacote(
        um=_fonte("receiver_repetido", classe="Um"),
        dois=_fonte("receiver_repetido", classe="Dois"),
    )
    with pytest.raises(catalogo.CatalogoInvalido) as erro:
        catalogo.carregar_pacote(pacote)
    assert "receiver_repetido" in str(erro.value)
    assert "Um" in str(erro.value) and "Dois" in str(erro.value)


def test_manifesto_quebrado_nao_embarca(fabrica_pacote):
    pacote = fabrica_pacote(torto=_fonte("Receiver Errado"))
    with pytest.raises(ManifestoInvalido):
        catalogo.carregar_pacote(pacote)


def test_manifesto_que_nao_e_manifesto_nao_embarca(fabrica_pacote):
    fonte = (
        "from iphub.drivers.base import Driver\n"
        "\n"
        "\n"
        "class Mentiroso(Driver):\n"
        '    MANIFESTO = {"tipo": "receiver_dict"}\n'
    )
    pacote = fabrica_pacote(mentiroso=fonte)
    with pytest.raises(catalogo.CatalogoInvalido):
        catalogo.carregar_pacote(pacote)


def test_o_que_nao_declara_manifesto_fica_de_fora(fabrica_pacote):
    """A helper class and a subclass that only inherits are not entries of the catalog.

    Uma classe auxiliar e uma subclasse que só herda não são entradas do catálogo.
    """
    fonte = _fonte("receiver_base", classe="Base") + (
        "\n"
        "\n"
        "class SemManifesto(Driver):\n"
        "    pass\n"
        "\n"
        "\n"
        "class Herdeiro(Base):\n"
        "    pass\n"
        "\n"
        "\n"
        "class NaoEDriver:\n"
        '    MANIFESTO = "nada"\n'
    )
    pacote = fabrica_pacote(unico=fonte)
    assert list(catalogo.carregar_pacote(pacote)) == ["receiver_base"]


def test_a_mesma_classe_reexportada_nao_e_duplicata(fabrica_pacote):
    pacote = fabrica_pacote(
        um=_fonte("receiver_unico", classe="Um"),
        dois='from .um import Um\n\n__all__ = ["Um"]\n',
    )
    assert list(catalogo.carregar_pacote(pacote)) == ["receiver_unico"]


def test_modulo_privado_nao_e_varrido(fabrica_pacote):
    pacote = fabrica_pacote(
        _comum=_fonte("receiver_privado", classe="Privado"),
        publico=_fonte("receiver_publico", classe="Publico"),
    )
    assert list(catalogo.carregar_pacote(pacote)) == ["receiver_publico"]


def test_pacote_vazio_da_catalogo_vazio(fabrica_pacote):
    assert catalogo.carregar_pacote(fabrica_pacote()) == {}


def test_carregar_usa_cache_e_esquecer_o_limpa():
    primeiro = catalogo.carregar()
    assert catalogo.carregar() is primeiro
    catalogo.esquecer()
    assert catalogo.carregar() is not primeiro


def test_o_catalogo_real_so_traz_manifesto_valido():
    for tipo, classe in catalogo.carregar().items():
        assert isinstance(classe.MANIFESTO, Manifesto)
        assert classe.MANIFESTO.tipo == tipo
        assert validar(classe.MANIFESTO) is None


def _nativos_povoado() -> bool:
    nativos = importlib.import_module(catalogo.PACOTE_NATIVOS)
    return any(not info.name.startswith("_") for info in pkgutil.iter_modules(nativos.__path__))


@pytest.mark.skipif(
    not _nativos_povoado(),
    reason="the first native driver of milestone 2 has not landed in drivers/nativos yet",
)
def test_o_catalogo_real_traz_ao_menos_um_driver():
    assert catalogo.carregar(), "drivers/nativos holds a module and the catalog found nothing"
