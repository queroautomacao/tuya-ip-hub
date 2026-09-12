# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Session token kept by hash in a 0600 file, 24 h renewed, 30 day cap."""

import errno
import json
import logging
from pathlib import Path

import pytest

from iphub import sessoes as modulo
from iphub.sessoes import ARQUIVO, PERSISTIR_APOS_S, TETO_S, VALIDADE_S, Sessoes, impressao
from iphub.versao import SCHEMA_VERSION

PASSO = VALIDADE_S // 2


class Relogio:
    def __init__(self, agora: float = 1_000_000.0) -> None:
        self.agora = agora

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


@pytest.fixture
def relogio() -> Relogio:
    return Relogio()


@pytest.fixture
def arquivo(tmp_path: Path) -> Path:
    return tmp_path / ARQUIVO


@pytest.fixture
def sessoes(tmp_path: Path, relogio: Relogio) -> Sessoes:
    return Sessoes(tmp_path, agora=relogio)


def test_token_criado_vale(sessoes):
    token, expira_em_s = sessoes.criar()
    assert expira_em_s == VALIDADE_S
    assert sessoes.validar(token)
    assert sessoes.quantidade() == 1


@pytest.mark.parametrize("token", [None, "", "token-inventado", "0" * 64])
def test_token_desconhecido_nao_vale(sessoes, token):
    sessoes.criar()
    assert not sessoes.validar(token)


def test_ociosidade_alem_da_validade_mata_a_sessao(sessoes, relogio):
    token, _ = sessoes.criar()
    relogio.avancar(VALIDADE_S + 1)
    assert not sessoes.validar(token)
    assert sessoes.quantidade() == 0


def test_uso_regular_mantem_a_sessao_viva(sessoes, relogio):
    token, _ = sessoes.criar()
    for _ in range(4):
        relogio.avancar(VALIDADE_S - 1)
        assert sessoes.validar(token)


def test_o_teto_absoluto_mata_a_sessao_mesmo_em_uso(sessoes, relogio):
    token, _ = sessoes.criar()
    for _ in range(TETO_S // PASSO):
        relogio.avancar(PASSO)
        assert sessoes.validar(token)
    relogio.avancar(1)
    assert not sessoes.validar(token)


def test_expira_em_s_encolhe_perto_do_teto(sessoes, relogio):
    token, _ = sessoes.criar()
    for _ in range(TETO_S // PASSO - 1):
        relogio.avancar(PASSO)
        assert sessoes.validar(token)
    relogio.avancar(PASSO - 60)
    assert sessoes.validar(token)
    assert sessoes.expira_em_s(token) == 60


def test_revogar_derruba_so_a_sessao_indicada(sessoes):
    primeiro, _ = sessoes.criar()
    segundo, _ = sessoes.criar()
    sessoes.revogar(primeiro)
    assert not sessoes.validar(primeiro)
    assert sessoes.validar(segundo)


def test_revogar_todas_derruba_tudo(sessoes):
    primeiro, _ = sessoes.criar()
    segundo, _ = sessoes.criar()
    sessoes.revogar_todas()
    assert not sessoes.validar(primeiro)
    assert not sessoes.validar(segundo)
    assert sessoes.quantidade() == 0


def test_revogar_token_desconhecido_nao_derruba_nada(sessoes):
    token, _ = sessoes.criar()
    sessoes.revogar("token-inventado")
    assert sessoes.validar(token)


def test_a_sessao_sobrevive_a_um_reinicio(tmp_path, relogio):
    token, _ = Sessoes(tmp_path, agora=relogio).criar()
    assert Sessoes(tmp_path, agora=relogio).validar(token)


def test_o_arquivo_nao_guarda_o_token_em_claro(sessoes, arquivo):
    token, _ = sessoes.criar()
    conteudo = arquivo.read_text(encoding="utf-8")
    assert token not in conteudo
    assert impressao(token) in conteudo


def test_a_impressao_lida_do_arquivo_nao_serve_de_token(sessoes, arquivo):
    # Whoever reads the file gets the fingerprint, and it must not be a session.
    token, _ = sessoes.criar()
    guardado = json.loads(arquivo.read_text(encoding="utf-8"))
    (impressao_guardada,) = guardado["sessoes"]
    assert impressao_guardada == impressao(token)
    assert not sessoes.validar(impressao_guardada)


def test_a_sessao_expirada_some_do_arquivo(sessoes, arquivo, relogio):
    token, _ = sessoes.criar()
    relogio.avancar(VALIDADE_S + 1)
    assert not sessoes.validar(token)
    assert impressao(token) not in arquivo.read_text(encoding="utf-8")


def test_o_arquivo_e_0600(sessoes, arquivo):
    sessoes.criar()
    assert arquivo.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "conteudo",
    [
        "{ isto nao e json",
        "[]",
        '"texto"',
        json.dumps({"schema_version": SCHEMA_VERSION + 1, "sessoes": {}}),
        json.dumps({"schema_version": SCHEMA_VERSION, "sessoes": "nao e objeto"}),
        json.dumps({"sessoes": {}}),
    ],
)
def test_arquivo_ilegivel_vira_repositorio_vazio(tmp_path, arquivo, relogio, conteudo):
    arquivo.write_text(conteudo, encoding="utf-8")
    sessoes = Sessoes(tmp_path, agora=relogio)
    assert sessoes.quantidade() == 0
    assert json.loads(arquivo.read_text(encoding="utf-8")) == {
        "schema_version": SCHEMA_VERSION,
        "sessoes": {},
    }


def test_entrada_forjada_sem_data_e_descartada(tmp_path, arquivo, relogio):
    forjado = "token-do-atacante"
    arquivo.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "sessoes": {impressao(forjado): {"criada_em": "sempre"}},
            }
        ),
        encoding="utf-8",
    )
    sessoes = Sessoes(tmp_path, agora=relogio)
    assert not sessoes.validar(forjado)
    assert sessoes.quantidade() == 0


@pytest.fixture
def escritas(monkeypatch) -> list[Path]:
    """Counts how many times the store really touches the disk."""
    real = modulo.escrever_json
    feitas: list[Path] = []

    def contar(caminho, dados):
        feitas.append(caminho)
        return real(caminho, dados)

    monkeypatch.setattr(modulo, "escrever_json", contar)
    return feitas


def _impedir_escrita(monkeypatch) -> None:
    def falhar(caminho, dados):
        raise OSError(errno.EROFS, "read-only file system")

    monkeypatch.setattr(modulo, "escrever_json", falhar)


def test_uso_seguido_nao_reescreve_o_arquivo(sessoes, relogio, escritas):
    # Every authenticated request renewed the file, and on the eMMC of the reference
    # appliance that is write amplification with no reader.
    token, _ = sessoes.criar()
    escritas.clear()
    for _ in range(20):
        relogio.avancar(1)
        assert sessoes.validar(token)
    assert escritas == []


def test_a_renovacao_e_gravada_uma_vez_por_intervalo(sessoes, relogio, escritas):
    token, _ = sessoes.criar()
    escritas.clear()
    relogio.avancar(PERSISTIR_APOS_S)
    assert sessoes.validar(token)
    assert len(escritas) == 1
    relogio.avancar(PERSISTIR_APOS_S - 1)
    assert sessoes.validar(token)
    assert len(escritas) == 1


def test_a_renovacao_gravada_sobrevive_a_um_reinicio(tmp_path, relogio):
    sessoes = Sessoes(tmp_path, agora=relogio)
    token, _ = sessoes.criar()
    relogio.avancar(PERSISTIR_APOS_S + 1)
    assert sessoes.validar(token)
    relogio.avancar(VALIDADE_S - 1)
    assert Sessoes(tmp_path, agora=relogio).validar(token)


def test_a_expiracao_dispara_mesmo_sem_gravar_cada_uso(sessoes, relogio, escritas):
    token, _ = sessoes.criar()
    escritas.clear()
    for _ in range(10):
        relogio.avancar(1)
        assert sessoes.validar(token)
    assert escritas == []
    relogio.avancar(VALIDADE_S + 1)
    assert not sessoes.validar(token)
    assert sessoes.quantidade() == 0


def test_registro_datado_no_futuro_expira(tmp_path, arquivo, relogio):
    # A record ahead of now never goes idle, so a file planted by hand, or written by a
    # clock that ran forward, would hold a session that no expiry ever reaches.
    plantado = "token-plantado"
    futuro = relogio.agora + 10 * TETO_S
    arquivo.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "sessoes": {impressao(plantado): {"criada_em": futuro, "usada_em": futuro}},
            }
        ),
        encoding="utf-8",
    )
    sessoes = Sessoes(tmp_path, agora=relogio)
    assert sessoes.expira_em_s(plantado) <= VALIDADE_S
    relogio.avancar(VALIDADE_S + 1)
    assert not sessoes.validar(plantado)
    assert sessoes.quantidade() == 0


def test_revogacao_vale_mesmo_sem_conseguir_gravar(tmp_path, relogio, monkeypatch, caplog):
    # A full or read only data volume must not lose a revocation, nor answer the caller
    # with an error that leaves the session alive on the next boot.
    sessoes = Sessoes(tmp_path, agora=relogio)
    token, _ = sessoes.criar()
    _impedir_escrita(monkeypatch)
    caplog.set_level(logging.ERROR, logger="iphub.sessoes")
    sessoes.revogar(token)
    assert not sessoes.validar(token)
    assert sessoes.quantidade() == 0
    assert ARQUIVO in caplog.text
    assert token not in caplog.text


def test_a_falha_de_escrita_e_registrada_uma_unica_vez(tmp_path, relogio, monkeypatch, caplog):
    sessoes = Sessoes(tmp_path, agora=relogio)
    token, _ = sessoes.criar()
    _impedir_escrita(monkeypatch)
    caplog.set_level(logging.ERROR, logger="iphub.sessoes")
    for _ in range(5):
        relogio.avancar(PERSISTIR_APOS_S + 1)
        assert sessoes.validar(token)
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_o_painel_continua_funcionando_com_o_volume_somente_leitura(tmp_path, relogio):
    sessoes = Sessoes(tmp_path, agora=relogio)
    token, _ = sessoes.criar()
    tmp_path.chmod(0o500)
    try:
        relogio.avancar(PERSISTIR_APOS_S + 1)
        assert sessoes.validar(token)
        outro, _ = sessoes.criar()
        assert sessoes.validar(outro)
    finally:
        tmp_path.chmod(0o700)


def test_a_sessao_de_fora_vale_ate_o_fim_da_janela_e_nao_mais(sessoes, relogio):
    """Opened through the relay, a session carries the end of its window: the idle rule
    would keep it alive for a day of use, and the window says no."""
    token, restante = sessoes.criar(2, valida_ate=relogio.agora + 600)
    assert restante == 600
    relogio.avancar(599)
    assert sessoes.validar(token) is True
    assert sessoes.expira_em_s(token) == 1
    relogio.avancar(1)
    assert sessoes.validar(token) is False


def test_revogar_remotas_deixa_as_da_rede_local(sessoes, relogio):
    de_dentro, _ = sessoes.criar(2)
    de_fora, _ = sessoes.criar(2, valida_ate=relogio.agora + 600)
    sessoes.revogar_remotas()
    assert sessoes.validar(de_fora) is False
    assert sessoes.validar(de_dentro) is True


def test_o_fim_da_janela_sobrevive_a_um_reinicio(tmp_path, relogio):
    token, _ = Sessoes(tmp_path, agora=relogio).criar(2, valida_ate=relogio.agora + 600)
    relogio.avancar(601)
    assert Sessoes(tmp_path, agora=relogio).validar(token) is False


def test_uma_sessao_gravada_sem_fim_de_janela_e_da_rede_local(tmp_path, arquivo, relogio):
    """A file written by an older daemon carries no valida_ate, and that is a session of the
    local network, not one the next window is allowed to kill."""
    antigo = "token-antigo"
    arquivo.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "sessoes": {
                    impressao(antigo): {
                        "criada_em": relogio.agora,
                        "usada_em": relogio.agora,
                        "fatores": 2,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    sessoes = Sessoes(tmp_path, agora=relogio)
    sessoes.revogar_remotas()
    assert sessoes.validar(antigo) is True
