// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The reading cycle of the equipment and the full card of one of them are shared by the
// home and by the detail screen, so they live here, written once; neither screen decides how
// the list is read.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Controles from "./ControlesEquipamento.tsx";
import EditarEquipamento from "./EditarEquipamento.tsx";
import { nomeLegivel } from "./FormularioEquipamento.tsx";
import {
  autenticarEquipamento,
  codigoDoErro,
  executarAcao,
  lerCatalogo,
  lerEquipamentos,
  removerEquipamento,
} from "./api.ts";
import { LEITURA_INICIAL, aplicarCiclo, type Leitura, type Tentativa } from "./ciclo.ts";
import {
  INTERVALO_MS,
  camposVisiveis,
  linhasDoEstado,
  rotuloDoTipo,
  textoDoManifesto,
  type Equipamento,
  type ItemCatalogo,
  type Preparo,
  type ResultadoAutenticacao,
} from "./equipamentos.ts";
import { t, traduzirErro, type Idioma } from "./i18n";
import type { Papel } from "./licencas.ts";

async function tentar<T>(trabalho: () => Promise<T>): Promise<Tentativa<T>> {
  try {
    return { ok: true, valor: await trabalho() };
  } catch (falha) {
    return { ok: false, codigo: codigoDoErro(falha) };
  }
}

export function usarEquipamentos(): Leitura & { recarregar: () => Promise<void> } {
  const [leitura, setLeitura] = useState<Leitura>(LEITURA_INICIAL);

  // The catalog is read in the same cycle as the list, so a request that failed once
  // is tried again on the next tick instead of leaving the panel unable to register
  // anything until someone reloads the page.
  const recarregar = useCallback(async (): Promise<void> => {
    const [catalogo, lista] = await Promise.all([tentar(lerCatalogo), tentar(lerEquipamentos)]);
    setLeitura((anterior) => aplicarCiclo(anterior, catalogo, lista));
  }, []);

  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  return { ...leitura, recarregar };
}

// The readings of the state are what the controls above already show, so repeating them
// here made the first card of the screen the longest one on it and pushed the keys the
// operator came for below the fold. What stays is what nothing else says: where the equipment
// answers, what it calls itself, and the fields of its registration.
export function Linhas({
  equipamento,
  item,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
}) {
  // The technical card is a list of facts, so the label of a fact is the name of the
  // field and never the sentence the manifest writes to teach what to type there. That
  // sentence belongs to the form; here it made a paragraph of a label and left the value
  // beside it one letter wide.
  const nomeDoCampo = nomeLegivel;
  const detalhe = linhasDoEstado(equipamento.estado).find((linha) => linha.especie === "codigo");
  return (
    <dl className="ficha-tecnica">
      <div>
        <dt>{t("equipamentos_endereco")}</dt>
        <dd>{equipamento.ip}</dd>
      </div>
      <div>
        <dt>{t("equipamentos_identidade")}</dt>
        <dd>{equipamento.identidade}</dd>
      </div>
      {camposVisiveis(item, equipamento.campos).map(({ nome, valor }) => (
        <div key={`campo-${nome}`}>
          <dt>{nomeDoCampo(nome)}</dt>
          <dd>{valor}</dd>
        </div>
      ))}
      {equipamento.segredos_definidos.map((nome) => (
        <div key={`segredo-${nome}`}>
          <dt>{nomeDoCampo(nome)}</dt>
          <dd className="texto-suave">{t("segredo_definido")}</dd>
        </div>
      ))}
      {/* Detalhe is a code of a fixed vocabulary and it is the one reading that says why */}
      {/* An equipment is offline, which no control above can show. */}
      {detalhe !== undefined && detalhe.especie === "codigo" && (
        <div>
          <dt>{t("estado_detalhe")}</dt>
          <dd>{t(`detalhe_${detalhe.codigo}` as const)}</dd>
        </div>
      )}
    </dl>
  );
}

export function CartaoEquipamento({
  equipamento,
  item,
  idioma,
  papel = "",
  apos,
  configuracoes,
  aoMudar,
  aoRemover,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  papel?: Papel;
  // What goes right under the controls, and what goes inside the card of the setup.
  apos?: ReactNode;
  configuracoes?: ReactNode;
  aoMudar: () => void;
  aoRemover: () => void;
}) {
  const [erro, setErro] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState(false);
  const [editando, setEditando] = useState(false);
  const [par, setPar] = useState<ResultadoAutenticacao | null>(null);
  const [emCurso, setEmCurso] = useState<string | null>(null);
  const ocupado = emCurso !== null;

  async function chamar(marca: string, trabalho: () => Promise<void>): Promise<void> {
    setEmCurso(marca);
    try {
      await trabalho();
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setEmCurso(null);
    }
  }

  function executar(acao: string, preparo: Preparo): void {
    // A value the action cannot take is refused here with the same stable code the
    // daemon would answer, so a typo costs no request.
    if (!preparo.ok) {
      setErro(preparo.codigo);
      return;
    }
    void chamar(acao, async () => {
      await executarAcao(equipamento.identidade, acao, preparo.valor);
      aoMudar();
    });
  }

  const ajuda = textoDoManifesto(item, idioma, "auth_ajuda");
  const capacidades = item?.capacidades ?? [];
  // The operator opens this screen to press something, so the keys come first and
  // everything that is read and not pressed comes after them: the group right below, because
  // it changes what the keys do, then the technical card, then the whole of the setup under
  // one roof instead of three cards deep down the page.
  return (
    <div className="detalhe">
      <section className={`cartao ${equipamento.estado.online ? "cartao-online" : "cartao-offline"}`}>
        {/* The card that carries the keys is titled with the name of what they command, */}
        {/* Which is the one label an operator needs on the first card of the screen. */}
        <div className="equipamento-cabeca">
          <div>
            <h2>{equipamento.nome || equipamento.identidade}</h2>
            <p className="texto-suave">{rotuloDoTipo(item, idioma, equipamento.tipo)}</p>
          </div>
          <p className="estado-curto">
            <span className="ponto" aria-hidden="true" />
            {equipamento.estado.online ? t("equipamentos_online") : t("equipamentos_offline")}
          </p>
        </div>
        {capacidades.length === 0 && <p className="texto-suave">{t("detalhe_sem_controle")}</p>}
        <Controles
          capacidades={capacidades}
          estado={equipamento.estado}
          item={item}
          equipamento={equipamento}
          papel={papel}
          ocupado={ocupado}
          aoExecutar={executar}
        />
        {item !== undefined && item.auth !== "nenhuma" && (
          <div className="pareamento">
            {/* A device waiting to be paired is not a device that failed, and reading */}
            {/* It as offline sends the operator to the log to find out why, which is where */}
            {/* An LG TV with Connect Apps on and nobody having accepted the dialog sends */}
            {/* Him. The one thing to do is right here, so it says so right here. */}
            {equipamento.estado.detalhe === "auth_pendente" && (
              <p className="aviso-pareamento" role="status">
                {t("detalhe_auth_pendente")}
              </p>
            )}
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado}
              onClick={() =>
                void chamar("parear", async () =>
                  setPar(await autenticarEquipamento(equipamento.identidade)),
                )
              }
            >
              {emCurso === "parear" ? t("pareando") : t("parear")}
            </button>
            {ajuda && <p className="dica">{ajuda}</p>}
            {par !== null && (
              <p className={par === "falhou" ? "erro" : "sucesso"} role="status">
                {par === "aguardando" && item.auth === "codigo"
                  ? t("par_aguardando_codigo")
                  : t(`par_${par}` as const)}
              </p>
            )}
          </div>
        )}
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        {/* Where the equipment answers and what it calls itself belong to the card of */}
        {/* The thing itself, in a footer under the keys, and not to a card of their own. */}
        <Linhas equipamento={equipamento} item={item} />
      </section>
      {apos}
      <section className="cartao">
        <h2>{t("detalhe_configuracoes")}</h2>
        <h3>{t("detalhe_cadastro")}</h3>
        {editando && item !== undefined ? (
          <EditarEquipamento
            equipamento={equipamento}
            item={item}
            idioma={idioma}
            aoSalvar={() => {
              setEditando(false);
              aoMudar();
            }}
            aoCancelar={() => setEditando(false)}
          />
        ) : confirmando ? (
          // Keeps the panel in charge of its own answers, and a browser
          // dialog is also the one thing a kiosk tablet may refuse to show.
          <div className="confirmacao">
            <p>{t("remover_pergunta")}</p>
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado}
              onClick={() => {
                setConfirmando(false);
                void chamar("remover", async () => {
                  await removerEquipamento(equipamento.identidade);
                  aoRemover();
                });
              }}
            >
              {t("remover_confirmar")}
            </button>
            <button type="button" className="botao secundario" onClick={() => setConfirmando(false)}>
              {t("remover_cancelar")}
            </button>
          </div>
        ) : (
          <div className="acoes-largas">
            {item !== undefined && (
              <button type="button" className="botao secundario" onClick={() => setEditando(true)}>
                {t("editar")}
              </button>
            )}
            <button type="button" className="botao secundario" onClick={() => setConfirmando(true)}>
              {t("remover")}
            </button>
          </div>
        )}
        {configuracoes}
      </section>
    </div>
  );
}
