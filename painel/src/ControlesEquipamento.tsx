// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: section 6, the controls of an equipment are the capabilities the manifest declares and
// nothing else. An equipment with transport is drawn like a player: what plays now, the
// transport keys with play or pause as one key, the volume with mute beside it, the inputs
// and the radios as chips. Everything else is drawn like a remote: power as two keys, the
// keys of a TV as a keypad, the setpoint of an air conditioner with its mode and fan. Every
// press is one action on the daemon, and the state read back is what the screen shows,
// never the press.
// Por que: seção 6, os controles de um equipamento são as capacidades que o manifesto declara
// e nada mais. Um equipamento com transporte é desenhado como um player: o que toca agora, as
// teclas de transporte com tocar ou pausar numa tecla só, o volume com o mudo ao lado, as
// entradas e as rádios como fichas. Todo o resto é desenhado como um controle remoto: energia
// em duas teclas, as teclas de uma TV como um teclado, o setpoint de um ar condicionado com o
// modo e o vento. Toda apertada é uma ação no daemon, e o estado lido de volta é o que a tela
// mostra, nunca a apertada.

import { useEffect, useState, type ReactNode } from "react";
import {
  TEMPERATURA_MAXIMA,
  TEMPERATURA_MINIMA,
  itensDe,
  paineis,
  prepararTemperatura,
  prepararTexto,
  textoDoManifesto,
  type Capacidade,
  type Equipamento,
  type EstadoEquipamento,
  type Item,
  type ItemCatalogo,
  type Preparo,
} from "./equipamentos.ts";
import { idiomaAtual, t, type Chave } from "./i18n";
import type { Papel } from "./licencas.ts";

// Why: the slider shows the value it was released at until the equipment reads it back, or
// for this long when it never does, so the thumb does not bounce to the old volume during the
// request and a device that ignored the command still lets go of the value.
// Por que: o slider mostra o valor em que foi solto até o equipamento o ler de volta, ou por
// este tempo quando nunca lê, então o cursor não pula para o volume antigo durante a
// requisição e um aparelho que ignorou o comando ainda solta o valor.
const ESPERA_DE_LEITURA_MS = 4_000;

export const ICONES = {
  anterior: "M6 6h2v12H6zm3.5 6 8.5 6V6z",
  tocar: "M8 5v14l11-7z",
  pausar: "M7 5h4v14H7zM13 5h4v14h-4z",
  parar: "M6 6h12v12H6z",
  proxima: "M16 6h2v12h-2zM6 18l8.5-6L6 6z",
  mudo: "M4 9v6h4l5 4V5L8 9H4zm12.5 3 2.5-2.5-1.4-1.4L15.1 10.6 12.6 8.1 11.2 9.5l2.5 2.5-2.5 2.5 1.4 1.4 2.5-2.5 2.5 2.5 1.4-1.4z",
} as const;

// Why: a word of the vocabulary of section 6 has a phrase in the dictionary, and a word this
// panel does not know yet prints itself instead of an empty button.
// Por que: uma palavra do vocabulário da seção 6 tem frase no dicionário, e uma palavra que
// este painel ainda não conhece imprime a si mesma em vez de um botão vazio.
export function palavra(prefixo: string, valor: string): string {
  const texto = t(`${prefixo}_${valor}` as Chave) as string | undefined;
  return texto ?? valor;
}

export function Icone({ desenho }: { desenho: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d={desenho} />
    </svg>
  );
}

function Grupo({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <div className="controle-grupo">
      <span className="controle-rotulo">{rotulo}</span>
      {children}
    </div>
  );
}

function Fichas({
  rotulo,
  opcoes,
  atual,
  ocupado,
  aoEscolher,
}: {
  rotulo: string;
  opcoes: Item[];
  atual: string | null;
  ocupado: boolean;
  aoEscolher: (valor: string) => void;
}) {
  return (
    <div className="fichas" role="group" aria-label={rotulo}>
      {opcoes.map((opcao) => (
        <button
          key={opcao.valor}
          type="button"
          className="ficha"
          aria-pressed={atual === opcao.valor}
          disabled={ocupado}
          onClick={() => aoEscolher(opcao.valor)}
        >
          {opcao.rotulo}
        </button>
      ))}
    </div>
  );
}

// Why: the input the driver read back is a value of the driver, and the label the integrator
// gave it is what the customer knows it by.
// Por que: a entrada que o driver leu de volta é um valor do driver, e o rótulo que o
// integrador deu a ela é como o cliente a conhece.
function rotuloDe(itens: Item[], valor: string | null): string {
  if (valor === null || valor === "") return "";
  return itens.find((item) => item.valor === valor)?.rotulo ?? valor;
}

export default function Controles({
  capacidades,
  estado,
  item,
  equipamento,
  papel = "",
  ocupado,
  aoExecutar,
}: {
  capacidades: string[];
  estado: EstadoEquipamento;
  item?: ItemCatalogo;
  equipamento?: Equipamento;
  papel?: Papel;
  ocupado: boolean;
  aoExecutar: (acao: string, preparo: Preparo) => void;
}) {
  const [arrastando, setArrastando] = useState<number | null>(null);
  const [pendente, setPendente] = useState<number | null>(null);
  const [fonteLivre, setFonteLivre] = useState("");
  const [extra, setExtra] = useState("");
  const [graus, setGraus] = useState<string | null>(null);
  useEffect(() => {
    setPendente(null);
  }, [estado.volume]);
  useEffect(() => {
    if (pendente === null) return undefined;
    const temporizador = window.setTimeout(() => setPendente(null), ESPERA_DE_LEITURA_MS);
    return () => window.clearTimeout(temporizador);
  }, [pendente]);
  const painel = paineis(capacidades);
  if (!painel.algum) return null;
  const tem = (capacidade: Capacidade): boolean => capacidades.includes(capacidade);
  const simples = (acao: Capacidade): void => aoExecutar(acao, { ok: true, valor: null });
  const volume = arrastando ?? pendente ?? estado.volume ?? 0;
  const soltar = (): void => {
    if (arrastando !== null) {
      setPendente(arrastando);
      aoExecutar("volume", { ok: true, valor: arrastando });
    }
    setArrastando(null);
  };
  const deAr = item?.produto === "ar";
  const entradas = equipamento === undefined ? [] : itensDe(equipamento, "entradas");
  const atalhos = equipamento === undefined ? [] : itensDe(equipamento, "atalhos");
  const modos = equipamento === undefined ? [] : itensDe(equipamento, "modos");
  const temperatura = graus ?? String(estado.temperatura ?? 22);
  const doVocabulario = (prefixo: string, palavras: readonly string[]): Item[] =>
    palavras.map((valor) => ({ valor, rotulo: palavra(prefixo, valor) }));
  // Why: section 14, a speaker in a group keeps its input for the group, so the chips stay
  // visible and locked while the card says why.
  // Por que: seção 14, uma caixa num grupo guarda a entrada para o grupo, então as fichas
  // ficam visíveis e travadas enquanto o cartão diz por quê.
  const emGrupo = papel === "escravo" || papel === "mestre" || papel === "alheio";
  // Why: section 14, a speaker held in a group this hub does not lead refuses volume,
  // transport, radios and input, and nothing routes them to a master the hub does not know,
  // so those controls stay visible and locked while the card says why.
  // Por que: seção 14, uma caixa presa num grupo que este hub não lidera recusa volume,
  // transporte, rádios e entrada, e nada os roteia para um mestre que o hub não conhece, então
  // esses controles ficam visíveis e travados enquanto o cartão diz por quê.
  const preso = papel === "alheio";
  const player = painel.transporte.length > 0 && tem("tocar") && tem("pausar");
  const ajudaAtalho = textoDoManifesto(item, idiomaAtual(), "cap_atalho");
  const dicaDePapel = (
    <>
      {papel === "escravo" && <p className="dica">{t("controles_grupo_escravo")}</p>}
      {papel === "mestre" && <p className="dica">{t("controles_grupo_mestre")}</p>}
      {papel === "alheio" && <p className="dica">{t("controles_grupo_alheio")}</p>}
    </>
  );

  const energia = painel.energia.length > 0 && (
    <Grupo rotulo={t("controles_energia")}>
      <div className="segmentos" role="group" aria-label={t("controles_energia")}>
        {painel.energia.map((acao) => (
          <button
            key={acao}
            type="button"
            disabled={ocupado}
            aria-pressed={estado.ligado === (acao === "ligar")}
            onClick={() => simples(acao)}
          >
            {t(`acao_${acao}` as const)}
          </button>
        ))}
      </div>
    </Grupo>
  );

  const volumeEMudo = (painel.volume || painel.mudo) && (
    <Grupo rotulo={t("controles_volume")}>
      <div className="controle-volume">
        {painel.volume && (
          <>
            <span className="controle-volume-valor">{volume}</span>
            <input
              type="range"
              min={0}
              max={100}
              value={volume}
              disabled={preso}
              aria-label={t("acao_volume")}
              onChange={(evento) => setArrastando(Number(evento.target.value))}
              onPointerUp={soltar}
              onKeyUp={soltar}
            />
          </>
        )}
        {painel.mudo && (
          <button
            type="button"
            className={`botao-icone ${estado.mudo === true ? "botao-icone-aceso" : ""}`}
            disabled={ocupado}
            aria-pressed={estado.mudo === true}
            aria-label={t("acao_mudo")}
            title={t("acao_mudo")}
            onClick={() => aoExecutar("mudo", { ok: true, valor: !(estado.mudo ?? false) })}
          >
            <Icone desenho={ICONES.mudo} />
          </button>
        )}
      </div>
    </Grupo>
  );

  const fonte = painel.fonte && (
    <Grupo rotulo={t("controles_fonte")}>
      {entradas.length > 0 ? (
        <Fichas
          rotulo={t("controles_fonte")}
          opcoes={entradas}
          atual={estado.fonte}
          ocupado={ocupado || emGrupo}
          aoEscolher={(valor) => aoExecutar("fonte", { ok: true, valor })}
        />
      ) : estado.fontes.length > 0 ? (
        <Fichas
          rotulo={t("controles_fonte")}
          opcoes={estado.fontes.map((valor) => ({ valor, rotulo: valor }))}
          atual={estado.fonte}
          ocupado={ocupado || emGrupo}
          aoEscolher={(valor) => aoExecutar("fonte", { ok: true, valor })}
        />
      ) : (
        <div className="controle-linha">
          <input
            type="text"
            value={fonteLivre}
            placeholder={t("controles_fonte_livre")}
            aria-label={t("acao_fonte")}
            onChange={(evento) => setFonteLivre(evento.target.value)}
          />
          <button
            type="button"
            className="botao secundario"
            disabled={ocupado || emGrupo}
            onClick={() => aoExecutar("fonte", prepararTexto(fonteLivre))}
          >
            {t("acao_aplicar")}
          </button>
        </div>
      )}
    </Grupo>
  );

  const radios = painel.atalho && (
    <Grupo rotulo={player ? t("controles_radios") : t("controles_atalhos")}>
      {atalhos.length > 0 ? (
        <Fichas
          rotulo={player ? t("controles_radios") : t("controles_atalhos")}
          opcoes={atalhos}
          atual={null}
          ocupado={ocupado || preso}
          aoEscolher={(valor) => aoExecutar("atalho", { ok: true, valor })}
        />
      ) : (
        <p className="dica">{player ? t("controles_sem_atalhos") : t("controles_sem_lista")}</p>
      )}
      {ajudaAtalho && atalhos.length === 0 && <p className="dica">{ajudaAtalho}</p>}
    </Grupo>
  );

  const teclado = painel.teclas && (
    <Grupo rotulo={t("controles_teclas")}>
      <div className="teclado" role="group" aria-label={t("controles_teclas")}>
        {(item?.teclas ?? []).map((tecla) => (
          <button
            key={tecla}
            type="button"
            className="ficha"
            disabled={ocupado}
            onClick={() => aoExecutar("tecla", { ok: true, valor: tecla })}
          >
            {palavra("tecla", tecla)}
          </button>
        ))}
      </div>
    </Grupo>
  );

  if (player) {
    const tocando = estado.reproduzindo === true;
    return (
      <div className="painel-controles player">
        {dicaDePapel}
        <div className={`agora ${tocando ? "agora-tocando" : ""}`} aria-live="polite">
          <span className="controle-rotulo">{t("controles_agora")}</span>
          {/* Why: a speaker that plays a raw stream with no metadata has no title to show, */}
          {/* and a line inventing one would be the panel guessing out loud. */}
          {/* Por que: uma caixa tocando um fluxo cru sem metadado não tem título a mostrar, */}
          {/* e uma linha inventando um seria o painel adivinhando em voz alta. */}
          <strong className="agora-titulo">{estado.tocando ?? ""}</strong>
          <span className="agora-fonte">{rotuloDe(entradas, estado.fonte)}</span>
        </div>
        {energia}
        <div className="transporte transporte-player" role="group" aria-label={t("controles_transporte")}>
          {tem("anterior") && (
            <button
              type="button"
              className="botao-icone"
              disabled={ocupado || preso}
              aria-label={t("acao_anterior")}
              title={t("acao_anterior")}
              onClick={() => simples("anterior")}
            >
              <Icone desenho={ICONES.anterior} />
            </button>
          )}
          {estado.reproduzindo === null ? (
            // Why: a driver that cannot say whether the transport plays leaves reproduzindo
            // empty (section 6), and one key that guesses would never send the other half.
            // Por que: um driver que não sabe dizer se o transporte toca deixa reproduzindo
            // vazio (seção 6), e uma tecla que adivinhasse nunca mandaria a outra metade.
            (["tocar", "pausar"] as const).map((acao) => (
              <button
                key={acao}
                type="button"
                className="botao-icone botao-icone-grande"
                disabled={ocupado || preso}
                aria-label={t(`acao_${acao}` as const)}
                title={t(`acao_${acao}` as const)}
                onClick={() => simples(acao)}
              >
                <Icone desenho={ICONES[acao]} />
              </button>
            ))
          ) : (
            <button
              type="button"
              className="botao-icone botao-icone-grande"
              disabled={ocupado || preso}
              aria-label={tocando ? t("acao_pausar") : t("acao_tocar")}
              title={t("acao_tocar_pausar")}
              onClick={() => simples(tocando ? "pausar" : "tocar")}
            >
              <Icone desenho={tocando ? ICONES.pausar : ICONES.tocar} />
            </button>
          )}
          {tem("parar") && (
            <button
              type="button"
              className="botao-icone"
              disabled={ocupado || preso}
              aria-label={t("acao_parar")}
              title={t("acao_parar")}
              onClick={() => simples("parar")}
            >
              <Icone desenho={ICONES.parar} />
            </button>
          )}
          {tem("proxima") && (
            <button
              type="button"
              className="botao-icone"
              disabled={ocupado || preso}
              aria-label={t("acao_proxima")}
              title={t("acao_proxima")}
              onClick={() => simples("proxima")}
            >
              <Icone desenho={ICONES.proxima} />
            </button>
          )}
        </div>
        {volumeEMudo}
        {fonte}
        {radios}
        {painel.modo && modos.length > 0 && (
          <Grupo rotulo={t("controles_modo")}>
            <Fichas
              rotulo={t("controles_modo")}
              opcoes={modos}
              atual={estado.modo}
              ocupado={ocupado}
              aoEscolher={(valor) => aoExecutar("modo", { ok: true, valor })}
            />
          </Grupo>
        )}
        {teclado}
        {painel.extra && (
          <Grupo rotulo={t("controles_extra")}>
            <div className="controle-linha">
              <input
                type="text"
                value={extra}
                aria-label={t("acao_comando_extra")}
                onChange={(evento) => setExtra(evento.target.value)}
              />
              <button
                type="button"
                className="botao secundario"
                disabled={ocupado}
                onClick={() => aoExecutar("comando_extra", prepararTexto(extra))}
              >
                {t("acao_enviar")}
              </button>
            </div>
            <p className="dica">{t("controles_extra_ajuda")}</p>
          </Grupo>
        )}
      </div>
    );
  }

  return (
    <div className="painel-controles">
      {dicaDePapel}
      {energia}
      {painel.temperatura && (
        <Grupo rotulo={t("controles_temperatura")}>
          <div className="controle-linha">
            <input
              className="curto"
              type="number"
              inputMode="numeric"
              min={TEMPERATURA_MINIMA}
              max={TEMPERATURA_MAXIMA}
              value={temperatura}
              aria-label={t("acao_temperatura")}
              onChange={(evento) => setGraus(evento.target.value)}
            />
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado}
              onClick={() => {
                aoExecutar("temperatura", prepararTemperatura(temperatura));
                setGraus(null);
              }}
            >
              {t("acao_aplicar")}
            </button>
          </div>
        </Grupo>
      )}
      {painel.modo && (
        <Grupo rotulo={t("controles_modo")}>
          {deAr ? (
            <Fichas
              rotulo={t("controles_modo")}
              opcoes={doVocabulario("modo_ar", item?.modos ?? [])}
              atual={estado.modo}
              ocupado={ocupado}
              aoEscolher={(valor) => aoExecutar("modo", { ok: true, valor })}
            />
          ) : modos.length > 0 ? (
            <Fichas
              rotulo={t("controles_modo")}
              opcoes={modos}
              atual={estado.modo}
              ocupado={ocupado}
              aoEscolher={(valor) => aoExecutar("modo", { ok: true, valor })}
            />
          ) : (
            <p className="dica">{t("controles_sem_lista")}</p>
          )}
        </Grupo>
      )}
      {painel.vento && (
        <Grupo rotulo={t("controles_vento")}>
          <Fichas
            rotulo={t("controles_vento")}
            opcoes={doVocabulario("vento", item?.ventos ?? [])}
            atual={estado.vento}
            ocupado={ocupado}
            aoEscolher={(valor) => aoExecutar("vento", { ok: true, valor })}
          />
        </Grupo>
      )}
      {volumeEMudo}
      {painel.transporte.length > 0 && (
        <Grupo rotulo={t("controles_transporte")}>
          <div className="transporte" role="group" aria-label={t("controles_transporte")}>
            {painel.transporte.map((acao) => (
              <button
                key={acao}
                type="button"
                className="botao-icone"
                disabled={ocupado}
                aria-label={t(`acao_${acao}` as const)}
                title={t(`acao_${acao}` as const)}
                onClick={() => simples(acao)}
              >
                <Icone desenho={ICONES[acao]} />
              </button>
            ))}
          </div>
        </Grupo>
      )}
      {fonte}
      {radios}
      {teclado}
      {painel.extra && (
        <Grupo rotulo={t("controles_extra")}>
          <div className="controle-linha">
            <input
              type="text"
              value={extra}
              aria-label={t("acao_comando_extra")}
              onChange={(evento) => setExtra(evento.target.value)}
            />
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado}
              onClick={() => aoExecutar("comando_extra", prepararTexto(extra))}
            >
              {t("acao_enviar")}
            </button>
          </div>
          <p className="dica">{t("controles_extra_ajuda")}</p>
        </Grupo>
      )}
    </div>
  );
}
