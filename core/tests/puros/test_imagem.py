# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The image and compose files must work on the legacy builder without a bridge network.

A imagem e os arquivos compose precisam funcionar no builder legado sem rede bridge.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]


def _ler(nome: str) -> str:
    arquivo = RAIZ / nome
    assert arquivo.is_file(), f"missing {nome}"
    return arquivo.read_text(encoding="utf-8")


@pytest.fixture
def dockerfile() -> str:
    return _ler("Dockerfile")


@pytest.fixture
def instrucoes(dockerfile: str) -> list[str]:
    return [linha for linha in dockerfile.splitlines() if not linha.lstrip().startswith("#")]


def test_dockerfile_sem_diretiva_syntax(dockerfile):
    assert not re.search(r"^\s*#\s*syntax\s*=", dockerfile, re.IGNORECASE | re.MULTILINE)


@pytest.mark.parametrize(
    "padrao",
    [r"--mount=", r"BUILDPLATFORM", r"TARGETPLATFORM", r"^\s*FROM\b.*--platform=", r"<<"],
)
def test_dockerfile_sem_recurso_exclusivo_do_buildkit(instrucoes, padrao):
    ofensores = [linha for linha in instrucoes if re.search(padrao, linha, re.IGNORECASE)]
    assert not ofensores, f"BuildKit-only syntax {padrao!r} in:\n" + "\n".join(ofensores)


def test_dockerfile_roda_como_usuario_nao_root_no_estagio_final(instrucoes):
    # Why: only the last stage ships; a USER in the panel stage proves nothing.
    # Por que: só o último estágio é entregue; um USER no estágio do painel não prova nada.
    ultimo_from = max(i for i, linha in enumerate(instrucoes) if re.match(r"\s*FROM\b", linha))
    usuarios = [
        m.group(1)
        for linha in instrucoes[ultimo_from:]
        if (m := re.match(r"\s*USER\s+(\S+)", linha))
    ]
    assert usuarios, "final stage has no USER line"
    assert usuarios[-1].split(":")[0] not in {"root", "0"}


def test_dockerfile_healthcheck_com_folga_de_boot_e_sem_curl(instrucoes):
    healthchecks = [linha for linha in instrucoes if re.match(r"\s*HEALTHCHECK\b", linha)]
    assert len(healthchecks) == 1, "Dockerfile must have exactly one HEALTHCHECK"
    assert "--start-period=45s" in healthchecks[0]
    assert "iphub.saude" in healthchecks[0]
    assert "curl" not in healthchecks[0]


def test_compose_usa_rede_do_host_sem_docker_sock_e_com_log_limitado():
    compose = _ler("docker-compose.yml")
    assert re.search(r"^\s*network_mode:\s*['\"]?host['\"]?\s*$", compose, re.MULTILINE)
    assert "docker.sock" not in compose
    assert re.search(r"^\s*max-size:", compose, re.MULTILINE)
    assert not re.search(r"^\s*ports:", compose, re.MULTILINE), (
        "the base compose never publishes a port"
    )
    assert not re.search(r"^\s*container_name:", compose, re.MULTILINE), (
        "a fixed name breaks the smoke"
    )
    assert re.search(r"^\s*image:\s*\$\{IPHUB_IMAGEM:-", compose, re.MULTILINE)
    assert re.search(r"^\s*test:.*iphub\.saude", compose, re.MULTILINE)
    assert "curl" not in compose


def test_compose_constroi_na_rede_do_host():
    # Why: the reference ARM appliance has no bridge network for build containers either.
    # Por que: o appliance ARM de referência também não tem rede bridge para os containers de build.
    compose = _ler("docker-compose.yml")
    bloco = re.search(r"^\s*build:\n((?:\s{4,}.*\n)+)", compose, re.MULTILINE)
    assert bloco, "compose has no build block"
    assert re.search(r"^\s*network:\s*host\s*$", bloco.group(1), re.MULTILINE)


def test_compose_repete_os_tempos_do_healthcheck_da_imagem():
    # Why: a healthcheck in compose replaces the image's entirely, boot slack included.
    # Por que: um healthcheck no compose substitui o da imagem inteiro, folga de boot inclusa.
    compose = _ler("docker-compose.yml")
    for campo, valor in (
        ("start_period", "45s"),
        ("interval", "10s"),
        ("timeout", "5s"),
        ("retries", "3"),
    ):
        assert re.search(rf"^\s*{campo}:\s*{valor}\s*$", compose, re.MULTILINE), campo


def test_dockerfile_nao_da_a_arvore_do_codigo_ao_usuario_do_servico(instrucoes):
    # Why: /app is the working directory, so it lands first on sys.path; a writable /app
    # turns one remote code execution into a package that shadows a real one on the next boot.
    # Por que: /app é o diretório de trabalho, então entra primeiro no sys.path; um /app
    # gravável transforma uma execução remota num pacote que sombreia outro no próximo boot.
    ofensores = [
        linha
        for linha in instrucoes
        if (m := re.search(r"\bchown\s+(\S+)", linha))
        and re.search(r"(^|\s)/app(\s|/|$)", linha)
        and m.group(1).split(":")[0] not in {"root", "0"}
    ]
    assert not ofensores, "the service user must not own /app:\n" + "\n".join(ofensores)


def test_imagem_carrega_o_aviso_de_licenca_do_painel(instrucoes):
    # Why: the bundle embeds React and the minifier drops its notice, so the notice ships apart.
    # Por que: o bundle embute o React e o minificador tira o aviso dele, então o aviso
    # viaja à parte.
    assert any("NOTICE-painel.md" in linha for linha in instrucoes)


def test_compose_desktop_publica_a_porta_8080():
    compose = _ler("docker-compose.desktop.yml")
    assert re.search(r"""^\s*-\s*['"]8080:8080['"]\s*$""", compose, re.MULTILINE)
