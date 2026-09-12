// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useState, type FormEvent } from "react";
import { SENHA_MINIMA, codigoDoErro, senhaCurta, tomarPosse } from "./api";
import { Restaurar } from "./Backup.tsx";
import { t, traduzirErro } from "./i18n";

export default function Assistente({ aoEntrar }: { aoEntrar: () => void }) {
  const [senha, setSenha] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  // A new box replacing one that died starts from the backup of the old one, and then
  // the owner of the old one is the owner of this one, with the same password.
  const [restaurando, setRestaurando] = useState(false);

  if (restaurando) {
    return (
      <section className="cartao">
        <h2>{t("assistente_restaurar")}</h2>
        <p>{t("assistente_restaurar_texto")}</p>
        <Restaurar prefixo="porta-restaurar" />
        <button type="button" className="botao secundario" onClick={() => setRestaurando(false)}>
          {t("remover_cancelar")}
        </button>
      </section>
    );
  }

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    // The daemon rejects both cases anyway, but only the browser knows the
    // confirmation field, and the answer here costs no PBKDF2 on an ARM board.
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
      await tomarPosse(senha);
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
      <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
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
      <p className="dica">
        <button type="button" className="ligacao" onClick={() => setRestaurando(true)}>
          {t("assistente_restaurar")}
        </button>
      </p>
    </section>
  );
}
