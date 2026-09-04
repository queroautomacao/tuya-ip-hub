// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Assistente from "./Assistente";
import Cenas from "./Cenas.tsx";
import Concha, { BotaoTema, Idiomas, Marca } from "./Concha.tsx";
import Conta from "./Conta.tsx";
import DetalheEquipamento from "./DetalheEquipamento.tsx";
import DriversDeclarativos from "./DriversDeclarativos.tsx";
import { usarEquipamentos } from "./Equipamentos.tsx";
import Inicio from "./Inicio.tsx";
import Login from "./Login";
import NovoEquipamento from "./NovoEquipamento.tsx";
import Simulador from "./Simulador.tsx";
import Zonas from "./Zonas.tsx";
import { lerEstado, lerSessao, sair, type Estado } from "./api";
import { definirIdioma, idiomaAtual, t, type Idioma } from "./i18n";
import { abasVisiveis, rotaAtual, type Rota } from "./rotas.ts";
import { ler as lerToken } from "./sessao";
import { aplicarTema, definirTema, lerTema, proximoTema, type Tema } from "./tema.ts";
import { podeOcuparBloco } from "./zonas.ts";

const REPOSITORIO = "https://github.com/queroautomacao/tuya-ip-hub";

type TelaDaPorta = "carregando" | "indisponivel" | "assistente" | "login" | "painel";

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
  aoRenomear,
}: {
  rota: Rota;
  estado: Estado;
  idioma: Idioma;
  aoSair: () => void;
  aoRenomear: (nome: string) => void;
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
    case "simulador":
      return <Simulador nomeInstalacao={estado.nome_instalacao} />;
    case "drivers":
      return <DriversDeclarativos idioma={idioma} />;
    case "conta":
      return <Conta estado={estado} idioma={idioma} aoSair={aoSair} aoRenomear={aoRenomear} />;
  }
}

// Why: the licence and the source belong on every page and matter to nobody in a hurry, so
// they are the smallest text on it.
// Por que: a licença e o fonte pertencem a toda página e não importam a ninguém com pressa,
// então são o menor texto dela.
function Rodape() {
  return (
    <footer className="rodape miudo">
      <p>
        {t("licenca")}{" "}
        <a href={REPOSITORIO} rel="noreferrer">
          {t("repositorio")}
        </a>
      </p>
      <p>
        {t("marca")} {t("copyright")}
      </p>
    </footer>
  );
}

function Painel({
  estado,
  idioma,
  tema,
  aoTrocarIdioma,
  aoTrocarTema,
  aoSair,
  aoRenomear,
}: {
  estado: Estado;
  idioma: Idioma;
  tema: Tema;
  aoTrocarIdioma: (idioma: Idioma) => void;
  aoTrocarTema: () => void;
  aoSair: () => void;
  aoRenomear: (nome: string) => void;
}) {
  const rota = usarRota();
  const { catalogo, lista } = usarEquipamentos();
  // Why: section 6, a zone is a multiroom equipment, so the tabs about zones exist once one is
  // registered; until then a screen of empty blocks would be a question nobody can answer.
  // Por que: seção 6, uma zona é um equipamento multiroom, então as abas sobre zonas existem
  // quando um está cadastrado; até lá uma tela de blocos vazios seria pergunta que ninguém
  // responde.
  const temMultiroom = (lista ?? []).some((equipamento) =>
    podeOcuparBloco((catalogo ?? []).find((item) => item.tipo === equipamento.tipo)),
  );
  return (
    <Concha
      rota={rota}
      idioma={idioma}
      tema={tema}
      abas={abasVisiveis(temMultiroom)}
      subtitulo={estado.nome_instalacao || t("empresa")}
      aoTrocarIdioma={aoTrocarIdioma}
      aoTrocarTema={aoTrocarTema}
    >
      <Tela rota={rota} estado={estado} idioma={idioma} aoSair={aoSair} aoRenomear={aoRenomear} />
      <Rodape />
    </Concha>
  );
}

// Why: before the owner is in there is no navigation to draw: the wizard and the login are
// the whole page, with nothing around them that could be tapped by mistake.
// Por que: antes de o dono entrar não há navegação para desenhar: o assistente e o login são
// a página inteira, sem nada em volta que pudesse ser tocado por engano.
function Porta({
  idioma,
  tema,
  aoTrocarIdioma,
  aoTrocarTema,
  children,
}: {
  idioma: Idioma;
  tema: Tema;
  aoTrocarIdioma: (idioma: Idioma) => void;
  aoTrocarTema: () => void;
  children: ReactNode;
}) {
  return (
    <div className="pagina">
      <header className="cabecalho">
        <Marca subtitulo={t("subtitulo")} />
        <div className="barra-acoes">
          <BotaoTema tema={tema} aoTrocar={aoTrocarTema} />
          <Idiomas idioma={idioma} aoTrocar={aoTrocarIdioma} />
        </div>
      </header>
      <main>{children}</main>
      <Rodape />
    </div>
  );
}

export default function App() {
  const [idioma, setIdioma] = useState<Idioma>(idiomaAtual);
  const [tema, setTema] = useState<Tema>(lerTema);
  const [tela, setTela] = useState<TelaDaPorta>("carregando");
  const [estado, setEstado] = useState<Estado | null>(null);

  useEffect(() => {
    aplicarTema(tema);
  }, [tema]);

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

  function trocarTema(): void {
    const novo = proximoTema(tema);
    definirTema(novo);
    setTema(novo);
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
      <Painel
        estado={estado}
        idioma={idioma}
        tema={tema}
        aoTrocarIdioma={trocarIdioma}
        aoTrocarTema={trocarTema}
        aoSair={() => void encerrar()}
        aoRenomear={(nome) => setEstado({ ...estado, nome_instalacao: nome })}
      />
    );
  }

  return (
    <Porta idioma={idioma} tema={tema} aoTrocarIdioma={trocarIdioma} aoTrocarTema={trocarTema}>
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
