// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: everything about the appliance and its owner in one place, the daemon first because
// it is what the operator checks when something is wrong: the firmware and whether a newer
// one exists, the restart, then the installation name, the session and the password.
// Por que: tudo sobre o appliance e o dono num lugar só, o daemon primeiro porque é o que o
// operador confere quando algo está errado: o firmware e se existe um mais novo, o reinício,
// depois o nome da instalação, a sessão e a senha.

import { useState, type FormEvent } from "react";
import Licencas from "./Licencas.tsx";
import TrocarSenha from "./TrocarSenha";
import {
  codigoDoErro,
  lerAtualizacao,
  reiniciar,
  renomearInstalacao,
  type Atualizacao,
  type Estado,
} from "./api";
import { t, traduzirErro, type Idioma } from "./i18n";
import { formatarUptime, lerSaude } from "./saude";
import { usarSaude } from "./usarSaude.ts";

const NOME_MAXIMO = 60;
const COMANDO_DE_ATUALIZACAO = "docker compose pull && docker compose up -d";
// Why: the daemon answers /health about seven seconds after it starts on the reference
// board, so the panel asks every two seconds for up to a minute before giving up.
// Por que: o daemon responde o /health uns sete segundos depois de subir na placa de
// referência, então o painel pergunta a cada dois segundos por até um minuto antes de
// desistir.
const ESPERA_DE_VOLTA_MS = 2_000;
const TENTATIVAS_DE_VOLTA = 30;

function Atualizar() {
  const [resultado, setResultado] = useState<Atualizacao | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [verificando, setVerificando] = useState(false);

  async function verificar(): Promise<void> {
    setVerificando(true);
    try {
      setResultado(await lerAtualizacao());
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setVerificando(false);
    }
  }

  return (
    <div className="pilha">
      <button type="button" className="botao secundario" disabled={verificando} onClick={() => void verificar()}>
        {verificando ? t("conta_atualizacao_verificando") : t("conta_atualizacao_verificar")}
      </button>
      {resultado !== null && !resultado.verificada && (
        <p className="texto-suave" role="status">
          {t("conta_atualizacao_sem_internet")}
        </p>
      )}
      {resultado !== null && resultado.verificada && resultado.ultima === null && (
        <p className="texto-suave" role="status">
          {t("conta_atualizacao_sem_release")}
        </p>
      )}
      {resultado !== null && resultado.verificada && resultado.ultima !== null && !resultado.disponivel && (
        <p className="sucesso" role="status">
          {t("conta_atualizacao_nenhuma")}
        </p>
      )}
      {resultado !== null && resultado.disponivel && (
        <div className="aviso" role="status">
          <p>
            <strong>
              {t("conta_atualizacao_disponivel")} {resultado.ultima}
            </strong>
          </p>
          <p>{t("conta_atualizacao_como")}</p>
          <pre className="comando">{COMANDO_DE_ATUALIZACAO}</pre>
        </div>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
    </div>
  );
}

function Reiniciar() {
  const [confirmando, setConfirmando] = useState(false);
  const [reiniciando, setReiniciando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function esperarVoltar(): Promise<void> {
    // Why: the first answers are the daemon that is still going down, so the panel waits for
    // it to disappear and then for it to answer again before reloading.
    // Por que: as primeiras respostas são o daemon que ainda está caindo, então o painel
    // espera ele sumir e depois responder de novo antes de recarregar.
    let sumiu = false;
    for (let tentativa = 0; tentativa < TENTATIVAS_DE_VOLTA; tentativa += 1) {
      await new Promise((resolver) => window.setTimeout(resolver, ESPERA_DE_VOLTA_MS));
      try {
        await lerSaude();
        if (sumiu) {
          window.location.reload();
          return;
        }
      } catch {
        sumiu = true;
      }
    }
    window.location.reload();
  }

  async function confirmar(): Promise<void> {
    setConfirmando(false);
    setReiniciando(true);
    try {
      await reiniciar();
      await esperarVoltar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
      setReiniciando(false);
    }
  }

  if (reiniciando) {
    return (
      <p className="texto-suave" role="status">
        {t("conta_reiniciando")}
      </p>
    );
  }
  return (
    <div className="pilha">
      {confirmando ? (
        <div className="confirmacao">
          <p>{t("conta_reiniciar_pergunta")}</p>
          <button type="button" className="botao perigo" onClick={() => void confirmar()}>
            {t("conta_reiniciar_confirmar")}
          </button>
          <button type="button" className="botao secundario" onClick={() => setConfirmando(false)}>
            {t("remover_cancelar")}
          </button>
        </div>
      ) : (
        <button type="button" className="botao perigo" onClick={() => setConfirmando(true)}>
          {t("conta_reiniciar")}
        </button>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
    </div>
  );
}

// Why: on a desktop the rail of the shell shows this at its foot, always in view; a phone has
// no rail, so the card exists there and nowhere else.
// Por que: no desktop o trilho da casca mostra isto no pé, sempre à vista; um celular não tem
// trilho, então o cartão existe lá e em mais lugar nenhum.
function CartaoFirmware({ idioma }: { idioma: Idioma }) {
  const { fase, saude, em } = usarSaude();
  const locale = idioma === "pt" ? "pt-BR" : "en-US";
  return (
    <section className={`cartao cartao-${fase} so-celular`} aria-live="polite">
      <h2>{t("conta_firmware")}</h2>
      <p className="estado">
        <span className="ponto" aria-hidden="true" />
        {t(`estado_${fase}` as const)}
      </p>
      <dl>
        <dt>{t("conta_firmware_atual")}</dt>
        <dd>{saude ? saude.versao : t("indisponivel")}</dd>
        <dt>{t("uptime")}</dt>
        <dd>{saude ? formatarUptime(saude.uptime_s) : t("indisponivel")}</dd>
        <dt>{t("ultima_verificacao")}</dt>
        <dd>{em ? em.toLocaleTimeString(locale) : t("indisponivel")}</dd>
      </dl>
    </section>
  );
}

function CartaoManutencao() {
  return (
    <section className="cartao">
      <h2>{t("conta_manutencao")}</h2>
      <p className="texto-suave">{t("conta_manutencao_texto")}</p>
      <div className="manutencao">
        <Atualizar />
        <Reiniciar />
      </div>
    </section>
  );
}

function CartaoInstalacao({
  nome,
  aoRenomear,
}: {
  nome: string;
  aoRenomear: (nome: string) => void;
}) {
  const [rascunho, setRascunho] = useState(nome);
  const [erro, setErro] = useState<string | null>(null);
  const [salvo, setSalvo] = useState(false);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    setSalvo(false);
    setEnviando(true);
    try {
      const guardado = await renomearInstalacao(rascunho);
      setRascunho(guardado);
      aoRenomear(guardado);
      setErro(null);
      setSalvo(true);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="cartao">
      <h2>{t("instalacao")}</h2>
      <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
        <label htmlFor="instalacao-nome">{t("conta_nome")}</label>
        <input
          id="instalacao-nome"
          name="nome"
          type="text"
          maxLength={NOME_MAXIMO}
          autoComplete="off"
          value={rascunho}
          onChange={(evento) => {
            setSalvo(false);
            setRascunho(evento.target.value);
          }}
        />
        <p className="dica">{t("conta_nome_dica")}</p>
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        {salvo && (
          <p className="sucesso" role="status">
            {t("conta_nome_ok")}
          </p>
        )}
        <button type="submit" className="botao" disabled={enviando || rascunho.trim() === nome}>
          {enviando ? t("enviando") : t("conta_nome_salvar")}
        </button>
      </form>
    </section>
  );
}

export default function Conta({
  estado,
  idioma,
  aoSair,
  aoRenomear,
}: {
  estado: Estado;
  idioma: Idioma;
  aoSair: () => void;
  aoRenomear: (nome: string) => void;
}) {
  return (
    <>
      <div className="tela-cabeca">
        <div>
          <h2>{t("conta_titulo")}</h2>
          <p>{t("conta_intro")}</p>
        </div>
      </div>
      <CartaoFirmware idioma={idioma} />
      <Licencas />
      <CartaoManutencao />
      <CartaoInstalacao nome={estado.nome_instalacao} aoRenomear={aoRenomear} />
      <TrocarSenha />
      <section className="cartao so-celular">
        <h2>{t("conta_sessao")}</h2>
        <p className="texto-suave">{t("conta_sessao_texto")}</p>
        <button type="button" className="botao secundario" onClick={aoSair}>
          {t("sair")}
        </button>
      </section>
    </>
  );
}
