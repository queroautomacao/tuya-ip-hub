// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useState, type FormEvent } from "react";
import { SENHA_MINIMA, codigoDoErro, senhaCurta, tomarPosse } from "./api";
import { t, traduzirErro } from "./i18n";

export default function Assistente({ aoEntrar }: { aoEntrar: () => void }) {
  const [codigo, setCodigo] = useState("");
  const [senha, setSenha] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    // Why: the daemon rejects both cases anyway, but only the browser knows the
    // confirmation field, and the answer here costs no PBKDF2 on an ARM board.
    // Por que: o daemon recusa os dois casos de qualquer jeito, mas só o navegador
    // conhece o campo de confirmação, e a resposta aqui não custa PBKDF2 numa placa ARM.
    if (senhaCurta(senha)) {
      setErro("senha_curta");
      return;
    }
    if (senha !== confirmacao) {
      setErro("confirmacao");
      return;
    }
    setErro(null);
    setEnviando(true);
    try {
      await tomarPosse(codigo, senha);
      aoEntrar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
      setEnviando(false);
    }
  }

  return (
    <section className="cartao">
      <h2>{t("assistente_titulo")}</h2>
      <p>{t("assistente_intro")}</p>
      <p className="texto-suave">{t("assistente_onde")}</p>
      <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
        <label htmlFor="posse-codigo">{t("assistente_codigo")}</label>
        <input
          id="posse-codigo"
          name="codigo"
          type="text"
          required
          autoComplete="off"
          spellCheck={false}
          placeholder={t("assistente_codigo_exemplo")}
          value={codigo}
          onChange={(evento) => setCodigo(evento.target.value)}
        />
        <label htmlFor="posse-senha">{t("assistente_senha")}</label>
        <input
          id="posse-senha"
          name="senha"
          type="password"
          required
          minLength={SENHA_MINIMA}
          autoComplete="new-password"
          value={senha}
          onChange={(evento) => setSenha(evento.target.value)}
        />
        <p className="dica">{t("assistente_senha_dica")}</p>
        <label htmlFor="posse-confirmacao">{t("assistente_confirmacao")}</label>
        <input
          id="posse-confirmacao"
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
        <button type="submit" className="botao" disabled={enviando}>
          {enviando ? t("enviando") : t("assistente_enviar")}
        </button>
      </form>
    </section>
  );
}
