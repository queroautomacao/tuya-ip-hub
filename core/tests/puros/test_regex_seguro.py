# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7 under attack: the pattern that freezes a daemon is fed to the reader itself.

Every test here is the defect it defends against: a catastrophic pattern arriving in a
driver, a pattern the compiler refuses, a device answering a megabyte, and a worker killed
by a deadline having to leave every other device readable.

Seção 7 sob ataque: o padrão que congela um daemon é dado de comer ao próprio leitor.

Todo teste aqui é o defeito de que ele defende: um padrão catastrófico chegando num
driver, um padrão que o compilador recusa, um aparelho respondendo um megabyte, e um
trabalhador morto por um prazo tendo que deixar todo outro aparelho legível.
"""

import asyncio
import contextlib
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from iphub import regex_seguro
from iphub.regex_seguro import (
    ESTOUROS_ATE_QUARENTENA,
    MAX_TEXTO,
    PROVA_DE_FOGO,
    QUARENTENA_S,
    RegexSeguro,
    compilavel,
    fechar_instancia,
    instancia,
    instancia_validacao,
)

# The classic: exponential backtracking as soon as the anchor fails at the end.
# O clássico: retrocesso exponencial assim que a âncora falha no fim.
CATASTROFICO = r"(a+)+$"
ALTERNANCIA_SOBREPOSTA = r"(a|aa)+$"
QUANTIFICADOR_VAZIO = r"(a*)*$"
BOM = r"PWR (ON|OFF)"

# Why: the reader stops asking a pattern that keeps blowing the deadline, so a test that needs
# a real deadline paid brings a pattern of its own instead of borrowing one already spent by
# the test above it on the worker this file shares.
# Por que: o leitor para de perguntar um padrão que insiste em estourar o prazo, então um teste
# que precisa de um prazo de verdade traz um padrão próprio em vez de emprestar um já gasto
# pelo teste acima dele no trabalhador que este arquivo compartilha.

# A deadline that a killed worker plus a respawn still fits in, on a loaded machine.
# Um prazo em que um trabalhador morto mais um renascimento ainda cabem, numa máquina cheia.
TETO_DA_ESPERA_S = 5.0


@pytest.fixture(scope="module")
def regex():
    """One worker for the file: each spawn costs a fresh interpreter, and the point of the
    module is exactly that the worker survives, or is born again, between reads.

    Um trabalhador para o arquivo: cada spawn custa um interpretador novo, e o ponto do
    módulo é justamente o trabalhador sobreviver, ou renascer, entre leituras.
    """
    leitor = RegexSeguro()
    yield leitor
    leitor.fechar()


def test_padrao_normal_devolve_os_grupos(regex):
    assert regex.buscar(BOM, "resposta: PWR ON") == ["ON"]
    assert regex.buscar(r"SRC (\d)", "SRC 3") == ["3"]


def test_grupo_declarado_que_nao_casou_volta_none_no_lugar_dele(regex):
    assert regex.buscar(r"(A)?(B)", "B") == [None, "B"]


def test_padrao_que_nao_casa_volta_lista_vazia(regex):
    assert regex.buscar(BOM, "PWR STANDBY") == []


def test_padrao_sem_grupo_de_captura_nao_estoura(regex):
    # Why: m.group(1) on a pattern with no group raised IndexError, the poll died and the
    # device read offline forever while it was powered on.
    # Por que: m.group(1) num padrão sem grupo estourava IndexError, o poll morria e o
    # aparelho lia offline para sempre estando ligado.
    assert regex.buscar(r"PWR ON", "PWR ON") == []


@pytest.mark.parametrize("padrao", [CATASTROFICO, ALTERNANCIA_SOBREPOSTA])
def test_padrao_catastrofico_estoura_o_prazo_e_a_prova_de_fogo_o_recusa(regex, padrao):
    inicio = time.monotonic()
    assert regex.buscar(padrao, PROVA_DE_FOGO) is None
    gasto = time.monotonic() - inicio
    assert gasto >= regex.prazo_s
    # Why: without the kill this read takes minutes and the whole daemon is frozen with it,
    # so the number that matters is the ceiling, not the match.
    # Por que: sem a morte esta leitura leva minutos e o daemon inteiro congela junto, então
    # o número que importa é o teto, não o casamento.
    assert gasto < TETO_DA_ESPERA_S
    assert regex.perigosa(padrao) is True


def test_padrao_bom_nao_e_chamado_de_perigoso(regex):
    # Why: a false positive here refuses a driver that works, which is the failure the
    # heuristic used to produce and the reason the fire test replaced it.
    # Por que: um falso positivo aqui recusa um driver que funciona, que é a falha que a
    # heurística produzia e a razão de a prova de fogo tê-la substituído.
    for padrao in (BOM, r"SRC (\d)", r"volume=(\d+)", r"^(\w+)\s+(\w+)$"):
        assert regex.perigosa(padrao) is False


def test_padrao_que_nao_compila_e_recusado_sem_ser_chamado_de_perigoso(regex):
    assert compilavel("(") is False
    assert compilavel(r"(a)") is True
    assert compilavel(5) is False
    assert regex.perigosa("(") is False
    assert regex.buscar("(", "qualquer coisa") is None


def test_leitura_seguinte_funciona_depois_de_o_prazo_matar_o_trabalhador(regex):
    assert regex.buscar(BOM, "PWR ON") == ["ON"]
    antes = regex._proc.pid
    assert regex.buscar(CATASTROFICO, PROVA_DE_FOGO) is None
    # Why: one bad pattern in one driver must not leave every other device unreadable.
    # Por que: um padrão ruim num driver não pode deixar todo outro aparelho ilegível.
    assert regex.buscar(BOM, "PWR OFF") == ["OFF"]
    assert regex._proc.pid != antes


def test_trabalhador_morto_por_fora_e_recriado_na_leitura_seguinte(regex):
    assert regex.buscar(BOM, "PWR ON") == ["ON"]
    regex._proc.kill()
    regex._proc.join(2)
    assert regex.buscar(BOM, "PWR ON") == ["ON"]


def test_texto_acima_do_teto_e_truncado_em_vez_de_ir_inteiro(regex):
    marca = "MARCA"
    assert regex.buscar(f"({marca})", "x" * (MAX_TEXTO - len(marca)) + marca) == [marca]
    assert regex.buscar(f"({marca})", "x" * MAX_TEXTO + marca) == []


def test_padrao_ou_texto_que_nao_e_texto_volta_none(regex):
    assert regex.buscar(None, "PWR ON") is None
    assert regex.buscar(BOM, 42) is None
    assert regex.buscar(b"PWR", "PWR ON") is None


def test_grupo_com_a_palavra_erro_nao_e_lido_como_falha(regex):
    # Why: the worker reports a failure as a tuple and a match as a list, so a device that
    # answers the word erro reads as a match, never as a dead worker.
    # Por que: o trabalhador relata falha como tupla e casamento como lista, então um
    # aparelho que responde a palavra erro lê como casamento, nunca como trabalhador morto.
    assert regex.buscar(r"(erro)", "o aparelho disse erro") == ["erro"]


def test_buscar_em_paralelo_nao_troca_as_respostas(regex):
    marcas = [f"M{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=4) as piscina:
        respostas = list(piscina.map(lambda m: regex.buscar(f"({m})", f"eco {m} fim"), marcas))
    assert respostas == [[m] for m in marcas]


async def test_buscar_async_nao_segura_o_laco_de_eventos(regex):
    passos = 0

    async def tiquetaque():
        nonlocal passos
        while True:
            await asyncio.sleep(0.005)
            passos += 1

    relogio = asyncio.create_task(tiquetaque())
    resultado = await regex.buscar_async(QUANTIFICADOR_VAZIO, PROVA_DE_FOGO)
    relogio.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await relogio
    assert resultado is None
    # Why: a deadline spent on the loop thread is a panel and an API frozen for a quarter of
    # a second per read, which is the freeze this module exists to avoid, only smaller.
    # Por que: um prazo gasto na thread do laço é painel e API congelados por um quarto de
    # segundo por leitura, que é o congelamento que este módulo existe para evitar, só menor.
    assert passos >= 5
    assert await regex.buscar_async(BOM, "PWR ON") == ["ON"]


def test_fechar_encerra_o_trabalhador_e_a_busca_seguinte_o_reabre():
    leitor = RegexSeguro()
    try:
        assert leitor.buscar(BOM, "PWR ON") == ["ON"]
        processo = leitor._proc
        leitor.fechar()
        assert leitor._proc is None
        assert not processo.is_alive()
        # Why: a shutdown that always ends in a kill hides the day the worker stops reading
        # its own pipe, which is the state a deadline can no longer be told apart from.
        # Por que: um desligamento que sempre acaba em morte esconde o dia em que o
        # trabalhador para de ler o próprio pipe, estado que um prazo não distingue mais.
        assert processo.exitcode == 0
        assert leitor.buscar(BOM, "PWR ON") == ["ON"]
    finally:
        leitor.fechar()


def test_fechar_duas_vezes_nao_estoura():
    leitor = RegexSeguro()
    leitor.fechar()
    leitor.fechar()
    assert leitor._proc is None


def test_instancia_e_uma_so_no_processo():
    try:
        assert instancia() is instancia()
        anterior = instancia()
        fechar_instancia()
        assert regex_seguro._instancia is None
        assert instancia() is not anterior
    finally:
        fechar_instancia()


def test_trabalhador_que_nao_arranca_nao_trava_quem_chama():
    # Why: a board that cannot spawn a process must answer a failed read, not hang the poll
    # of every declarative device behind a start that never happens.
    # Por que: uma placa que não consegue criar processo precisa responder leitura falha, e
    # não pendurar o poll de todo aparelho declarativo atrás de um arranque que não vem.
    leitor = RegexSeguro(arranque_s=0.0)
    try:
        inicio = time.monotonic()
        assert leitor.buscar(BOM, "PWR ON") is None
        assert time.monotonic() - inicio < TETO_DA_ESPERA_S
    finally:
        leitor.fechar()
    assert leitor.buscar(BOM, "PWR ON") is None


class Espiao(RegexSeguro):
    """Counts the interpreters the reads cost, which is the whole point of the quarantine.

    Conta os interpretadores que as leituras custaram, que é o ponto inteiro da quarentena.
    """

    def __init__(self, **pecas: object) -> None:
        super().__init__(**pecas)  # type: ignore[arg-type]
        self.partidas = 0

    def _garantir(self) -> bool:
        vivo = self._proc is not None and self._proc.is_alive()
        pronto = super()._garantir()
        if pronto and not vivo:
            self.partidas += 1
        return pronto


class Cronometro:
    """A clock the test moves by hand, so a quarantine of minutes fits in a test.

    Um relógio que o teste move na mão, para uma quarentena de minutos caber num teste.
    """

    def __init__(self) -> None:
        self.agora = 0.0

    def __call__(self) -> float:
        return self.agora


def test_um_padrao_lento_nao_paga_um_interpretador_novo_por_leitura():
    """A pattern that survives the fire test and is slow against what the device sends killed
    the worker on every read, and the next read paid the whole process startup again: on an
    ARM board that is the poll of every declarative device, every ten seconds.

    Um padrão que passa na prova de fogo e é lento contra o que o aparelho manda matava o
    trabalhador a cada leitura, e a leitura seguinte pagava o arranque inteiro do processo de
    novo: numa placa ARM isso é o poll de todo aparelho declarativo, a cada dez segundos.
    """
    espiao = Espiao(prazo_s=0.05)
    try:
        for _ in range(ESTOUROS_ATE_QUARENTENA + 4):
            assert espiao.buscar(CATASTROFICO, PROVA_DE_FOGO) is None
        assert espiao.partidas <= ESTOUROS_ATE_QUARENTENA
    finally:
        espiao.fechar()


def test_a_quarentena_de_um_padrao_nao_cala_a_leitura_dos_outros():
    """An unread field is one field, never the state of the device: the other readings of the
    same poll still arrive and the equipment stays online.

    Um campo não lido é um campo, nunca o estado do aparelho: as outras leituras do mesmo poll
    seguem chegando e o equipamento continua online.
    """
    leitor = RegexSeguro(prazo_s=0.05)
    try:
        for _ in range(ESTOUROS_ATE_QUARENTENA + 1):
            assert leitor.buscar(CATASTROFICO, PROVA_DE_FOGO) is None
        assert leitor.buscar(BOM, "PWR ON") == ["ON"]
        assert leitor.buscar(r"SRC (\d)", "SRC 3") == ["3"]
    finally:
        leitor.fechar()


def test_a_quarentena_expira_e_o_padrao_volta_a_ser_perguntado():
    """A device that started answering something shorter is read again without a restart.

    Um aparelho que passou a responder algo mais curto é lido de novo sem reiniciar nada.
    """
    relogio = Cronometro()
    espiao = Espiao(prazo_s=0.05, relogio=relogio)
    try:
        for _ in range(ESTOUROS_ATE_QUARENTENA):
            assert espiao.buscar(CATASTROFICO, PROVA_DE_FOGO) is None
        gastas = espiao.partidas
        assert espiao.buscar(CATASTROFICO, PROVA_DE_FOGO) is None
        assert espiao.partidas == gastas
        relogio.agora += QUARENTENA_S + 1
        assert espiao.buscar(CATASTROFICO, PROVA_DE_FOGO) is None
        assert espiao.partidas == gastas + 1
    finally:
        espiao.fechar()


def test_uma_leitura_que_responde_zera_a_contagem_do_padrao():
    """Deadlines in a ROW are what says a pattern cannot be read here; a device that answered
    a long line once is a hiccup, and a hiccup must not silence a pattern that works.

    Prazos SEGUIDOS são o que diz que um padrão não se lê aqui; um aparelho que respondeu uma
    linha longa uma vez é soluço, e soluço não pode calar um padrão que funciona.
    """
    lento = r"(x+x+)+$"
    leitor = RegexSeguro(prazo_s=0.05)
    try:
        for _ in range(ESTOUROS_ATE_QUARENTENA - 1):
            assert leitor.buscar(lento, "x" * 40 + "!") is None
        assert leitor.buscar(lento, "xxx") == ["xxx"]
        for _ in range(ESTOUROS_ATE_QUARENTENA - 1):
            assert leitor.buscar(lento, "x" * 40 + "!") is None
        assert leitor.buscar(lento, "xxx") == ["xxx"]
    finally:
        leitor.fechar()


def test_a_prova_de_fogo_nunca_e_calada_pela_quarentena():
    """Section 7: every regex passes the fire test when the driver is saved. A pattern the
    reads put in quarantine would be judged without being tried, and the answer would be the
    quarantine talking, not the pattern.

    Seção 7: toda regex passa pela prova de fogo ao salvar o driver. Um padrão que as leituras
    puseram em quarentena seria julgado sem ser tentado, e a resposta seria a quarentena
    falando, não o padrão.
    """
    leitor = RegexSeguro(prazo_s=0.05)
    try:
        for _ in range(ESTOUROS_ATE_QUARENTENA + 1):
            assert leitor.buscar(BOM, PROVA_DE_FOGO) == []
        # Why: a good pattern in quarantine would be called dangerous and refuse a driver that
        # works, and a bad one would be called safe if the quarantine answered for it.
        # Por que: um padrão bom em quarentena seria chamado de perigoso e recusaria um driver
        # que funciona, e um ruim seria chamado de são se a quarentena respondesse por ele.
        for _ in range(ESTOUROS_ATE_QUARENTENA + 1):
            assert leitor.perigosa(ALTERNANCIA_SOBREPOSTA) is True
            assert leitor.perigosa(BOM) is False
    finally:
        leitor.fechar()


def test_o_trabalhador_da_validacao_nao_e_o_das_leituras():
    """A file being typed kills a worker per catastrophic pattern, and paying that on the
    worker the polls read through is the panel spending the poll of every device.

    Um arquivo sendo digitado mata um trabalhador por padrão catastrófico, e pagar isso no
    trabalhador por onde os polls leem é o painel gastando o poll de todo aparelho.
    """
    try:
        assert instancia_validacao() is instancia_validacao()
        assert instancia_validacao() is not instancia()
        fechar_instancia()
        assert regex_seguro._instancia is None
        assert regex_seguro._validacao is None
    finally:
        fechar_instancia()
