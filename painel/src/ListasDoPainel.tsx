// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The panel of the platform draws the inputs, the shortcuts and the modes of an equipment
// from its profile, and the profile is built from these lists, each item a label the
// customer reads and a value the driver takes. They are edited here, on the equipment, because
// they are a fact about it; the daemon judges the ceilings and answers a stable code.

import { useEffect, useState } from "react";
import { atualizarEquipamento, codigoDoErro } from "./api.ts";
import {
  LISTAS,
  LISTAS_MAXIMO,
  ROTULO_MAXIMO,
  VALOR_DE_LISTA_MAXIMO,
  produtoDe,
  sugeridos,
  textoDoManifesto,
  type Equipamento,
  type Item,
  type ItemCatalogo,
  type Lista,
  type Listas,
} from "./equipamentos.ts";
import { palavra } from "./ControlesEquipamento.tsx";
import { imprimivel } from "./formulario.ts";
import { idiomaAtual, t, traduzirErro, type Chave } from "./i18n";

// Which capability reads each list, so a list is only offered when it is read.
const CAPACIDADE_DA_LISTA: Record<Lista, string> = {
  entradas: "fonte",
  atalhos: "atalho",
  modos: "modo",
};

const SEPARADORES = /[,|;]/;

// The label travels inside the profile string, where ',' '|' and ';' are the
// separators, and the daemon measures printable the way python does, so the panel refuses
// here exactly what the daemon refuses with lista_invalida.
export function itemValido(item: Item): boolean {
  const rotulo = item.rotulo.trim();
  const valor = item.valor.trim();
  if (rotulo === "" || [...rotulo].length > ROTULO_MAXIMO) return false;
  if (SEPARADORES.test(rotulo) || !imprimivel(rotulo)) return false;
  return valor !== "" && [...valor].length <= VALOR_DE_LISTA_MAXIMO && imprimivel(valor);
}

export function listasValidas(listas: Listas): boolean {
  return LISTAS.every((nome) => {
    const itens = listas[nome] ?? [];
    return itens.length <= LISTAS_MAXIMO[nome] && itens.every(itemValido);
  });
}

function limpas(listas: Listas): Listas {
  const saida: Listas = {};
  for (const nome of LISTAS) {
    const itens = (listas[nome] ?? []).map((item) => ({
      rotulo: item.rotulo.trim(),
      valor: item.valor.trim(),
    }));
    if (itens.length > 0) saida[nome] = itens;
  }
  return saida;
}

function Tabela({
  nome,
  itens,
  ajuda,
  exemplos,
  rotuloDosExemplos,
  vazio,
  ocupado,
  aoMudar,
}: {
  nome: Lista;
  itens: Item[];
  ajuda: string;
  exemplos: Item[];
  rotuloDosExemplos: string;
  vazio: string;
  ocupado: boolean;
  aoMudar: (itens: Item[]) => void;
}) {
  const cheia = itens.length >= LISTAS_MAXIMO[nome];
  // An example only helps while it is not already in the list, and it never pushes the
  // list past the ceiling the daemon would refuse.
  const faltando = exemplos
    .filter((exemplo) => !itens.some((atual) => atual.valor === exemplo.valor))
    .slice(0, LISTAS_MAXIMO[nome] - itens.length);
  return (
    <div className="listas-bloco">
      <h3>{t(`listas_${nome}` as const)}</h3>
      <p className="dica">{t(`listas_ajuda_${nome}` as const)}</p>
      {ajuda && <p className="dica">{ajuda}</p>}
      {itens.length === 0 && <p className="texto-suave">{t("listas_vazia")}</p>}
      {itens.length === 0 && vazio !== "" && <p className="dica">{vazio}</p>}
      {faltando.length > 0 && (
        // An example is worth more read than described, so the values themselves are on
        // the screen before the button that puts them in the list.
        <ul className="listas-exemplos">
          {faltando.map((exemplo) => (
            <li key={exemplo.valor}>
              <b>{exemplo.rotulo}</b>
              <code>{exemplo.valor}</code>
            </li>
          ))}
        </ul>
      )}
      {itens.length > 0 && (
        <ol className="listas-itens">
          {itens.map((item, indice) => (
            <li key={indice} className="listas-item">
              <input
                type="text"
                maxLength={ROTULO_MAXIMO}
                value={item.rotulo}
                placeholder={t("listas_rotulo")}
                aria-label={`${t("listas_rotulo")} ${indice + 1}`}
                disabled={ocupado}
                onChange={(evento) =>
                  aoMudar(itens.map((atual, posicao) => (posicao === indice ? { ...atual, rotulo: evento.target.value } : atual)))
                }
              />
              <input
                type="text"
                maxLength={VALOR_DE_LISTA_MAXIMO}
                value={item.valor}
                placeholder={t("listas_valor")}
                aria-label={`${t("listas_valor")} ${indice + 1}`}
                disabled={ocupado}
                onChange={(evento) =>
                  aoMudar(itens.map((atual, posicao) => (posicao === indice ? { ...atual, valor: evento.target.value } : atual)))
                }
              />
              <button
                type="button"
                className="passo-remover"
                aria-label={`${t("listas_remover")} ${indice + 1}`}
                disabled={ocupado}
                onClick={() => aoMudar(itens.filter((_ignorado, posicao) => posicao !== indice))}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="listas-acoes">
        <button
          type="button"
          className="botao secundario"
          disabled={ocupado || cheia}
          onClick={() => aoMudar([...itens, { rotulo: "", valor: "" }])}
        >
          + {t("listas_adicionar")}
        </button>
        {faltando.length > 0 && (
          <button
            type="button"
            className="botao secundario"
            disabled={ocupado}
            onClick={() => aoMudar([...itens, ...faltando])}
          >
            {rotuloDosExemplos}
          </button>
        )}
      </div>
    </div>
  );
}

export default function ListasDoPainel({
  equipamento,
  item,
  aoMudar,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  aoMudar: () => void;
}) {
  const [rascunho, setRascunho] = useState<Listas | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [salvo, setSalvo] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  useEffect(() => {
    setRascunho(null);
  }, [equipamento.identidade]);
  const oferecidas = LISTAS.filter((nome) =>
    (item?.capacidades ?? []).includes(CAPACIDADE_DA_LISTA[nome]),
  );
  // The inputs and the sound modes of a device are the ones IT declares, which no
  // manifest knows in advance, and a manifest that guessed them offered buttons that answer
  // invalid_value: an RX-A1080 refuses hdmi1 and refuses movie, and another receiver of the
  // same maker accepts both. So both lists are filled with what THIS equipment answered, and
  // what the driver suggests comes after, for the lists it can suggest without asking.
  const doAparelhoDe = (nome: Lista): readonly string[] => {
    if (nome === "entradas") return equipamento.estado.fontes;
    if (nome === "modos") return equipamento.estado.modos;
    return [];
  };
  const exemplosDe = (nome: Lista): Item[] => {
    const doDriver = sugeridos(item, nome);
    const doAparelho =
      nome === "atalhos"
        ? equipamento.estado.atalhos.map((atalho) => ({
            rotulo: atalho.rotulo || atalho.valor,
            valor: atalho.valor,
          }))
        : doAparelhoDe(nome).map((valor) => ({
            rotulo: palavra(nome === "modos" ? "modo" : "fonte", valor),
            valor,
          }));
    if (doAparelho.length === 0) return doDriver;
    const vistos = new Set(doAparelho.map((lido) => lido.valor));
    return [...doAparelho, ...doDriver.filter((sugerido) => !vistos.has(sugerido.valor))];
  };
  // An air conditioner has no list, its words come from the manifest; and a driver that
  // reads none of the lists offers nothing to fill.
  if (produtoDe(item) === "ar" || oferecidas.length === 0) return null;
  const listas = rascunho ?? equipamento.listas;
  const validas = listasValidas(listas);

  async function salvar(): Promise<void> {
    setOcupado(true);
    setSalvo(false);
    try {
      await atualizarEquipamento(equipamento.identidade, {
        tipo: equipamento.tipo,
        identidade: equipamento.identidade,
        nome: equipamento.nome,
        ip: equipamento.ip,
        campos: equipamento.campos,
        listas: limpas(listas),
      });
      setRascunho(null);
      setErro(null);
      setSalvo(true);
      aoMudar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="bloco-de-configuracao">
      <h3>{t("listas_titulo")}</h3>
      <p className="texto-suave">{t("listas_intro")}</p>
      {oferecidas.map((nome) => (
        <Tabela
          key={nome}
          nome={nome}
          itens={listas[nome] ?? []}
          ajuda={textoDoManifesto(item, idiomaAtual(), `lista_${nome}`)}
          exemplos={exemplosDe(nome)}
          rotuloDosExemplos={
            doAparelhoDe(nome).length > 0 || (nome === "atalhos" && equipamento.estado.atalhos.length > 0)
              ? t("listas_exemplos_fontes")
              : t("listas_exemplos")
          }
          vazio={
            nome === "entradas" && equipamento.estado.fontes.length === 0
              ? t("listas_sem_fontes")
              : ""
          }
          ocupado={ocupado}
          aoMudar={(itens) => {
            setSalvo(false);
            setRascunho({ ...listas, [nome]: itens });
          }}
        />
      ))}
      {rascunho !== null && !validas && (
        <p className="erro" role="alert">
          {traduzirErro("lista_invalida" as Chave)}
        </p>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {salvo && (
        <p className="sucesso" role="status">
          {t("listas_salvas")}
        </p>
      )}
      {rascunho !== null && (
        <div className="acoes-largas">
          <button type="button" className="botao" disabled={ocupado || !validas} onClick={() => void salvar()}>
            {ocupado ? t("enviando") : t("listas_salvar")}
          </button>
          <button
            type="button"
            className="botao secundario"
            disabled={ocupado}
            onClick={() => {
              setRascunho(null);
              setErro(null);
            }}
          >
            {t("cenas_descartar")}
          </button>
        </div>
      )}
    </div>
  );
}
