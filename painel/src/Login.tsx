// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useState, type FormEvent } from "react";
import { codigoDoErro, entrar } from "./api";
import { t, traduzirErro } from "./i18n";

export default function Login({ aoEntrar }: { aoEntrar: () => void }) {
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    setErro(null);
    setEnviando(true);
    try {
      await entrar(senha);
      aoEntrar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
      setSenha("");
      setEnviando(false);
    }
  }

  return (
    <section className="cartao">
      <h2>{t("login_titulo")}</h2>
      <p>{t("login_intro")}</p>
      <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
        <label htmlFor="entrar-senha">{t("login_senha")}</label>
        <input
          id="entrar-senha"
          name="senha"
          type="password"
          required
          autoComplete="current-password"
          value={senha}
          onChange={(evento) => setSenha(evento.target.value)}
        />
        {erro && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        <button type="submit" className="botao" disabled={enviando}>
          {enviando ? t("enviando") : t("login_enviar")}
        </button>
      </form>
    </section>
  );
}
