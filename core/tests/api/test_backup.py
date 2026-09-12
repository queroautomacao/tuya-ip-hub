# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""A backup is the installation in one file with a password of its own, and a restore is the
installation coming back on another board: the same owner, the same secrets, a new box."""

import base64
import json
from dataclasses import replace

import pytest

from iphub import backup as modulo
from iphub import cofre
from iphub.api.backup import nome_do_arquivo
from iphub.app import criar_app
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config, Licenca
from iphub.config import carregar as carregar_config
from iphub.segredos import ARQUIVO_TOKEN

SENHA_DO_BACKUP = "guarde-bem-esta-frase"

INSTALACAO = Config(
    nome_instalacao="Casa da Praia",
    otp_segredo="JBSWY3DPEHPK3PXP",
    remoto_home="123456",
    licencas=(Licenca(id="av1", produto="av", nome="Casa", uuid="u", pid="p", chave="chave-tuya"),),
    equipamentos=(
        Cadastro(
            identidade="a", tipo="t", nome="Sala", ip="192.0.2.10", segredos={"senha": "s3gr3d0"}
        ),
    ),
)


def _b64(bruto: bytes) -> str:
    return base64.b64encode(bruto).decode("ascii")


def test_ida_e_volta_com_a_senha_certa():
    arquivo = modulo.exportar(INSTALACAO, SENHA_DO_BACKUP)
    assert modulo.importar(arquivo, SENHA_DO_BACKUP) == INSTALACAO
    texto = arquivo.decode("utf-8")
    # Nothing of the installation is readable without the password, the name included.
    for segredo in ("chave-tuya", "s3gr3d0", "JBSWY3DPEHPK3PXP", "Casa da Praia", "192.0.2.10"):
        assert segredo not in texto
    envelope = json.loads(texto)
    assert envelope["formato"] == modulo.FORMATO
    assert envelope["iteracoes"] == modulo.ITERACOES


def test_a_senha_errada_e_um_arquivo_mexido_nao_abrem():
    arquivo = modulo.exportar(INSTALACAO, SENHA_DO_BACKUP)
    with pytest.raises(modulo.SenhaErrada):
        modulo.importar(arquivo, "outra-senha-comprida")
    envelope = json.loads(arquivo)
    dados = bytearray(base64.b64decode(envelope["dados"]))
    dados[10] ^= 0x01
    envelope["dados"] = _b64(bytes(dados))
    with pytest.raises(modulo.SenhaErrada):
        modulo.importar(json.dumps(envelope).encode(), SENHA_DO_BACKUP)


@pytest.mark.parametrize(
    "mexida",
    [
        lambda e: e.update(formato="outro"),
        lambda e: e.update(versao=2),
        lambda e: e.update(iteracoes=10),
        lambda e: e.update(iteracoes=10**9),
        lambda e: e.update(sal="zz"),
        lambda e: e.update(dados="nao e base64!"),
    ],
)
def test_um_envelope_que_nao_e_backup_e_recusado(mexida):
    envelope = json.loads(modulo.exportar(INSTALACAO, SENHA_DO_BACKUP))
    mexida(envelope)
    with pytest.raises(modulo.BackupInvalido):
        modulo.importar(json.dumps(envelope).encode(), SENHA_DO_BACKUP)
    with pytest.raises(modulo.BackupInvalido):
        modulo.importar(b"nao sou json", SENHA_DO_BACKUP)


def test_uma_senha_curta_nao_exporta():
    with pytest.raises(ValueError):
        modulo.exportar(INSTALACAO, "curta")


def test_o_nome_do_arquivo_e_o_da_instalacao_com_a_data():
    assert nome_do_arquivo("Casa da Praia", 1_700_000_000).startswith("tuya-ip-hub-casa-da-praia-")
    assert nome_do_arquivo("", 1_700_000_000).startswith("tuya-ip-hub-backup-")
    assert nome_do_arquivo("Ção/Ção", 1_700_000_000).endswith(".iphub")


async def test_exportar_pede_sessao_e_senha_e_entrega_o_arquivo(cliente, posse, bearer):
    assert (await cliente.post("/api/backup", json={"senha": SENHA_DO_BACKUP})).status == 401
    token = await posse(cliente)
    curta = await cliente.post("/api/backup", json={"senha": "curta"}, headers=bearer(token))
    assert (await curta.json())["code"] == "senha_curta"
    resposta = await cliente.post(
        "/api/backup", json={"senha": SENHA_DO_BACKUP}, headers=bearer(token)
    )
    assert resposta.status == 200
    assert resposta.content_type == "application/octet-stream"
    assert 'filename="tuya-ip-hub-' in resposta.headers["Content-Disposition"]
    assert resposta.headers["Cache-Control"] == "no-store"
    restaurada = modulo.importar(await resposta.read(), SENHA_DO_BACKUP)
    assert restaurada.configurado is True


async def test_uma_caixa_nova_restaura_e_o_dono_antigo_entra_com_a_senha_antiga(
    aiohttp_client, amb, posse, senha, bearer, tmp_path
):
    """The whole point: a board died, a new one boots empty, the file comes back."""
    encerrado = []
    antiga = await aiohttp_client(criar_app(amb, encerrar=lambda: encerrado.append("antiga")))
    token = await posse(antiga)
    licenca = {"id": "av1", "produto": "av", "nome": "Casa", "uuid": "u", "pid": "p", "chave": "k"}
    assert (await antiga.post("/api/licencas", json=licenca, headers=bearer(token))).status == 200
    await antiga.post("/api/instalacao", json={"nome": "Casa da Praia"}, headers=bearer(token))
    arquivo = await (
        await antiga.post("/api/backup", json={"senha": SENHA_DO_BACKUP}, headers=bearer(token))
    ).read()

    nova_dir = tmp_path / "nova"
    from dataclasses import replace as substituir

    amb_nova = substituir(amb, dir_data=nova_dir)
    nova = await aiohttp_client(criar_app(amb_nova, encerrar=lambda: encerrado.append("nova")))
    assert (await (await nova.get("/api/estado")).json())["configurado"] is False
    # No session: a hub nobody owns answers the restore, the password of the file is the proof.
    errada = await nova.post(
        "/api/restaurar", json={"senha": "senha-errada-comprida", "arquivo": _b64(arquivo)}
    )
    assert errada.status == 401 and (await errada.json())["code"] == "senha_invalida"
    resposta = await nova.post(
        "/api/restaurar", json={"senha": SENHA_DO_BACKUP, "arquivo": _b64(arquivo)}
    )
    assert resposta.status == 200, await resposta.text()
    assert (await resposta.json())["nome_instalacao"] == "Casa da Praia"
    import asyncio

    for _ in range(100):
        if encerrado == ["nova"]:
            break
        await asyncio.sleep(0.02)
    assert encerrado == ["nova"]
    # On disk: the installation of the old box, sealed by the vault of the new one, and a
    # machine credential of its own.
    lida = carregar_config(nova_dir)
    assert lida.nome_instalacao == "Casa da Praia"
    assert lida.licencas[0].chave == "k"
    texto = (nova_dir / ARQUIVO_CONFIG).read_text(encoding="utf-8")
    assert '"chave": "k"' not in texto and "cofre:" in texto
    assert cofre.abrir(nova_dir).decifrar(
        (nova_dir / ARQUIVO_TOKEN).read_text().strip()
    ) != cofre.abrir(amb.dir_data).decifrar((amb.dir_data / ARQUIVO_TOKEN).read_text().strip())
    # The owner of the old box is the owner of the new one, with the same password.
    entrada = await nova.post("/api/entrar", json={"senha": senha})
    assert entrada.status == 200, await entrada.text()
    assert (await (await nova.get("/api/estado")).json())["configurado"] is True


async def test_um_hub_com_dono_so_restaura_com_a_sessao_do_dono(cliente, posse, bearer):
    token = await posse(cliente)
    arquivo = await (
        await cliente.post("/api/backup", json={"senha": SENHA_DO_BACKUP}, headers=bearer(token))
    ).read()
    sem_sessao = await cliente.post(
        "/api/restaurar", json={"senha": SENHA_DO_BACKUP, "arquivo": _b64(arquivo)}
    )
    assert sem_sessao.status == 401
    assert (await sem_sessao.json())["code"] == "nao_autenticado"


async def test_um_arquivo_que_nao_e_backup_e_recusado_sem_gastar_nada(cliente):
    resposta = await cliente.post(
        "/api/restaurar", json={"senha": SENHA_DO_BACKUP, "arquivo": "!!"}
    )
    assert (await resposta.json())["code"] == "backup_invalido"
    resposta = await cliente.post(
        "/api/restaurar", json={"senha": SENHA_DO_BACKUP, "arquivo": _b64(b"nao sou backup")}
    )
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "backup_invalido"


async def test_senhas_erradas_seguidas_trancam_o_endereco(cliente, posse, bearer):
    token = await posse(cliente)
    arquivo = await (
        await cliente.post("/api/backup", json={"senha": SENHA_DO_BACKUP}, headers=bearer(token))
    ).read()
    corpo = {"senha": "senha-errada-comprida", "arquivo": _b64(arquivo)}
    for _ in range(5):
        await cliente.post("/api/restaurar", json=corpo, headers=bearer(token))
    trancado = await cliente.post("/api/restaurar", json=corpo, headers=bearer(token))
    assert trancado.status == 429


def test_o_backup_nao_leva_o_que_e_da_caixa():
    """Sessions, the machine credential and the key of the panel belong to the box."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    envelope = json.loads(modulo.exportar(replace(INSTALACAO, senha_hash="c3d4"), SENHA_DO_BACKUP))
    chave = modulo._chave(SENHA_DO_BACKUP, bytes.fromhex(envelope["sal"]), modulo.ITERACOES)
    dentro = json.loads(
        AESGCM(chave).decrypt(
            bytes.fromhex(envelope["nonce"]),
            base64.b64decode(envelope["dados"]),
            modulo.FORMATO.encode(),
        )
    )
    assert "senha_hash" in dentro
    assert "api_token" not in dentro and "sessoes" not in dentro
