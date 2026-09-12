// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useState, type FormEvent } from "react";
import { codigoDoErro, entrar } from "./api";
import { t, traduzirErro } from "./i18n";

export default function Login({ aoEntrar }: { aoEntrar: () => void }) {
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const [acesso, setAcesso] = useState("");
  // The code of the window is what the app of the customer shows to whoever armed it, and
  // it is only ever asked for through the relay.
  const [pedeAcesso, setPedeAcesso] = useState(false);
  // The field of the second factor appears when the daemon asks for it, which is what
  // happens through the relay; on the local network the password alone still opens.
  const [pedeCodigo, setPedeCodigo] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    setErro(null);
    setEnviando(true);
    try {
      await entrar(senha, codigo, acesso);
      aoEntrar();
    } catch (falha) {
      const codigoDaFalha = codigoDoErro(falha);
      setErro(codigoDaFalha);
      if (codigoDaFalha === "otp_exigido" || codigoDaFalha === "codigo_invalido") {
        setPedeCodigo(true);
        setCodigo("");
      } else if (codigoDaFalha === "acesso_exigido" || codigoDaFalha === "acesso_invalido") {
        // Through the relay both codes are always asked for, so both fields appear at
        // once: asking for one, then the other, is two round trips to type two things
        // the person already has in front of them.
        setPedeAcesso(true);
        setPedeCodigo(true);
        setAcesso("");
      } else {
        setSenha("");
      }
      setEnviando(false);
    }
  }

  return (
    <section className="cartao">
      <h2>{t("login_titulo")}</h2>
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
        {pedeAcesso && (
          <>
            <label htmlFor="entrar-acesso">{t("login_acesso")}</label>
            <input
              id="entrar-acesso"
              name="acesso"
              type="text"
              autoComplete="off"
              maxLength={12}
              value={acesso}
              onChange={(evento) => setAcesso(evento.target.value)}
            />
            <p className="dica">{t("login_acesso_dica")}</p>
          </>
        )}
        {pedeCodigo && (
          <>
            <label htmlFor="entrar-codigo">{t("login_codigo")}</label>
            <input
              id="entrar-codigo"
              name="codigo"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={8}
              value={codigo}
              onChange={(evento) => setCodigo(evento.target.value)}
            />
            <p className="dica">{t("login_codigo_dica")}</p>
          </>
        )}
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
