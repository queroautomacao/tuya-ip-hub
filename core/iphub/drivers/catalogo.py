# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 6 and 7: the catalog is walked, never listed by hand, and one bad file never
costs the boot.

The natives of the image are imported, and the declarative files are read from the embedded
catalog and from the drivers directory of the integrator, the second winning a conflict of
tipo. Two rules decide everything else. A JSON never shadows a native: code that ships in
the image is not replaceable by data. And no file ever raises out of here: a declaration
that does not validate is logged naming its fields and skipped, while every other driver
goes on loading, because on the bench one hand written file took a whole appliance down in
a restart loop.

Seções 6 e 7: o catálogo é varrido, nunca listado na mão, e um arquivo ruim nunca custa o
boot.

Os nativos da imagem são importados, e os arquivos declarativos são lidos do catálogo
embarcado e da pasta de drivers do integrador, o segundo vencendo conflito de tipo. Duas
regras decidem o resto. Um JSON nunca encobre um nativo: código que embarca na imagem não é
substituível por dado. E nenhum arquivo estoura daqui: uma declaração que não valida é
registrada nomeando os campos dela e pulada, enquanto todo outro driver segue carregando,
porque na bancada um arquivo escrito à mão derrubou um appliance inteiro em laço de
reinício.
"""

import functools
import importlib
import json
import logging
import pkgutil
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from iphub import regex_seguro
from iphub.arquivos import ler_texto
from iphub.drivers.base import Driver
from iphub.drivers.declarativo.formato import (
    CAMPO_ARQUIVO,
    DECL_DESCOBERTA_INVALIDA,
    DeclaracaoInvalida,
    Definicao,
    ProvaDeFogo,
)
from iphub.drivers.declarativo.formato import validar as validar_declaracao
from iphub.drivers.manifesto import ASSINATURAS, Manifesto, validar

log = logging.getLogger("iphub.drivers.catalogo")

PACOTE_NATIVOS = "iphub.drivers.nativos"

PASTA_EMBARCADA = Path(__file__).resolve().parent / "catalogo_json"
PASTA_INTEGRADOR = "drivers"
PADRAO_ARQUIVO = "*.json"

ORIGEM_IMAGEM = "imagem"
ORIGEM_INTEGRADOR = "integrador"
ORIGENS = (ORIGEM_IMAGEM, ORIGEM_INTEGRADOR)

# Why: a driver is a file somebody types, so the loader reads a page of text and not what a
# directory of a customer happens to hold; the size is checked before a byte is read.
# Por que: um driver é um arquivo que alguém digita, então o carregador lê uma página de
# texto e não o que a pasta de um cliente por acaso guardar; o tamanho é conferido antes de
# um byte ser lido.
ARQUIVO_MAXIMO = 64 * 1024

DECL_ARQUIVO_GRANDE = "decl_arquivo_grande"
DECL_JSON_INVALIDO = "decl_json_invalido"
DECL_INVALIDO = "decl_invalido"
DECL_TIPO_OCUPADO = "decl_tipo_ocupado"

CAMPO_TIPO = "manifesto.tipo"
CAMPO_DESCOBERTA = "descoberta"

# A name that gave the loader nothing to read, which None cannot say: a file holding null is
# readable, is JSON, and is refused by the validation with a code of its own.
# Um nome que não deu ao carregador nada para ler, o que o None não sabe dizer: um arquivo
# com null é legível, é JSON, e é recusado pela validação com um código próprio.
_ILEGIVEL = object()

type Fabrica = Callable[[Definicao], type[Driver]]


class CatalogoInvalido(ValueError):
    """Two drivers claim the same tipo, which is a test error and never a runtime choice.

    Dois drivers reivindicam o mesmo tipo, que é erro de teste e nunca escolha em runtime.
    """


@dataclass(frozen=True)
class Declarativo:
    """One JSON driver of the catalog: what it declares, where it came from, which file.

    Um driver JSON do catálogo: o que ele declara, de onde veio, qual arquivo.
    """

    definicao: Definicao
    classe: type[Driver]
    origem: str
    arquivo: Path

    @property
    def tipo(self) -> str:
        return self.definicao.manifesto.tipo


@dataclass(frozen=True)
class Recusado:
    """A file the loader refused, so the panel and the log say which field to fix.

    Um arquivo que o carregador recusou, para o painel e o log dizerem que campo consertar.
    """

    arquivo: Path
    origem: str
    problemas: tuple[tuple[str, str], ...]


class Catalogo:
    """The drivers of the image plus the JSON of the integrator, re read without a restart.

    Os drivers da imagem mais o JSON do integrador, relidos sem reiniciar.
    """

    def __init__(
        self,
        dir_data: Path | None = None,
        *,
        regex: ProvaDeFogo | None = None,
        fabrica: Fabrica | None = None,
        nativos: dict[str, type[Driver]] | None = None,
        pasta_embarcada: Path | None = PASTA_EMBARCADA,
    ) -> None:
        # Why: nativos and pasta_embarcada are what the IMAGE carries, and a test that names
        # the drivers of its hub has to be able to name all of them; walking the package and
        # loading the examples would hand that test drivers it never asked for.
        # Por que: nativos e pasta_embarcada são o que a IMAGEM carrega, e um teste que nomeia
        # os drivers do hub dele precisa poder nomear todos; varrer o pacote e carregar os
        # exemplos entregaria a esse teste drivers que ele nunca pediu.
        self._pasta_integrador = None if dir_data is None else Path(dir_data) / PASTA_INTEGRADOR
        self._pastas = tuple(
            (origem, pasta)
            for origem, pasta in (
                (ORIGEM_IMAGEM, None if pasta_embarcada is None else Path(pasta_embarcada)),
                (ORIGEM_INTEGRADOR, self._pasta_integrador),
            )
            if pasta is not None
        )
        self._regex = regex_seguro.instancia() if regex is None else regex
        self._fabrica = _do_motor if fabrica is None else fabrica
        self._nativos = (
            carregar_pacote(importlib.import_module(PACOTE_NATIVOS))
            if nativos is None
            else dict(nativos)
        )
        self._declarativos: dict[str, Declarativo] = {}
        self._recusados: tuple[Recusado, ...] = ()
        self._drivers: dict[str, type[Driver]] = {}
        self.recarregar()

    @property
    def drivers(self) -> dict[str, type[Driver]]:
        """What the gestor takes: the natives and the declarations that survived, read only.

        O que o gestor recebe: os nativos e as declarações que sobreviveram, só de leitura.
        """
        return self._drivers

    @property
    def nativos(self) -> dict[str, type[Driver]]:
        return self._nativos

    @property
    def declarativos(self) -> dict[str, Declarativo]:
        return self._declarativos

    @property
    def recusados(self) -> tuple[Recusado, ...]:
        return self._recusados

    @property
    def pasta_integrador(self) -> Path | None:
        """Where a file of the integrator lives, so no route repeats the layout of /data.

        Onde mora um arquivo do integrador, para nenhuma rota repetir o desenho do /data.
        """
        return self._pasta_integrador

    def recarregar(self) -> None:
        """Re reads both directories, so a driver saved in the panel is usable at once.

        Relê as duas pastas, para um driver salvo no painel servir na hora.
        """
        aceitos: dict[str, Declarativo] = {}
        recusados: list[Recusado] = []
        # The natives claim their signatures first: code that ships wins a dispute with data.
        # Os nativos reivindicam as assinaturas primeiro: código que embarca vence dado.
        assinaturas = _assinaturas_de(classe.MANIFESTO for classe in self._nativos.values())
        for origem, pasta in self._pastas:
            for arquivo in _arquivos_de(pasta):
                try:
                    self._acolher(arquivo, origem, aceitos, recusados, assinaturas)
                except Exception:
                    # Why: this is the promise of the module, and it is kept here rather than
                    # by naming exception types one at a time, because the file is written by
                    # hand and the next surprise it carries is one nobody listed. Exception and
                    # not BaseException: a cancellation still has to leave.
                    # Por que: esta é a promessa do módulo, e ela é mantida aqui em vez de por
                    # nomear tipos de exceção um a um, porque o arquivo é escrito à mão e a
                    # próxima surpresa que ele traz é uma que ninguém listou. Exception e não
                    # BaseException: um cancelamento ainda precisa sair.
                    log.exception("declarative driver %s could not be loaded", arquivo.name)
                    _recusar(recusados, arquivo, origem, ((CAMPO_ARQUIVO, DECL_INVALIDO),))
        self._declarativos = aceitos
        self._recusados = tuple(recusados)
        self._drivers = {**self._nativos, **{t: d.classe for t, d in aceitos.items()}}

    def _acolher(
        self,
        arquivo: Path,
        origem: str,
        aceitos: dict[str, Declarativo],
        recusados: list[Recusado],
        assinaturas: dict[str, str],
    ) -> None:
        definicao = self._ler(arquivo, origem, recusados)
        if definicao is None:
            return
        manifesto = definicao.manifesto
        tipo = manifesto.tipo
        if tipo in self._nativos:
            # Why: a JSON that took the tipo of a native driver would replace code that
            # shipped in the image with data, which rule 3 of section 2 forbids.
            # Por que: um JSON que tomasse o tipo de um driver nativo trocaria código que
            # embarcou na imagem por dado, o que a regra 3 da seção 2 proíbe.
            _recusar(recusados, arquivo, origem, ((CAMPO_TIPO, DECL_TIPO_OCUPADO),))
            return
        anterior = aceitos.get(tipo)
        if anterior is not None and anterior.origem == origem:
            # Why: two files of the same directory claiming one tipo have no winner to
            # choose, and choosing by the order a directory happens to list is a decision
            # taken in runtime; the first name in order keeps the tipo and the other is named.
            # Por que: dois arquivos da mesma pasta reivindicando um tipo não têm vencedor a
            # escolher, e escolher pela ordem em que a pasta lista é decisão tomada em
            # runtime; o primeiro nome na ordem fica com o tipo e o outro é nomeado.
            _recusar(recusados, arquivo, origem, ((CAMPO_TIPO, DECL_TIPO_OCUPADO),))
            return
        conflito = _conflito_de_assinatura(manifesto, assinaturas)
        if conflito is not None:
            # Why: section 6 builds the sweep from the manifests and refuses a signature two
            # types claim; a file of the integrator cannot be caught by a test, so it is
            # refused here instead of turning every discovery of the installation into a 500.
            # Por que: a seção 6 monta a varredura a partir dos manifestos e recusa uma
            # assinatura que dois tipos pedem; um arquivo do integrador não tem teste que o
            # pegue, então é recusado aqui em vez de transformar toda descoberta da
            # instalação num 500.
            log.error(
                "driver %s claims the discovery signature %r, already claimed by %s",
                tipo,
                conflito[0],
                conflito[1],
            )
            _recusar(recusados, arquivo, origem, ((CAMPO_DESCOBERTA, DECL_DESCOBERTA_INVALIDA),))
            return
        classe = self._construir(definicao, arquivo, origem, recusados)
        if classe is None:
            return
        if anterior is not None:
            log.info(
                "driver %s of the integrator replaces the one of the image (%s)",
                tipo,
                anterior.arquivo.name,
            )
            _liberar_assinaturas(anterior.definicao.manifesto, assinaturas)
        _tomar_assinaturas(manifesto, assinaturas)
        aceitos[tipo] = Declarativo(
            definicao=definicao, classe=classe, origem=origem, arquivo=arquivo
        )

    def _construir(
        self, definicao: Definicao, arquivo: Path, origem: str, recusados: list[Recusado]
    ) -> type[Driver] | None:
        try:
            return self._fabrica(definicao)
        except Exception:
            # Why: the engine builds the driver from a declaration the validation already
            # accepted, so a failure here is a defect of ours, and a defect of ours must
            # still not cost the boot of the installation.
            # Por que: o motor monta o driver de uma declaração que a validação já aceitou,
            # então uma falha aqui é defeito nosso, e defeito nosso ainda assim não pode
            # custar o boot da instalação.
            log.exception("driver %s could not be built by the engine", arquivo.name)
            _recusar(recusados, arquivo, origem, ((CAMPO_ARQUIVO, DECL_INVALIDO),))
            return None

    def _ler(self, arquivo: Path, origem: str, recusados: list[Recusado]) -> Definicao | None:
        try:
            # Why: a name that is not a regular file is refused without ever being opened. A
            # directory is what a bind mount of a missing path leaves behind, and reading a
            # fifo planted there would hang the boot instead of failing it.
            # Por que: um nome que não é arquivo comum é recusado sem nunca ser aberto. Uma
            # pasta é o que um bind mount de caminho ausente deixa, e ler um fifo plantado ali
            # penduraria o boot em vez de falhá-lo.
            regular = arquivo.is_file()
            grande = regular and arquivo.stat().st_size > ARQUIVO_MAXIMO
        except OSError:
            regular = False
            grande = False
        if not regular:
            _recusar(recusados, arquivo, origem, ((CAMPO_ARQUIVO, DECL_JSON_INVALIDO),))
            return None
        if grande:
            _recusar(recusados, arquivo, origem, ((CAMPO_ARQUIVO, DECL_ARQUIVO_GRANDE),))
            return None
        dados = _dados_de(arquivo)
        if dados is _ILEGIVEL:
            _recusar(recusados, arquivo, origem, ((CAMPO_ARQUIVO, DECL_JSON_INVALIDO),))
            return None
        try:
            return validar_declaracao(dados, regex=self._regex)
        except DeclaracaoInvalida as erro:
            _recusar(recusados, arquivo, origem, erro.problemas)
        except Exception:
            # Why: the validation promises to answer with problems and nothing else, and a
            # defect of ours in that promise is still not allowed to cost the boot.
            # Por que: a validação promete responder com problemas e nada mais, e um defeito
            # nosso nessa promessa ainda assim não pode custar o boot.
            log.exception("driver %s was not judged by the validation", arquivo.name)
            _recusar(recusados, arquivo, origem, ((CAMPO_ARQUIVO, DECL_INVALIDO),))
        return None


def _do_motor(definicao: Definicao) -> type[Driver]:
    """The engine of section 7 turns a declaration into a Driver of section 6.

    O motor da seção 7 transforma uma declaração num Driver da seção 6.
    """
    # Why: the loading, the validation and the reload are also what the API calls before a
    # file is saved, and none of that needs a socket; the engine is pulled in only when a
    # driver is actually built, which keeps a stub engine enough to test the loading rules.
    # Por que: a carga, a validação e a recarga são também o que a API chama antes de salvar
    # um arquivo, e nada disso precisa de socket; o motor só entra quando um driver é de fato
    # montado, o que deixa um motor de mentira bastar para testar as regras de carga.
    from iphub.drivers.declarativo.motor import construir

    return construir(definicao)


def _dados_de(arquivo: Path) -> object:
    """The file as JSON, or _ILEGIVEL when it is not readable text and not JSON at all.

    A null file is JSON and lands on the validation, which names it for what it is; the
    sentinel is only for a name that gave the loader nothing to look at.

    O arquivo como JSON, ou _ILEGIVEL quando ele não é texto legível nem JSON.

    Um arquivo nulo é JSON e cai na validação, que o nomeia pelo que ele é; a sentinela é só
    para um nome que não deu ao carregador nada para olhar.
    """
    try:
        # Why: ler_texto reads the name itself and never through a symlink, so a link planted
        # in the drivers directory does not turn the loader into a reader of any file the
        # container can open.
        # Por que: o ler_texto lê o próprio nome e nunca através de um link simbólico, então
        # um link plantado na pasta de drivers não transforma o carregador num leitor de
        # qualquer arquivo que o container consiga abrir.
        texto = ler_texto(arquivo)
        return _ILEGIVEL if texto is None else json.loads(texto)
    except Exception:
        # Why: json.loads answers RecursionError to a document nested deep enough, which a few
        # kilobytes of one bracket already are, so it never meets the size ceiling; naming the
        # exception types here is how the loader lost a whole appliance to one hand written
        # file. Exception and not BaseException: a cancellation still has to leave.
        # Por que: o json.loads responde RecursionError a um documento aninhado o bastante, que
        # alguns quilobytes de um colchete já são, então ele nunca encosta no teto de tamanho;
        # nomear os tipos de exceção aqui é como o carregador perdeu um appliance inteiro para
        # um arquivo escrito à mão. Exception e não BaseException: um cancelamento ainda sai.
        return _ILEGIVEL


def _arquivos_de(pasta: Path) -> list[Path]:
    """Every name of a directory that claims to be a driver, in a fixed order, and nothing at
    all when the directory is not there. What is not a file to read is refused by name, not
    passed over in silence, because an integrator who sees no driver and no log has nothing
    to fix.

    Todo nome de uma pasta que se diz driver, em ordem fixa, e nada quando a pasta não existe.
    O que não é arquivo de ler é recusado pelo nome, não pulado em silêncio, porque um
    integrador que não vê driver nem registro não tem o que consertar.
    """
    try:
        return sorted(pasta.glob(PADRAO_ARQUIVO))
    except OSError:
        log.warning("the drivers directory %s could not be listed", pasta)
        return []


def _recusar(
    recusados: list[Recusado], arquivo: Path, origem: str, problemas: tuple[tuple[str, str], ...]
) -> None:
    log.error(
        "declarative driver %s (%s) is invalid and was skipped: %s",
        arquivo.name,
        origem,
        "; ".join(f"{campo}: {codigo}" for campo, codigo in problemas),
    )
    recusados.append(Recusado(arquivo=arquivo, origem=origem, problemas=problemas))


def _assinaturas_de(manifestos: Iterable[Manifesto]) -> dict[str, str]:
    reivindicadas: dict[str, str] = {}
    for manifesto in manifestos:
        _tomar_assinaturas(manifesto, reivindicadas)
    return reivindicadas


def _cada_assinatura(manifesto: Manifesto) -> Iterator[str]:
    for nome in ASSINATURAS:
        for valor in getattr(manifesto.descoberta, nome):
            yield f"{nome}:{valor.strip().lower()}"


def _tomar_assinaturas(manifesto: Manifesto, reivindicadas: dict[str, str]) -> None:
    for assinatura in _cada_assinatura(manifesto):
        reivindicadas.setdefault(assinatura, manifesto.tipo)


def _liberar_assinaturas(manifesto: Manifesto, reivindicadas: dict[str, str]) -> None:
    for assinatura in _cada_assinatura(manifesto):
        if reivindicadas.get(assinatura) == manifesto.tipo:
            del reivindicadas[assinatura]


def _conflito_de_assinatura(
    manifesto: Manifesto, reivindicadas: dict[str, str]
) -> tuple[str, str] | None:
    for assinatura in _cada_assinatura(manifesto):
        dono = reivindicadas.get(assinatura)
        if dono is not None and dono != manifesto.tipo:
            return assinatura, dono
    return None


@functools.cache
def carregar() -> dict[str, type[Driver]]:
    """Every driver the IMAGE carries, built once per process: the natives and the embedded
    declarative catalog. The files of the integrator need the data directory, so they come
    from a Catalogo built with it.

    Todo driver que a IMAGEM carrega, montado uma vez por processo: os nativos e o catálogo
    declarativo embarcado. Os arquivos do integrador precisam do diretório de dados, então
    vêm de um Catalogo construído com ele.
    """
    return Catalogo().drivers


def esquecer() -> None:
    """Drops the cached catalog, for a test that swaps the package under it.

    Descarta o catálogo em cache, para um teste que troca o pacote por baixo dele.
    """
    carregar.cache_clear()


def carregar_pacote(pacote: ModuleType) -> dict[str, type[Driver]]:
    """Imports every module of the package and collects the Driver subclasses it declares.

    Importa todo módulo do pacote e recolhe as subclasses de Driver que ele declara.
    """
    catalogo: dict[str, type[Driver]] = {}
    origem: dict[str, str] = {}
    for modulo in _modulos(pacote):
        for classe in _drivers(modulo):
            tipo = _tipo_de(classe)
            anterior = catalogo.get(tipo)
            # Why: a module that imports a driver of another module to extend it exports the
            # same class object, and one class is one driver, not a duplicate tipo.
            # Por que: um módulo que importa um driver de outro módulo para estendê-lo exporta
            # o mesmo objeto de classe, e uma classe é um driver, não um tipo duplicado.
            if anterior is classe:
                continue
            if anterior is not None:
                raise CatalogoInvalido(
                    f"tipo {tipo!r} is claimed by {origem[tipo]}.{anterior.__name__} and by "
                    f"{modulo.__name__}.{classe.__name__}"
                )
            catalogo[tipo] = classe
            origem[tipo] = modulo.__name__
    return dict(sorted(catalogo.items()))


def _modulos(pacote: ModuleType) -> Iterator[ModuleType]:
    for info in pkgutil.iter_modules(pacote.__path__, f"{pacote.__name__}."):
        if info.ispkg or info.name.rpartition(".")[2].startswith("_"):
            continue
        yield importlib.import_module(info.name)


def _drivers(modulo: ModuleType) -> Iterator[type[Driver]]:
    for objeto in vars(modulo).values():
        # Why: the manifest has to be declared by the class itself; a subclass that only
        # inherits one is a variation of a driver, not a second entry in the catalog.
        # Por que: o manifesto tem de ser declarado pela própria classe; uma subclasse que só
        # herda um é variação de um driver, não uma segunda entrada no catálogo.
        if isinstance(objeto, type) and issubclass(objeto, Driver) and "MANIFESTO" in vars(objeto):
            yield objeto


def _tipo_de(classe: type[Driver]) -> str:
    manifesto = classe.MANIFESTO
    if not isinstance(manifesto, Manifesto):
        raise CatalogoInvalido(
            f"{classe.__module__}.{classe.__name__}.MANIFESTO must be a Manifesto, found "
            f"{type(manifesto).__name__}"
        )
    validar(manifesto)
    return manifesto.tipo
