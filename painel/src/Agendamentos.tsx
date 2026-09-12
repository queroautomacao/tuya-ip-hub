// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// A schedule is DATA, the way a scene is: one scene, one time, the days. The list is saved
// whole, like the scenes, and the hub says what time it thinks it is, so a wrong time zone
// is seen here before the radio plays at four in the morning.

import { useCallback, useEffect, useState } from "react";
import {
  codigoDoErro,
  lerAgendamentos,
  problemasDoErro,
  salvarAgendamentos,
  type Agendamento,
  type LeituraDeAgenda,
} from "./api";
import type { Cena } from "./cenas.ts";
import { t, traduzirErro, type Chave } from "./i18n";

const DIAS: readonly Chave[] = ["dia_0", "dia_1", "dia_2", "dia_3", "dia_4", "dia_5", "dia_6"];
// The zones an installation of this product is likely to be in; any other IANA name is typed.
const FUSOS = [
  "America/Sao_Paulo",
  "America/Manaus",
  "America/Belem",
  "America/Fortaleza",
  "America/Recife",
  "America/Bahia",
  "America/Cuiaba",
  "America/Campo_Grande",
  "America/Porto_Velho",
  "America/Rio_Branco",
  "America/Noronha",
  "America/Argentina/Buenos_Aires",
  "America/Santiago",
  "America/Montevideo",
  "America/Asuncion",
  "America/Bogota",
  "America/Lima",
  "America/Mexico_City",
  "America/New_York",
  "America/Los_Angeles",
  "Europe/Lisbon",
  "Europe/Madrid",
  "Europe/London",
  "UTC",
];

function novo(cenas: readonly Cena[]): Agendamento {
  const primeira = cenas.find((cena) => cena.nome !== "" || cena.passos.length > 0);
  return { cena: primeira?.numero ?? 1, hora: "07:00", dias: [0, 1, 2, 3, 4], ativo: true };
}

function Linha({
  indice,
  agendamento,
  cenas,
  aoMudar,
  aoRemover,
}: {
  indice: number;
  agendamento: Agendamento;
  cenas: readonly Cena[];
  aoMudar: (novo: Agendamento) => void;
  aoRemover: () => void;
}) {
  const id = `agenda-${indice}`;
  return (
    <li className="agendamento">
      <div className="agendamento-campos">
        <label htmlFor={`${id}-cena`} className="visualmente-oculto">
          {t("agenda_cena")}
        </label>
        <select
          id={`${id}-cena`}
          value={agendamento.cena}
          onChange={(evento) => aoMudar({ ...agendamento, cena: Number(evento.target.value) })}
        >
          {cenas.map((cena) => (
            <option key={cena.numero} value={cena.numero}>
              {cena.numero}. {cena.nome || t("cenas_sem_nome")}
            </option>
          ))}
        </select>
        <label htmlFor={`${id}-hora`} className="visualmente-oculto">
          {t("agenda_hora")}
        </label>
        <input
          id={`${id}-hora`}
          type="time"
          value={agendamento.hora}
          onChange={(evento) => aoMudar({ ...agendamento, hora: evento.target.value })}
        />
        <label className="caixa" htmlFor={`${id}-ativo`}>
          <input
            id={`${id}-ativo`}
            type="checkbox"
            checked={agendamento.ativo}
            onChange={(evento) => aoMudar({ ...agendamento, ativo: evento.target.checked })}
          />
          {t("agenda_ativo")}
        </label>
        <button type="button" className="passo-remover" aria-label={t("agenda_remover")} onClick={aoRemover}>
          ×
        </button>
      </div>
      <div className="dias" role="group" aria-label={t("agenda_dias")}>
        {DIAS.map((chave, dia) => {
          const marcado = agendamento.dias.includes(dia);
          return (
            <button
              key={chave}
              type="button"
              className={`dia${marcado ? " dia-marcado" : ""}`}
              aria-pressed={marcado}
              onClick={() =>
                aoMudar({
                  ...agendamento,
                  dias: marcado
                    ? agendamento.dias.filter((d) => d !== dia)
                    : [...agendamento.dias, dia].sort((a, b) => a - b),
                })
              }
            >
              {t(chave)}
            </button>
          );
        })}
      </div>
    </li>
  );
}

export default function Agendamentos({ cenas }: { cenas: readonly Cena[] }) {
  const [leitura, setLeitura] = useState<LeituraDeAgenda | null>(null);
  const [rascunho, setRascunho] = useState<Agendamento[] | null>(null);
  const [fuso, setFuso] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [problemas, setProblemas] = useState<readonly { campo: string; codigo: string }[]>([]);
  const [salvo, setSalvo] = useState(false);
  const [ocupado, setOcupado] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      setLeitura(await lerAgendamentos());
    } catch (falha) {
      setErro(codigoDoErro(falha));
    }
  }, []);

  useEffect(() => {
    void recarregar();
  }, [recarregar]);

  if (leitura === null) {
    return erro === null ? null : (
      <p className="erro" role="alert">
        {traduzirErro(erro)}
      </p>
    );
  }
  const agendamentos = rascunho ?? leitura.agendamentos;
  const fusoAtual = fuso ?? leitura.fuso;
  const mudou = rascunho !== null || fuso !== null;
  const comCena = cenas.filter((cena) => cena.nome !== "" || cena.passos.length > 0);

  function mudar(lista: Agendamento[]): void {
    setSalvo(false);
    setRascunho(lista);
  }

  async function salvar(): Promise<void> {
    setOcupado(true);
    try {
      setLeitura(await salvarAgendamentos(fusoAtual, agendamentos));
      setRascunho(null);
      setFuso(null);
      setErro(null);
      setProblemas([]);
      setSalvo(true);
    } catch (falha) {
      setErro(codigoDoErro(falha));
      setProblemas(problemasDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section className="cartao agenda">
      <h2>{t("agenda_titulo")}</h2>
      <p className="texto-suave">{t("agenda_intro")}</p>
      <div className="formulario">
        <label htmlFor="agenda-fuso">{t("agenda_fuso")}</label>
        <input
          id="agenda-fuso"
          type="text"
          list="agenda-fusos"
          value={fusoAtual}
          placeholder={leitura.fuso_efetivo}
          onChange={(evento) => {
            setSalvo(false);
            setFuso(evento.target.value);
          }}
        />
        <datalist id="agenda-fusos">
          {FUSOS.map((nome) => (
            <option key={nome} value={nome} />
          ))}
        </datalist>
        <p className="dica">
          {t("agenda_fuso_dica")} {t("agenda_agora")}: <strong>{leitura.agora}</strong> ({leitura.fuso_efetivo})
        </p>
      </div>
      {comCena.length === 0 ? (
        <p className="dica">{t("agenda_sem_cena")}</p>
      ) : (
        <>
          {agendamentos.length === 0 && <p className="texto-suave">{t("agenda_vazia")}</p>}
          <ul className="agendamentos">
            {agendamentos.map((agendamento, indice) => (
              <Linha
                key={indice}
                indice={indice}
                agendamento={agendamento}
                cenas={comCena}
                aoMudar={(novo) => mudar(agendamentos.map((atual, i) => (i === indice ? novo : atual)))}
                aoRemover={() => mudar(agendamentos.filter((_ignorado, i) => i !== indice))}
              />
            ))}
          </ul>
          <button
            type="button"
            className="botao secundario"
            disabled={agendamentos.length >= leitura.maximo}
            onClick={() => mudar([...agendamentos, novo(comCena)])}
          >
            + {t("agenda_novo")}
          </button>
        </>
      )}
      {(mudou || salvo || erro !== null) && (
        <div className="cenas-rodape" role="region" aria-live="polite">
          {mudou && (
            <div className="acoes-largas">
              <button type="button" className="botao" disabled={ocupado} onClick={() => void salvar()}>
                {ocupado ? t("enviando") : t("agenda_salvar")}
              </button>
              <button
                type="button"
                className="botao secundario"
                disabled={ocupado}
                onClick={() => {
                  setRascunho(null);
                  setFuso(null);
                  setErro(null);
                  setProblemas([]);
                }}
              >
                {t("cenas_descartar")}
              </button>
            </div>
          )}
          {salvo && (
            <p className="sucesso" role="status">
              {t("agenda_salvo")}
            </p>
          )}
          {erro !== null && (
            <p className="erro" role="alert">
              {traduzirErro(erro)}
            </p>
          )}
          {problemas.length > 0 && (
            <ul className="problemas">
              {problemas.map((problema) => (
                <li key={`${problema.campo}-${problema.codigo}`}>
                  <code>{problema.campo}</code> {traduzirErro(problema.codigo)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
