// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useEffect, useState } from "react";
import { IDIOMAS, definirIdioma, idiomaAtual, t, type Idioma } from "./i18n";
import { INTERVALO_MS, formatarUptime, lerSaude, type Saude } from "./saude";

const REPOSITORIO = "https://github.com/queroautomacao/tuya-ip-hub";

type Fase = "verificando" | "online" | "offline";

interface Leitura {
  fase: Fase;
  saude: Saude | null;
  em: Date | null;
}

function usarSaude(): Leitura {
  const [leitura, setLeitura] = useState<Leitura>({ fase: "verificando", saude: null, em: null });

  useEffect(() => {
    let ativo = true;

    async function verificar(): Promise<void> {
      let proxima: Leitura;
      try {
        const saude = await lerSaude();
        proxima = { fase: saude.ok ? "online" : "offline", saude, em: new Date() };
      } catch {
        proxima = { fase: "offline", saude: null, em: new Date() };
      }
      if (ativo) setLeitura(proxima);
    }

    void verificar();
    const temporizador = window.setInterval(() => void verificar(), INTERVALO_MS);
    return () => {
      ativo = false;
      window.clearInterval(temporizador);
    };
  }, []);

  return leitura;
}

function CartaoEstado({ leitura, idioma }: { leitura: Leitura; idioma: Idioma }) {
  const { fase, saude, em } = leitura;
  const locale = idioma === "pt" ? "pt-BR" : "en-US";
  return (
    <section className={`cartao cartao-${fase}`} aria-live="polite">
      <h2>{t("estado")}</h2>
      <p className="estado">
        <span className="ponto" aria-hidden="true" />
        {t(`estado_${fase}` as const)}
      </p>
      <dl>
        <dt>{t("versao")}</dt>
        <dd>{saude ? saude.versao : t("indisponivel")}</dd>
        <dt>{t("esquema")}</dt>
        <dd>{saude ? String(saude.schema_version) : t("indisponivel")}</dd>
        <dt>{t("uptime")}</dt>
        <dd>{saude ? formatarUptime(saude.uptime_s) : t("indisponivel")}</dd>
        <dt>{t("ultima_verificacao")}</dt>
        <dd>{em ? em.toLocaleTimeString(locale) : t("indisponivel")}</dd>
      </dl>
    </section>
  );
}

export default function App() {
  const [idioma, setIdioma] = useState<Idioma>(idiomaAtual);
  const leitura = usarSaude();

  function trocarIdioma(novo: Idioma): void {
    definirIdioma(novo);
    setIdioma(novo);
  }

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>{t("titulo")}</h1>
          <p className="subtitulo">{t("subtitulo")}</p>
        </div>
        <nav className="idiomas" aria-label={t("idioma")}>
          {IDIOMAS.map((opcao) => (
            <button
              key={opcao}
              type="button"
              aria-pressed={opcao === idioma}
              onClick={() => trocarIdioma(opcao)}
            >
              {t(`idioma_${opcao}` as const)}
            </button>
          ))}
        </nav>
      </header>
      <main>
        <CartaoEstado leitura={leitura} idioma={idioma} />
        <p className="aviso" role="note">
          {t("marco_aviso")}
        </p>
      </main>
      <footer className="rodape">
        <p>{t("licenca")}</p>
        <p>
          <a href={REPOSITORIO} rel="noreferrer">
            {t("repositorio")}
          </a>
        </p>
        <p className="discreto">
          {t("marca")} {t("copyright")}
        </p>
      </footer>
    </div>
  );
}
