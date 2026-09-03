// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useCallback, useEffect, useState } from "react";
import Assistente from "./Assistente";
import DriversDeclarativos from "./DriversDeclarativos.tsx";
import Equipamentos from "./Equipamentos.tsx";
import Login from "./Login";
import TrocarSenha from "./TrocarSenha";
import { lerEstado, lerSessao, sair, type Estado } from "./api";
import { IDIOMAS, definirIdioma, idiomaAtual, t, type Idioma } from "./i18n";
import { INTERVALO_MS, formatarUptime, lerSaude, type Saude } from "./saude";
import { ler as lerToken } from "./sessao";

const REPOSITORIO = "https://github.com/queroautomacao/tuya-ip-hub";

type Fase = "verificando" | "online" | "offline";
type Tela = "carregando" | "indisponivel" | "assistente" | "login" | "painel";

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

function Painel({
  estado,
  idioma,
  aoSair,
}: {
  estado: Estado;
  idioma: Idioma;
  aoSair: () => void;
}) {
  const leitura = usarSaude();
  return (
    <>
      <CartaoEstado leitura={leitura} idioma={idioma} />
      <Equipamentos idioma={idioma} />
      <DriversDeclarativos idioma={idioma} />
      <section className="cartao">
        <h2>{t("instalacao")}</h2>
        <p className="instalacao">{estado.nome_instalacao || t("sem_nome")}</p>
        <button type="button" className="botao secundario" onClick={aoSair}>
          {t("sair")}
        </button>
      </section>
      <TrocarSenha />
      <p className="aviso" role="note">
        {t("marco_aviso")}
      </p>
    </>
  );
}

export default function App() {
  const [idioma, setIdioma] = useState<Idioma>(idiomaAtual);
  const [tela, setTela] = useState<Tela>("carregando");
  const [estado, setEstado] = useState<Estado | null>(null);

  const carregar = useCallback(async (): Promise<void> => {
    setTela("carregando");
    let atual: Estado;
    try {
      atual = await lerEstado();
    } catch {
      setTela("indisponivel");
      return;
    }
    setEstado(atual);
    if (!atual.configurado) {
      setTela("assistente");
      return;
    }
    if (!lerToken()) {
      setTela("login");
      return;
    }
    try {
      await lerSessao();
      setTela("painel");
    } catch {
      // Why: a session that ended is the ordinary way back to the login screen,
      // not a failure the integrator has to read about.
      // Por que: sessão terminada é o caminho comum de volta ao login, não uma
      // falha sobre a qual o integrador precise ler.
      setTela("login");
    }
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  function trocarIdioma(novo: Idioma): void {
    definirIdioma(novo);
    setIdioma(novo);
  }

  async function encerrar(): Promise<void> {
    try {
      await sair();
    } catch {
      // Why: the browser has already dropped the token, so the screen goes back
      // to the login either way.
      // Por que: o navegador já largou o token, então a tela volta para o login
      // de qualquer jeito.
    }
    setTela("login");
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
        {tela === "carregando" && <p className="carregando">{t("carregando")}</p>}
        {tela === "indisponivel" && (
          <section className="cartao">
            <h2>{t("estado")}</h2>
            <p role="alert">{t("daemon_indisponivel")}</p>
            <button type="button" className="botao" onClick={() => void carregar()}>
              {t("repetir")}
            </button>
          </section>
        )}
        {tela === "assistente" && <Assistente aoEntrar={() => void carregar()} />}
        {tela === "login" && <Login aoEntrar={() => void carregar()} />}
        {tela === "painel" && estado && (
          <Painel estado={estado} idioma={idioma} aoSair={() => void encerrar()} />
        )}
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
