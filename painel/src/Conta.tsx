// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: everything about the owner and the daemon in one place: the installation, the session
// of this window, the password, the state of the daemon and what this panel is.
// Por que: tudo sobre o dono e o daemon num lugar só: a instalação, a sessão desta janela, a
// senha, o estado do daemon e o que este painel é.

import { useEffect, useState } from "react";
import TrocarSenha from "./TrocarSenha";
import type { Estado } from "./api";
import { t, type Idioma } from "./i18n";
import { INTERVALO_MS, formatarUptime, lerSaude, type Saude } from "./saude";

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

function CartaoDaemon({ idioma }: { idioma: Idioma }) {
  const { fase, saude, em } = usarSaude();
  const locale = idioma === "pt" ? "pt-BR" : "en-US";
  return (
    <section className={`cartao cartao-${fase}`} aria-live="polite">
      <h2>{t("conta_daemon")}</h2>
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

export default function Conta({
  estado,
  idioma,
  aoSair,
}: {
  estado: Estado;
  idioma: Idioma;
  aoSair: () => void;
}) {
  return (
    <>
      <div className="tela-cabeca">
        <div>
          <h2>{t("conta_titulo")}</h2>
          <p>{t("conta_intro")}</p>
        </div>
      </div>
      <section className="cartao">
        <h2>{t("instalacao")}</h2>
        <p className="instalacao">{estado.nome_instalacao || t("sem_nome")}</p>
        <h2>{t("conta_sessao")}</h2>
        <p className="texto-suave">{t("conta_sessao_texto")}</p>
        <button type="button" className="botao secundario" onClick={aoSair}>
          {t("sair")}
        </button>
      </section>
      <TrocarSenha />
      <CartaoDaemon idioma={idioma} />
      <section className="cartao">
        <h2>{t("conta_sobre")}</h2>
        <p className="texto-suave">{t("marco_aviso")}</p>
      </section>
    </>
  );
}
