// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the panel of the platform draws the inputs, the shortcuts and the modes of an equipment
// from its profile (section 8), and the profile is built from these lists, each item a label the
// customer reads and a value the driver takes. They are edited here, on the equipment, because
// they are a fact about it; the daemon judges the ceilings and answers a stable code.
// Por que: o painel da plataforma desenha as entradas, os atalhos e os modos de um equipamento a
// partir do perfil dele (seção 8), e o perfil nasce destas listas, cada item um rótulo que o
// cliente lê e um valor que o driver recebe. Elas são editadas aqui, no equipamento, porque são
// um fato sobre ele; o daemon julga os tetos e responde um código estável.

import { useEffect, useState } from "react";
import { atualizarEquipamento, codigoDoErro } from "./api.ts";
import {
  LISTAS,
  LISTAS_MAXIMO,
  ROTULO_MAXIMO,
  VALOR_DE_LISTA_MAXIMO,
  produtoDe,
  type Equipamento,
  type Item,
  type ItemCatalogo,
  type Lista,
  type Listas,
} from "./equipamentos.ts";
import { imprimivel } from "./formulario.ts";
import { t, traduzirErro, type Chave } from "./i18n";

// Which capability of section 6 reads each list, so a list is only offered when it is read.
// Qual capacidade da seção 6 lê cada lista, para uma lista só ser oferecida quando é lida.
const CAPACIDADE_DA_LISTA: Record<Lista, string> = {
  entradas: "fonte",
  atalhos: "atalho",
  modos: "modo",
};

const SEPARADORES = /[,|;]/;

// Why: the label travels inside the profile string of section 8, where ',' '|' and ';' are the
// separators, and the daemon measures printable the way python does, so the panel refuses
// here exactly what the daemon refuses with lista_invalida.
// Por que: o rótulo viaja dentro da string de perfil da seção 8, onde ',' '|' e ';' são os
// separadores, e o daemon mede imprimível como o python mede, então o painel recusa aqui
// exatamente o que o daemon recusa com lista_invalida.
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
  ocupado,
  aoMudar,
}: {
  nome: Lista;
  itens: Item[];
  ocupado: boolean;
  aoMudar: (itens: Item[]) => void;
}) {
  const cheia = itens.length >= LISTAS_MAXIMO[nome];
  return (
    <div className="listas-bloco">
      <h3>{t(`listas_${nome}` as const)}</h3>
      <p className="dica">{t(`listas_ajuda_${nome}` as const)}</p>
      {itens.length === 0 && <p className="texto-suave">{t("listas_vazia")}</p>}
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
      <button
        type="button"
        className="botao secundario"
        disabled={ocupado || cheia}
        onClick={() => aoMudar([...itens, { rotulo: "", valor: "" }])}
      >
        + {t("listas_adicionar")}
      </button>
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
  // Why: an air conditioner has no list, its words come from the manifest; and a driver that
  // reads none of the lists offers nothing to fill.
  // Por que: um ar condicionado não tem lista, as palavras dele vêm do manifesto; e um driver
  // que não lê lista nenhuma não oferece nada para preencher.
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
    <section className="cartao">
      <h2>{t("listas_titulo")}</h2>
      <p className="texto-suave">{t("listas_intro")}</p>
      {oferecidas.map((nome) => (
        <Tabela
          key={nome}
          nome={nome}
          itens={listas[nome] ?? []}
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
    </section>
  );
}
