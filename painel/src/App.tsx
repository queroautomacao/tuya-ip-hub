// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useCallback, useEffect, useState } from "react";
import Assistente from "./Assistente";
import Cenas from "./Cenas.tsx";
import Concha from "./Concha.tsx";
import Conta from "./Conta.tsx";
import DetalheEquipamento from "./DetalheEquipamento.tsx";
import DriversDeclarativos from "./DriversDeclarativos.tsx";
import { usarEquipamentos } from "./Equipamentos.tsx";
import Inicio from "./Inicio.tsx";
import Login from "./Login";
import Porta from "./Porta.tsx";
import NovoEquipamento from "./NovoEquipamento.tsx";
import Simulador from "./Simulador.tsx";
import { lerEstado, lerSessao, sair, type Estado } from "./api";
import { definirIdioma, idiomaAtual, t, type Idioma } from "./i18n";
import { abasDoMenu, rotaAtual, type Rota } from "./rotas.ts";
import { ler as lerToken } from "./sessao";
import { aplicarTema, definirTema, lerTema, proximoTema, type Tema } from "./tema.ts";

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
  const { lista } = usarEquipamentos();
  // Why: the scenes and the simulator act on registered equipment, so their tabs open once one
  // exists; until then a screen of empty numbers would be a question nobody can answer.
  // Por que: as cenas e o simulador agem sobre equipamento cadastrado, então as abas deles
  // abrem quando existe um; até lá uma tela de números vazios seria pergunta que ninguém
  // responde.
  const temEquipamento = (lista ?? []).length > 0;
  return (
    <Concha
      rota={rota}
      idioma={idioma}
      tema={tema}
      abas={abasDoMenu(temEquipamento)}
      aoSair={aoSair}
      subtitulo={estado.nome_instalacao || t("empresa")}
      aoTrocarIdioma={aoTrocarIdioma}
      aoTrocarTema={aoTrocarTema}
    >
      <Tela rota={rota} estado={estado} idioma={idioma} aoSair={aoSair} aoRenomear={aoRenomear} />
      <Rodape />
    </Concha>
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
    <Porta
      idioma={idioma}
      tema={tema}
      aoTrocarIdioma={trocarIdioma}
      aoTrocarTema={trocarTema}
      rodape={<Rodape />}
    >
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
