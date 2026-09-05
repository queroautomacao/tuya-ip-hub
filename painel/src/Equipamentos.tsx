// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the reading cycle of the equipment and the full card of one of them are shared by the
// home and by the detail screen, so they live here, written once; neither screen decides how
// the list is read.
// Por que: o ciclo de leitura dos equipamentos e o cartão completo de um deles são
// compartilhados pelo início e pela tela de detalhe, então moram aqui, escritos uma vez;
// nenhuma das telas decide como a lista é lida.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Controles from "./ControlesEquipamento.tsx";
import EditarEquipamento from "./EditarEquipamento.tsx";
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

  // Why: the catalog is read in the same cycle as the list, so a request that failed once
  // is tried again on the next tick instead of leaving the panel unable to register
  // anything until someone reloads the page.
  // Por que: o catálogo é lido no mesmo ciclo da lista, então uma requisição que falhou
  // uma vez é refeita no próximo ciclo em vez de deixar o painel sem conseguir cadastrar
  // nada até alguém recarregar a página.
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

// Why: the readings of the state are what the controls above already show, so repeating them
// here made the first card of the screen the longest one on it and pushed the keys the
// operator came for below the fold. What stays is what nothing else says: where the equipment
// answers, what it calls itself, and the fields of its registration.
// Por que: as leituras do estado são o que os controles acima já mostram, então repeti-las
// aqui fazia do primeiro cartão da tela o mais comprido dela e empurrava para baixo as teclas
// que o operador veio apertar. Fica o que mais nada diz: onde o equipamento responde, como ele
// se chama, e os campos do cadastro dele.
export function Linhas({
  equipamento,
  item,
  idioma,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  idioma: Idioma;
}) {
  const nomeDoCampo = (nome: string): string =>
    textoDoManifesto(item, idioma, `campo_${nome}`) || nome;
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
      {/* Why: detalhe is a code of a fixed vocabulary and it is the one reading that says why */}
      {/* an equipment is offline, which no control above can show. */}
      {/* Por que: detalhe é código de vocabulário fixo e é a única leitura que diz por que um */}
      {/* equipamento está offline, que nenhum controle acima consegue mostrar. */}
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
  // O que vai logo abaixo dos controles, e o que vai dentro do cartão de configuração.
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
    // Why: a value the action cannot take is refused here with the same stable code the
    // daemon would answer, so a typo costs no request.
    // Por que: um valor que a ação não aceita é recusado aqui com o mesmo código estável
    // que o daemon responderia, então um erro de digitação não custa requisição.
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
  // Why: the operator opens this screen to press something, so the keys come first and
  // everything that is read and not pressed comes after them: the group right below, because
  // it changes what the keys do, then the technical card, then the whole of the setup under
  // one roof instead of three cards deep down the page.
  // Por que: o operador abre esta tela para apertar algo, então as teclas vêm primeiro e tudo
  // que é lido e não apertado vem depois delas: o grupo logo abaixo, porque ele muda o que as
  // teclas fazem, depois a ficha técnica, e por fim toda a configuração sob um teto só em vez
  // de três cartões lá embaixo.
  return (
    <div className="detalhe">
      <section className={`cartao ${equipamento.estado.online ? "cartao-online" : "cartao-offline"}`}>
        {/* Why: the card that carries the keys is titled with the name of what they command, */}
        {/* which is the one label an operator needs on the first card of the screen. */}
        {/* Por que: o cartão que leva as teclas se intitula com o nome do que elas comandam, */}
        {/* que é o único rótulo de que um operador precisa no primeiro cartão da tela. */}
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
                {t(`par_${par}` as const)}
              </p>
            )}
          </div>
        )}
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
      </section>
      {apos}
      <section className="cartao cartao-compacto">
        <Linhas equipamento={equipamento} item={item} idioma={idioma} />
      </section>
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
          // Why: section 9 keeps the panel in charge of its own answers, and a browser
          // dialog is also the one thing a kiosk tablet may refuse to show.
          // Por que: a seção 9 mantém o painel dono das próprias respostas, e um diálogo do
          // navegador é também a única coisa que um tablet de quiosque pode recusar mostrar.
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
