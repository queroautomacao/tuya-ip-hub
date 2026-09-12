// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The remote door, in the one place its owner decides about it. Three locks, and the card
// shows which of them is still missing instead of offering a button that would fail: the
// address of the relay, the second factor, and a password that meets the current minimum.
// The window lasts twenty four hours and closes on its own; the integrator can also arm it
// from the app of the customer, which writes the same data point this card writes.
// The door is a service of the maker for the installations it registers, and what names an
// installation is the home of the app, written to the hub by the miniApp of the maker: without
// it the card is not shown at all, and the relay checks the home against the registry for real.

import { useEffect, useRef, useState, type FormEvent } from "react";
import QRCode from "qrcode";
import {
  armarRemoto,
  codigoDoErro,
  comecarOtp,
  confirmarOtp,
  lerRemoto,
  removerOtp,
  type Pareamento,
  type Remoto,
} from "./api";
import { t, traduzirErro } from "./i18n";

const LARGURA_DO_QR = 190;
// The window shrinks while the card is open, and a minute is close enough for a day.
const INTERVALO_MS = 60_000;

function restante(segundos: number): string {
  const horas = Math.floor(segundos / 3600);
  const minutos = Math.floor((segundos % 3600) / 60);
  return `${horas}h ${String(minutos).padStart(2, "0")}min`;
}

function Pendencias({ faltando }: { faltando: string[] }) {
  if (faltando.length === 0) return null;
  return (
    <ul className="lista-simples">
      {faltando.map((codigo) => (
        <li key={codigo} className="texto-suave">
          {traduzirErro(codigo)}
        </li>
      ))}
    </ul>
  );
}

function Pareador({ aoParear }: { aoParear: () => void }) {
  const [pareamento, setPareamento] = useState<Pareamento | null>(null);
  const [codigo, setCodigo] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const tela = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (pareamento === null || tela.current === null) return;
    void QRCode.toCanvas(tela.current, pareamento.uri, {
      width: LARGURA_DO_QR,
      margin: 1,
    }).catch(() => setErro("qr_falhou"));
  }, [pareamento]);

  async function comecar(): Promise<void> {
    setErro(null);
    try {
      setPareamento(await comecarOtp());
    } catch (falha) {
      setErro(codigoDoErro(falha));
    }
  }

  async function confirmar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    setEnviando(true);
    try {
      await confirmarOtp(codigo);
      setPareamento(null);
      setCodigo("");
      setErro(null);
      aoParear();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setEnviando(false);
    }
  }

  if (pareamento === null) {
    return (
      <div className="pilha">
        <p className="texto-suave">{t("remoto_otp_ausente")}</p>
        <button type="button" className="botao secundario" onClick={() => void comecar()}>
          {t("remoto_otp_parear")}
        </button>
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
      </div>
    );
  }

  return (
    <form className="formulario" onSubmit={(evento) => void confirmar(evento)}>
      <p className="texto-suave">{t("remoto_otp_leia")}</p>
      <canvas ref={tela} width={LARGURA_DO_QR} height={LARGURA_DO_QR} />
      {/* The secret is shown once, in writing, for a phone that cannot read the code. */}
      <p className="dica">
        {t("remoto_otp_segredo")}: <code>{pareamento.segredo}</code>
      </p>
      <label htmlFor="otp-codigo">{t("remoto_otp_codigo")}</label>
      <input
        id="otp-codigo"
        name="codigo"
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={8}
        value={codigo}
        onChange={(evento) => setCodigo(evento.target.value)}
      />
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      <button type="submit" className="botao" disabled={enviando || codigo.trim() === ""}>
        {enviando ? t("enviando") : t("remoto_otp_confirmar")}
      </button>
    </form>
  );
}

export default function AcessoRemoto() {
  const [estado, setEstado] = useState<Remoto | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [mexendo, setMexendo] = useState(false);
  const [senha, setSenha] = useState("");
  const [removendo, setRemovendo] = useState(false);

  async function recarregar(): Promise<void> {
    try {
      setEstado(await lerRemoto());
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    }
  }

  useEffect(() => {
    void recarregar();
    const relogio = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(relogio);
  }, []);

  async function armar(valor: boolean): Promise<void> {
    setMexendo(true);
    try {
      setEstado(await armarRemoto(valor));
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setMexendo(false);
    }
  }

  async function desparear(): Promise<void> {
    setMexendo(true);
    try {
      await removerOtp(senha);
      setSenha("");
      setRemovendo(false);
      await recarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setMexendo(false);
    }
  }

  if (estado === null) {
    if (erro === null) return null;
    return (
      <section className="cartao">
        <h2>{t("remoto_titulo")}</h2>
        <p className="texto-suave">{traduzirErro(erro)}</p>
      </section>
    );
  }
  if (!estado.disponivel) return null;

  return (
    <section className="cartao">
      <h2>{t("remoto_titulo")}</h2>
      <p className="texto-suave">{t("remoto_intro")}</p>
      <div className="pilha">
        <p className="dica">
          {t("remoto_home")}: <code>{estado.home}</code>
        </p>
        <p role="status">
          <strong>
            {estado.armado ? t("remoto_aberto") : t("remoto_fechado")}
            {estado.armado ? ` (${restante(estado.restante_s)})` : ""}
          </strong>
        </p>
        {estado.armado && estado.url !== "" && (
          <p>
            {t("remoto_url")}:{" "}
            <a
              className="endereco-da-janela"
              href={estado.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              {estado.url}
            </a>
          </p>
        )}
        {estado.armado && estado.codigo !== "" && (
          <p>
            {t("remoto_codigo")}: <code className="codigo-da-janela">{estado.codigo}</code>
          </p>
        )}
        {estado.armado && <p className="dica">{t("remoto_codigo_dica")}</p>}
        {estado.armado && estado.recusado && (
          <p className="erro" role="alert">
            {t("remoto_recusado")}
          </p>
        )}
        {estado.armado && !estado.conectado && !estado.recusado && (
          <p className="texto-suave">{t("remoto_conectando")}</p>
        )}
        <Pendencias faltando={estado.faltando} />
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        <button
          type="button"
          className={estado.armado ? "botao secundario" : "botao"}
          disabled={mexendo || (!estado.armado && !estado.pronto)}
          onClick={() => void armar(!estado.armado)}
        >
          {estado.armado ? t("remoto_fechar") : t("remoto_abrir")}
        </button>
      </div>
      <h3>{t("remoto_otp_titulo")}</h3>
      {estado.otp_ativo ? (
        <div className="pilha">
          <p className="texto-suave">{t("remoto_otp_ativo")}</p>
          {/* The password is asked for at the moment it is needed, and not before: a field
              asking for it on a settings card, all the time, is a field people learn to
              type into without reading what it is for. */}
          {removendo ? (
            <>
              <label htmlFor="otp-senha">{t("remoto_otp_senha")}</label>
              <input
                id="otp-senha"
                name="senha"
                type="password"
                autoComplete="current-password"
                value={senha}
                onChange={(evento) => setSenha(evento.target.value)}
              />
              <p className="dica">{t("remoto_otp_remover_aviso")}</p>
              <button
                type="button"
                className="botao secundario"
                disabled={mexendo || senha === ""}
                onClick={() => void desparear()}
              >
                {t("remoto_otp_remover_confirmar")}
              </button>
            </>
          ) : (
            <button type="button" className="botao secundario" onClick={() => setRemovendo(true)}>
              {t("remoto_otp_remover")}
            </button>
          )}
        </div>
      ) : (
        <Pareador aoParear={() => void recarregar()} />
      )}
    </section>
  );
}
