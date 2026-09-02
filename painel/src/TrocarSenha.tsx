// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useState, type FormEvent } from "react";
import { SENHA_MINIMA, codigoDoErro, senhaCurta, trocarSenha } from "./api";
import { t, traduzirErro } from "./i18n";

export default function TrocarSenha() {
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [pronto, setPronto] = useState(false);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    setPronto(false);
    if (senhaCurta(nova)) {
      setErro("senha_curta");
      return;
    }
    if (nova !== confirmacao) {
      setErro("confirmacao");
      return;
    }
    setErro(null);
    setEnviando(true);
    try {
      await trocarSenha(atual, nova);
      setAtual("");
      setNova("");
      setConfirmacao("");
      setPronto(true);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="cartao">
      <h2>{t("trocar_titulo")}</h2>
      <p className="texto-suave">{t("trocar_aviso")}</p>
      <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
        <label htmlFor="senha-atual">{t("trocar_atual")}</label>
        <input
          id="senha-atual"
          name="senha_atual"
          type="password"
          required
          autoComplete="current-password"
          value={atual}
          onChange={(evento) => setAtual(evento.target.value)}
        />
        <label htmlFor="senha-nova">{t("trocar_nova")}</label>
        <input
          id="senha-nova"
          name="senha_nova"
          type="password"
          required
          minLength={SENHA_MINIMA}
          autoComplete="new-password"
          value={nova}
          onChange={(evento) => setNova(evento.target.value)}
        />
        <label htmlFor="senha-confirmacao">{t("trocar_confirmacao")}</label>
        <input
          id="senha-confirmacao"
          name="confirmacao"
          type="password"
          required
          minLength={SENHA_MINIMA}
          autoComplete="new-password"
          value={confirmacao}
          onChange={(evento) => setConfirmacao(evento.target.value)}
        />
        {erro && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        {pronto && (
          <p className="sucesso" role="status">
            {t("trocar_ok")}
          </p>
        )}
        <button type="submit" className="botao" disabled={enviando}>
          {enviando ? t("enviando") : t("trocar_enviar")}
        </button>
      </form>
    </section>
  );
}
