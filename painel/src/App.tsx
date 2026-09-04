// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Assistente from "./Assistente";
import Cenas from "./Cenas.tsx";
import Concha from "./Concha.tsx";
import Conta from "./Conta.tsx";
import DetalheEquipamento from "./DetalheEquipamento.tsx";
import DriversDeclarativos from "./DriversDeclarativos.tsx";
import Inicio from "./Inicio.tsx";
import Login from "./Login";
import NovoEquipamento from "./NovoEquipamento.tsx";
import Zonas from "./Zonas.tsx";
import { lerEstado, lerSessao, sair, type Estado } from "./api";
import { IDIOMAS, definirIdioma, idiomaAtual, t, type Idioma } from "./i18n";
import { rotaAtual, type Rota } from "./rotas.ts";
import { ler as lerToken } from "./sessao";

const REPOSITORIO = "https://github.com/queroautomacao/tuya-ip-hub";

type Tela = "carregando" | "indisponivel" | "assistente" | "login" | "painel";

// Why: the address after the hash is the only state of navigation, so the back button, a
// reload and a bookmark all agree with what is on the screen.
// Por que: o endereço depois do hash é o único estado da navegação, então o botão de voltar,
// um recarregar e um favorito concordam todos com o que está na tela.
function usarRota(): Rota {
  const [rota, setRota] = useState<Rota>(rotaAtual);
  useEffect(() => {
    const aoMudar = (): void => setRota(rotaAtual());
    window.addEventListener("hashchange", aoMudar);
    return () => window.removeEventListener("hashchange", aoMudar);
  }, []);
  return rota;
}

function Tela({
  rota,
  estado,
  idioma,
  aoSair,
}: {
  rota: Rota;
  estado: Estado;
  idioma: Idioma;
  aoSair: () => void;
}) {
  switch (rota.tela) {
    case "inicio":
      return <Inicio idioma={idioma} />;
    case "novo":
      return <NovoEquipamento idioma={idioma} />;
    case "equipamento":
      return <DetalheEquipamento identidade={rota.identidade} idioma={idioma} />;
    case "zonas":
      return <Zonas idioma={idioma} />;
    case "cenas":
      return <Cenas />;
    case "drivers":
      return <DriversDeclarativos idioma={idioma} />;
    case "conta":
      return <Conta estado={estado} idioma={idioma} aoSair={aoSair} />;
  }
}

function Rodape() {
  return (
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
  );
}

// Why: before the owner is in there is no navigation to draw: the wizard and the login are
// the whole page, with nothing around them that could be tapped by mistake.
// Por que: antes de o dono entrar não há navegação para desenhar: o assistente e o login são
// a página inteira, sem nada em volta que pudesse ser tocado por engano.
function Porta({
  idioma,
  aoTrocarIdioma,
  children,
}: {
  idioma: Idioma;
  aoTrocarIdioma: (idioma: Idioma) => void;
  children: ReactNode;
}) {
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
              onClick={() => aoTrocarIdioma(opcao)}
            >
              {t(`idioma_${opcao}` as const)}
            </button>
          ))}
        </nav>
      </header>
      <main>{children}</main>
      <Rodape />
    </div>
  );
}

export default function App() {
  const [idioma, setIdioma] = useState<Idioma>(idiomaAtual);
  const [tela, setTela] = useState<Tela>("carregando");
  const [estado, setEstado] = useState<Estado | null>(null);
  const rota = usarRota();

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

  if (tela === "painel" && estado) {
    return (
      <Concha rota={rota} idioma={idioma} aoTrocarIdioma={trocarIdioma}>
        <Tela rota={rota} estado={estado} idioma={idioma} aoSair={() => void encerrar()} />
        <Rodape />
      </Concha>
    );
  }

  return (
    <Porta idioma={idioma} aoTrocarIdioma={trocarIdioma}>
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
    </Porta>
  );
}
