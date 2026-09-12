// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The installation as one file, and the way back. Exporting asks for a password for the
// file, because the file leaves the box: it goes into a mail, a drive, a pen drive, and it
// carries every credential of the installation. Restoring replaces the whole installation
// and restarts the daemon, so it asks twice.

import { useState, type FormEvent } from "react";
import { SENHA_MINIMA, codigoDoErro, exportarBackup, restaurarBackup } from "./api";
import { t, traduzirErro } from "./i18n";
import { esperarVoltarERecarregar } from "./reinicio.ts";

function baixar(blob: Blob, nome: string): void {
  const url = URL.createObjectURL(blob);
  const ancora = document.createElement("a");
  ancora.href = url;
  ancora.download = nome;
  document.body.appendChild(ancora);
  ancora.click();
  ancora.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function lerComoBase64(arquivo: File): Promise<string> {
  return new Promise((resolver, rejeitar) => {
    const leitor = new FileReader();
    leitor.onerror = () => rejeitar(new Error("leitura"));
    leitor.onload = () => {
      const texto = typeof leitor.result === "string" ? leitor.result : "";
      resolver(texto.slice(texto.indexOf(",") + 1));
    };
    leitor.readAsDataURL(arquivo);
  });
}

export function Exportar() {
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [feito, setFeito] = useState(false);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    setFeito(false);
    if (senha.length < SENHA_MINIMA) {
      setErro("senha_curta");
      return;
    }
    setEnviando(true);
    try {
      const { blob, nome } = await exportarBackup(senha);
      baixar(blob, nome);
      setSenha("");
      setErro(null);
      setFeito(true);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
      <label htmlFor="backup-senha">{t("conta_backup_senha")}</label>
      <input
        id="backup-senha"
        name="senha"
        type="password"
        autoComplete="new-password"
        minLength={SENHA_MINIMA}
        value={senha}
        onChange={(evento) => setSenha(evento.target.value)}
      />
      <p className="dica">{t("conta_backup_senha_dica")}</p>
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {feito && (
        <p className="sucesso" role="status">
          {t("conta_backup_baixado")}
        </p>
      )}
      <button type="submit" className="botao" disabled={enviando || senha === ""}>
        {enviando ? t("enviando") : t("conta_backup_baixar")}
      </button>
    </form>
  );
}

// The same form on the account screen of an owned hub and on the door of a hub with no
// owner: a new box restoring the installation of the one it replaces.
export function Restaurar({ prefixo, aoRestaurar }: { prefixo: string; aoRestaurar?: () => void }) {
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [senha, setSenha] = useState("");
  const [confirmando, setConfirmando] = useState(false);
  const [restaurando, setRestaurando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function confirmar(): Promise<void> {
    if (arquivo === null) return;
    setConfirmando(false);
    setRestaurando(true);
    try {
      await restaurarBackup(await lerComoBase64(arquivo), senha);
      aoRestaurar?.();
      await esperarVoltarERecarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
      setRestaurando(false);
    }
  }

  if (restaurando) {
    return (
      <p className="texto-suave" role="status">
        {t("conta_restaurando")}
      </p>
    );
  }
  return (
    <form
      className="formulario"
      onSubmit={(evento) => {
        evento.preventDefault();
        setErro(null);
        setConfirmando(true);
      }}
    >
      <label htmlFor={`${prefixo}-arquivo`}>{t("conta_restaurar_arquivo")}</label>
      <input
        id={`${prefixo}-arquivo`}
        name="arquivo"
        type="file"
        accept=".iphub,application/octet-stream,application/json"
        onChange={(evento) => setArquivo(evento.target.files?.[0] ?? null)}
      />
      <label htmlFor={`${prefixo}-senha`}>{t("conta_restaurar_senha")}</label>
      <input
        id={`${prefixo}-senha`}
        name="senha"
        type="password"
        autoComplete="off"
        value={senha}
        onChange={(evento) => setSenha(evento.target.value)}
      />
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {confirmando ? (
        <div className="confirmacao">
          <p>{t("conta_restaurar_pergunta")}</p>
          <button type="button" className="botao perigo" onClick={() => void confirmar()}>
            {t("conta_restaurar_confirmar")}
          </button>
          <button type="button" className="botao secundario" onClick={() => setConfirmando(false)}>
            {t("remover_cancelar")}
          </button>
        </div>
      ) : (
        <button type="submit" className="botao secundario" disabled={arquivo === null || senha === ""}>
          {t("conta_restaurar")}
        </button>
      )}
    </form>
  );
}

export default function CartaoBackup() {
  return (
    <section className="cartao">
      <h2>{t("conta_backup")}</h2>
      <p className="texto-suave">{t("conta_backup_texto")}</p>
      <Exportar />
      <h3>{t("conta_restaurar_titulo")}</h3>
      <p className="texto-suave">{t("conta_restaurar_texto")}</p>
      <Restaurar prefixo="conta-restaurar" />
    </section>
  );
}
