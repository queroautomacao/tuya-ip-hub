# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7 under attack: what the hub loads from JSON, and what it refuses to load.

The rule that matters most is the one the bench proved: a broken file NEVER stops the boot.
Every test that plants a bad file asserts, in the same breath, that the good drivers loaded.

Seção 7 sob ataque: o que o hub carrega de JSON, e o que ele se recusa a carregar.

A regra que mais importa é a que a bancada provou: um arquivo quebrado NUNCA para o boot.
Todo teste que planta um arquivo ruim afirma, na mesma frase, que os drivers bons entraram.
"""

import json
import os
from pathlib import Path

import pytest

from iphub import regex_seguro
from iphub.drivers import catalogo, descoberta
from iphub.drivers.base import Driver
from iphub.drivers.declarativo import formato
from iphub.drivers.declarativo.formato import Definicao

TIPO_DE_TESTE = "matriz_de_teste"

# Why: overlapping alternation, which no heuristic catches and which the fire test does.
# Por que: alternância sobreposta, que heurística nenhuma pega e a prova de fogo pega.
REGEX_CATASTROFICA = r"(a|aa)+$"


class _SemFogo:
    """A fire test that accepts everything, so a loading test never spawns a process.

    Uma prova de fogo que aceita tudo, para um teste de carga nunca criar um processo.
    """

    def perigosa(self, padrao: str) -> bool:
        return False


def _fabrica_falsa(definicao: Definicao) -> type[Driver]:
    """A Driver carrying the manifest and nothing else: the loading rules need no transport.

    Um Driver que carrega o manifesto e nada mais: as regras de carga não precisam de
    transporte.
    """
    return type(
        "DriverDeclarativoDeTeste",
        (Driver,),
        {"MANIFESTO": definicao.manifesto, "DEFINICAO": definicao},
    )


def _declaracao(tipo: str = TIPO_DE_TESTE, **trocas: object) -> dict:
    """A file an integrator would write, minimal and valid, with the keys a test replaces.

    Um arquivo que um integrador escreveria, mínimo e válido, com as chaves que um teste troca.
    """
    dados: dict = {
        "manifesto": {
            "tipo": tipo,
            "rotulo": {"pt": "Matriz de teste", "en": "Test matrix"},
            "categoria": "matriz",
            "capacidades": ["ligar"],
        },
        "transporte": {"tcp": {"porta": 23}},
        "comandos": {"ligar": {"envia": "SET POWER ON"}},
    }
    for chave, valor in trocas.items():
        if chave == "manifesto" and isinstance(valor, dict):
            dados["manifesto"].update(valor)
        else:
            dados[chave] = valor
    return dados


def _gravar(pasta: Path, nome: str, dados: object) -> Path:
    arquivo = pasta / nome
    texto = dados if isinstance(dados, str) else json.dumps(dados, ensure_ascii=False)
    arquivo.write_text(texto, encoding="utf-8")
    return arquivo


@pytest.fixture
def dir_data(tmp_path: Path) -> Path:
    (tmp_path / catalogo.PASTA_INTEGRADOR).mkdir()
    return tmp_path


@pytest.fixture
def pasta(dir_data: Path) -> Path:
    return dir_data / catalogo.PASTA_INTEGRADOR


# Why: the image ships an empty embedded catalogue, so the tests of section 7 load the three
# examples of milestone 3 as if they were embedded, from the folder where they live.
# Por que: a imagem embarca um catálogo vazio, então os testes da seção 7 carregam os três
# exemplos do marco 3 como se embarcados, da pasta onde eles vivem.
EXEMPLOS = Path(__file__).resolve().parents[1] / "drivers" / "exemplos"


def _montar(dir_data: Path | None = None, *, regex: object = None) -> catalogo.Catalogo:
    return catalogo.Catalogo(
        dir_data,
        regex=_SemFogo() if regex is None else regex,
        fabrica=_fabrica_falsa,
        pasta_embarcada=EXEMPLOS,
    )


def _codigos(catalogo_montado: catalogo.Catalogo, nome: str) -> tuple[str, ...]:
    """The codes of the refusal of one file, so a test names the field and the code.

    Os códigos da recusa de um arquivo, para um teste nomear o campo e o código.
    """
    return tuple(
        codigo
        for recusado in catalogo_montado.recusados
        if recusado.arquivo.name == nome
        for _campo, codigo in recusado.problemas
    )


def _embarcados() -> dict[str, dict]:
    return {
        arquivo.name: json.loads(arquivo.read_text(encoding="utf-8"))
        for arquivo in sorted(EXEMPLOS.glob(catalogo.PADRAO_ARQUIVO))
    }


# ---------- the exit gate of milestone 3 ----------


def test_o_catalogo_embarcado_traz_um_exemplo_por_transporte():
    """Section 13: the example files, TCP, HTTP and UDP plus an air conditioner over TCP, and
    each one validates for real.

    Seção 13: os arquivos de exemplo, TCP, HTTP e UDP mais um ar condicionado por TCP, e cada
    um valida de verdade.
    """
    arquivos = _embarcados()
    assert len(arquivos) >= 4, "milestone 3 asks for the three examples and section 8 for the air"
    transportes = []
    for nome, dados in arquivos.items():
        # The real fire test, the same one the API runs before saving a file of the panel.
        # A prova de fogo real, a mesma que a API roda antes de salvar um arquivo do painel.
        definicao = formato.validar(dados, regex=regex_seguro.instancia())
        assert definicao.manifesto.motor == formato.MOTOR
        assert nome == f"{definicao.manifesto.tipo}.json"
        transportes.append(type(definicao.transporte).__name__.lower())
    assert sorted(transportes) == ["http", "tcp", "tcp", "udp"]


def test_o_catalogo_embarcado_nao_recusa_nada_e_entra_no_catalogo():
    montado = _montar()
    assert montado.recusados == ()
    for tipo, declarativo in montado.declarativos.items():
        assert declarativo.origem == catalogo.ORIGEM_IMAGEM
        assert montado.drivers[tipo] is declarativo.classe
        assert montado.drivers[tipo].MANIFESTO.motor == formato.MOTOR
    assert set(montado.nativos) <= set(montado.drivers)


def test_o_catalogo_embarcado_e_o_nativo_dividem_um_dicionario_so():
    montado = _montar()
    assert set(montado.drivers) == set(montado.nativos) | set(montado.declarativos)
    assert not set(montado.nativos) & set(montado.declarativos)


# ---------- precedence and shadowing ----------


def test_o_arquivo_do_integrador_vence_o_da_imagem(dir_data: Path, pasta: Path):
    da_imagem = next(iter(_montar().declarativos))
    _gravar(pasta, f"{da_imagem}.json", _declaracao(da_imagem, comandos={"ligar": {"envia": "X"}}))
    montado = _montar(dir_data)
    escolhido = montado.declarativos[da_imagem]
    assert escolhido.origem == catalogo.ORIGEM_INTEGRADOR
    assert escolhido.definicao.comandos["ligar"].passos[0].envia == "X"
    assert montado.recusados == ()


def test_um_json_nunca_encobre_um_driver_nativo(dir_data: Path, pasta: Path):
    """Rule 3 of section 2: nothing loads code in runtime, and data never replaces code.

    Regra 3 da seção 2: nada carrega código em runtime, e dado nunca substitui código.
    """
    nativo = next(iter(_montar().nativos))
    _gravar(pasta, f"{nativo}.json", _declaracao(nativo))
    montado = _montar(dir_data)
    assert montado.drivers[nativo] is montado.nativos[nativo]
    assert nativo not in montado.declarativos
    assert _codigos(montado, f"{nativo}.json") == (catalogo.DECL_TIPO_OCUPADO,)


def test_dois_arquivos_de_um_tipo_na_mesma_pasta_nao_decidem_em_runtime(
    dir_data: Path, pasta: Path
):
    _gravar(pasta, "a_primeiro.json", _declaracao(comandos={"ligar": {"envia": "PRIMEIRO"}}))
    _gravar(pasta, "b_segundo.json", _declaracao(comandos={"ligar": {"envia": "SEGUNDO"}}))
    montado = _montar(dir_data)
    escolhido = montado.declarativos[TIPO_DE_TESTE]
    assert escolhido.arquivo.name == "a_primeiro.json"
    assert escolhido.definicao.comandos["ligar"].passos[0].envia == "PRIMEIRO"
    assert _codigos(montado, "b_segundo.json") == (catalogo.DECL_TIPO_OCUPADO,)


# ---------- the rule that matters most: a broken file never stops the boot ----------


def test_arquivo_quebrado_nao_derruba_o_boot_e_o_resto_carrega(dir_data: Path, pasta: Path):
    """The bench rule: the daemon boots, names the file and the field, and keeps the rest.

    A regra da bancada: o daemon sobe, nomeia o arquivo e o campo, e mantém o resto.
    """
    _gravar(pasta, "nao_e_json.json", "{isto nao e json")
    _gravar(pasta, "nulo.json", "null")
    # The exact shape that took an appliance down in a restart loop: a le that is not an object.
    # A forma exata que derrubou um appliance em laço de reinício: um le que não é objeto.
    _gravar(
        pasta,
        "le_torto.json",
        _declaracao("matriz_torta", estado={"pede": [{"envia": "GET"}], "le": ["ligado"]}),
    )
    _gravar(pasta, f"{TIPO_DE_TESTE}.json", _declaracao())
    montado = _montar(dir_data)
    assert TIPO_DE_TESTE in montado.declarativos
    assert set(montado.nativos) <= set(montado.drivers)
    assert "matriz_torta" not in montado.drivers
    assert _codigos(montado, "nao_e_json.json") == (catalogo.DECL_JSON_INVALIDO,)
    assert _codigos(montado, "nulo.json") == (formato.DECL_NAO_OBJETO,)
    assert _codigos(montado, "le_torto.json") == (formato.DECL_LEITURA_INVALIDA,)


def _plantar_bomba(pasta: Path) -> None:
    # Why: a document nested deeper than the JSON reader goes answers RecursionError, which is
    # neither OSError nor ValueError, and 20 KB of one bracket is enough while staying well
    # under ARQUIVO_MAXIMO, so the size ceiling never sees it coming.
    # Por que: um documento aninhado mais fundo do que o leitor de JSON vai responde
    # RecursionError, que não é OSError nem ValueError, e 20 KB de um colchete bastam ficando
    # bem abaixo do ARQUIVO_MAXIMO, então o teto de tamanho nunca a vê chegar.
    _gravar(pasta, "ruim.json", "[" * 20_000)


def _plantar_idioma_de_rotulo_com_controle(pasta: Path) -> None:
    dados = _declaracao("matriz_torta")
    dados["manifesto"]["rotulo"] = {"p\x01t": "Matriz", "en": "Matrix"}
    _gravar(pasta, "ruim.json", dados)


def _plantar_idioma_de_textos_com_controle(pasta: Path) -> None:
    dados = _declaracao("matriz_torta")
    dados["manifesto"]["textos"] = {"p\x01t": {"descricao": "A"}, "en": {"descricao": "B"}}
    _gravar(pasta, "ruim.json", dados)


def _plantar_texto_solto(pasta: Path) -> None:
    _gravar(pasta, "ruim.json", "isto nao e json de jeito nenhum")


def _plantar_pasta(pasta: Path) -> None:
    # A bind mount of a path the host does not have leaves a directory with the name of a file.
    # Um bind mount de caminho que o hospedeiro não tem deixa uma pasta com nome de arquivo.
    (pasta / "ruim.json").mkdir()


QUEBRADOS = (
    ("bomba de aninhamento", _plantar_bomba, catalogo.DECL_JSON_INVALIDO),
    (
        "idioma do rotulo com caractere de controle",
        _plantar_idioma_de_rotulo_com_controle,
        formato.DECL_ROTULO_INVALIDO,
    ),
    (
        "idioma dos textos com caractere de controle",
        _plantar_idioma_de_textos_com_controle,
        formato.DECL_TEXTOS_INVALIDOS,
    ),
    ("texto que nao e json", _plantar_texto_solto, catalogo.DECL_JSON_INVALIDO),
    ("pasta no lugar do arquivo", _plantar_pasta, catalogo.DECL_JSON_INVALIDO),
)


@pytest.mark.parametrize(
    ("rotulo", "plantar", "codigo"), QUEBRADOS, ids=[caso[0] for caso in QUEBRADOS]
)
def test_nenhum_arquivo_escrito_a_mao_derruba_o_boot(
    rotulo, plantar, codigo, dir_data: Path, pasta: Path
):
    """The promise of the loader against the shapes that defeated it: the daemon boots, the
    offender is named with a code the panel translates, and the good driver is in the catalog.

    A promessa do carregador contra as formas que a derrotaram: o daemon sobe, o culpado é
    nomeado com um código que o painel traduz, e o driver bom está no catálogo.
    """
    plantar(pasta)
    _gravar(pasta, f"{TIPO_DE_TESTE}.json", _declaracao())
    montado = _montar(dir_data)
    assert _codigos(montado, "ruim.json") == (codigo,)
    assert TIPO_DE_TESTE in montado.declarativos
    assert set(montado.nativos) <= set(montado.drivers)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file that denies everyone")
def test_arquivo_ilegivel_e_recusado_e_o_resto_carrega(dir_data: Path, pasta: Path):
    _gravar(pasta, "ruim.json", _declaracao("matriz_torta")).chmod(0o000)
    _gravar(pasta, f"{TIPO_DE_TESTE}.json", _declaracao())
    montado = _montar(dir_data)
    assert _codigos(montado, "ruim.json") == (catalogo.DECL_JSON_INVALIDO,)
    assert TIPO_DE_TESTE in montado.declarativos


def test_nada_alem_de_uma_recusa_sai_da_validacao_de_um_arquivo(
    monkeypatch, dir_data: Path, pasta: Path
):
    """The rule is kept for the surprise nobody listed, not only for the ones already known.

    A regra vale para a surpresa que ninguém listou, não só para as que já se conhece.
    """
    original = catalogo.validar_declaracao

    def estourar(dados, *, regex):
        if isinstance(dados, dict) and dados.get("manifesto", {}).get("tipo") == "matriz_torta":
            raise RecursionError("the validation gave up on this file")
        return original(dados, regex=regex)

    monkeypatch.setattr(catalogo, "validar_declaracao", estourar)
    _gravar(pasta, "ruim.json", _declaracao("matriz_torta"))
    _gravar(pasta, f"{TIPO_DE_TESTE}.json", _declaracao())
    montado = _montar(dir_data)
    assert _codigos(montado, "ruim.json") == (catalogo.DECL_INVALIDO,)
    assert TIPO_DE_TESTE in montado.declarativos
    assert set(montado.nativos) <= set(montado.drivers)


def test_nem_o_que_vem_depois_da_validacao_derruba_o_boot(monkeypatch, dir_data: Path, pasta: Path):
    """Reading and validating are not the whole handling of a file, and the promise covers the
    handling; the seam is private because there is no other way in after the validation.

    Ler e validar não são o tratamento inteiro de um arquivo, e a promessa cobre o tratamento;
    a costura é privada porque não há outra entrada depois da validação.
    """
    original = catalogo._tomar_assinaturas

    def estourar(manifesto, reivindicadas):
        if manifesto.tipo == "matriz_torta":
            raise MemoryError("the loader gave up on this file")
        original(manifesto, reivindicadas)

    monkeypatch.setattr(catalogo, "_tomar_assinaturas", estourar)
    _gravar(pasta, "a_ruim.json", _declaracao("matriz_torta"))
    _gravar(pasta, f"z_{TIPO_DE_TESTE}.json", _declaracao())
    montado = _montar(dir_data)
    assert _codigos(montado, "a_ruim.json") == (catalogo.DECL_INVALIDO,)
    assert TIPO_DE_TESTE in montado.declarativos


def test_arquivo_quebrado_do_integrador_nao_derruba_o_da_imagem(dir_data: Path, pasta: Path):
    """A file of the integrator that fails to validate leaves the embedded driver in place.

    Um arquivo do integrador que não valida deixa o driver embarcado no lugar.
    """
    da_imagem = next(iter(_montar().declarativos))
    _gravar(pasta, f"{da_imagem}.json", _declaracao(da_imagem, transporte={"tcp": {"porta": 0}}))
    montado = _montar(dir_data)
    assert montado.declarativos[da_imagem].origem == catalogo.ORIGEM_IMAGEM
    assert _codigos(montado, f"{da_imagem}.json") == (formato.DECL_PORTA_INVALIDA,)


def test_arquivo_grande_e_recusado(dir_data: Path, pasta: Path):
    _gravar(pasta, "gordo.json", "x" * (catalogo.ARQUIVO_MAXIMO + 1))
    _gravar(pasta, f"{TIPO_DE_TESTE}.json", _declaracao())
    montado = _montar(dir_data)
    assert _codigos(montado, "gordo.json") == (catalogo.DECL_ARQUIVO_GRANDE,)
    assert TIPO_DE_TESTE in montado.declarativos


def test_link_simbolico_na_pasta_de_drivers_nao_e_lido(dir_data: Path, pasta: Path):
    """Section 9: the daemon reads the name itself, never what a link points at.

    Seção 9: o daemon lê o próprio nome, nunca o que um link aponta.
    """
    fora = _gravar(dir_data, "fora.txt", _declaracao("matriz_de_fora"))
    (pasta / "link.json").symlink_to(fora)
    montado = _montar(dir_data)
    assert "matriz_de_fora" not in montado.drivers
    assert _codigos(montado, "link.json") == (catalogo.DECL_JSON_INVALIDO,)


def test_pasta_de_drivers_ausente_nao_e_problema(tmp_path: Path):
    montado = _montar(tmp_path)
    assert montado.recusados == ()
    assert set(montado.nativos) <= set(montado.drivers)


def test_um_manifesto_fora_da_secao_6_e_recusado_pelo_campo(dir_data: Path, pasta: Path):
    _gravar(pasta, "capacidade.json", _declaracao(manifesto={"capacidades": ["voar"]}))
    montado = _montar(dir_data)
    assert TIPO_DE_TESTE not in montado.drivers
    assert _codigos(montado, "capacidade.json") == (formato.DECL_CAPACIDADE_DESCONHECIDA,)


# ---------- section 7 under attack ----------


def test_regex_catastrofica_nunca_entra_no_catalogo(dir_data: Path, pasta: Path):
    """Section 7: the fire test runs when the driver is loaded, not in the middle of a poll.

    Seção 7: a prova de fogo roda ao carregar o driver, não no meio de um poll.
    """
    _gravar(
        pasta,
        "explosiva.json",
        _declaracao(
            estado={
                "pede": [{"envia": "GET"}],
                "le": {"fonte": {"regex": REGEX_CATASTROFICA}},
            }
        ),
    )
    montado = _montar(dir_data, regex=regex_seguro.instancia())
    assert TIPO_DE_TESTE not in montado.drivers
    assert _codigos(montado, "explosiva.json") == (formato.DECL_REGEX_PERIGOSA,)


def test_base_que_nao_e_o_proprio_aparelho_e_recusada(dir_data: Path, pasta: Path):
    """Section 9 on the driver side: a file received ready made never aims elsewhere.

    Seção 9 do lado do driver: um arquivo recebido pronto nunca aponta para outro lugar.
    """
    _gravar(
        pasta,
        "vazamento.json",
        _declaracao(
            transporte={"http": {"base": "http://198.51.100.7"}},
            comandos={"ligar": {"envia": "/on"}},
        ),
    )
    montado = _montar(dir_data)
    assert TIPO_DE_TESTE not in montado.drivers
    assert _codigos(montado, "vazamento.json") == (formato.DECL_BASE_INVALIDA,)


# ---------- discovery, section 6 ----------


def test_a_descoberta_nasce_tambem_do_manifesto_declarativo(dir_data: Path, pasta: Path):
    _gravar(
        pasta,
        f"{TIPO_DE_TESTE}.json",
        _declaracao(descoberta={"ssdp_fabricantes": ["fabricante de teste"]}),
    )
    montado = _montar(dir_data)
    plano = descoberta.montar(classe.MANIFESTO for classe in montado.drivers.values())
    assert ("fabricante de teste", TIPO_DE_TESTE) in plano.fabricantes


def test_assinatura_ja_reivindicada_e_recusada_e_o_plano_nao_fica_ambiguo(
    dir_data: Path, pasta: Path
):
    """Section 6: two types claiming one signature is never a decision taken in runtime.

    Seção 6: dois tipos pedindo uma assinatura nunca é decisão tomada em runtime.
    """
    assinatura = {"ssdp_st": ["urn:teste:servico:1"]}
    _gravar(pasta, "a_um.json", _declaracao("matriz_um", descoberta=assinatura))
    _gravar(pasta, "b_dois.json", _declaracao("matriz_dois", descoberta=assinatura))
    montado = _montar(dir_data)
    assert "matriz_um" in montado.drivers
    assert "matriz_dois" not in montado.drivers
    assert _codigos(montado, "b_dois.json") == (formato.DECL_DESCOBERTA_INVALIDA,)
    plano = descoberta.montar(classe.MANIFESTO for classe in montado.drivers.values())
    assert plano.por_st["urn:teste:servico:1"] == "matriz_um"


# ---------- reload without a restart ----------


def test_recarregar_traz_o_arquivo_novo_e_esquece_o_apagado(dir_data: Path, pasta: Path):
    """Section 7: a driver saved in the panel is usable at once, with no restart.

    Seção 7: um driver salvo no painel serve na hora, sem reiniciar.
    """
    montado = _montar(dir_data)
    assert TIPO_DE_TESTE not in montado.drivers
    arquivo = _gravar(pasta, f"{TIPO_DE_TESTE}.json", _declaracao())
    montado.recarregar()
    assert montado.drivers[TIPO_DE_TESTE].MANIFESTO.tipo == TIPO_DE_TESTE
    arquivo.unlink()
    montado.recarregar()
    assert TIPO_DE_TESTE not in montado.drivers
    assert set(montado.nativos) <= set(montado.drivers)


def test_a_imagem_embarca_um_catalogo_vazio_e_os_exemplos_nunca_embarcam():
    """The list of types the panel offers is what controls a real device, so an invented
    protocol never ships; the examples live with the tests.

    A lista de tipos que o painel oferece é o que controla um aparelho de verdade, então um
    protocolo inventado nunca embarca; os exemplos vivem com os testes.
    """
    assert sorted(catalogo.PASTA_EMBARCADA.glob(catalogo.PADRAO_ARQUIVO)) == []
    assert catalogo.PASTA_EMBARCADA.is_dir()
    assert {arquivo.name for arquivo in EXEMPLOS.glob(catalogo.PADRAO_ARQUIVO)} == {
        "matriz_hdmi_ascii.json",
        "rele_http.json",
        "amplificador_udp.json",
        "ar_condicionado_tcp.json",
    }
