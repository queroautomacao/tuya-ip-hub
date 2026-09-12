// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The controls of an equipment are the capabilities the manifest declares and
// nothing else. An equipment with transport is drawn like a player: what plays now, the
// transport keys with play or pause as one key, the volume with mute beside it, the inputs
// and the radios as chips. Everything else is drawn like a remote: power as two keys, the
// keys of a TV as a keypad, the setpoint of an air conditioner with its mode and fan. Every
// press is one action on the daemon, and the state read back is what the screen shows,
// never the press.

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
  semSetpoint,
} from "./equipamentos.ts";
import { idiomaAtual, t, type Chave } from "./i18n";
import type { Papel } from "./licencas.ts";

// The slider shows the value it was released at until the equipment reads it back, or
// for this long when it never does, so the thumb does not bounce to the old volume during the
// request and a device that ignored the command still lets go of the value.
const ESPERA_DE_LEITURA_MS = 4_000;

// Power is drawn, not written: "Ligar" and "Desligar" overflowed the small key of the
// card, and every other key of the card is a drawing. The two share the standby symbol, and
// off carries the same slash the mute key does.
export const ICONES = {
  ligar: "M11 3v9h2V3zM6.858 6.872A8 8 0 1 0 17.142 6.872L15.857 8.404A6 6 0 1 1 8.143 8.404Z",
  desligar:
    "M11 3v9h2V3zM6.858 6.872A8 8 0 1 0 17.142 6.872L15.857 8.404A6 6 0 1 1 8.143 8.404ZM6 4.5 4.5 6 18 19.5 19.5 18z",
  anterior: "M6 6h2v12H6zm3.5 6 8.5 6V6z",
  tocar: "M8 5v14l11-7z",
  pausar: "M7 5h4v14H7zM13 5h4v14h-4z",
  parar: "M6 6h12v12H6z",
  proxima: "M16 6h2v12h-2zM6 18l8.5-6L6 6z",
  mudo: "M4 9v6h4l5 4V5L8 9H4zm12.5 3 2.5-2.5-1.4-1.4L15.1 10.6 12.6 8.1 11.2 9.5l2.5 2.5-2.5 2.5 1.4 1.4 2.5-2.5 2.5 2.5 1.4-1.4z",
} as const;

// A word of the vocabulary has a phrase in the dictionary, and a word this
// panel does not know yet prints itself instead of an empty button.
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

// The input the driver read back is a value of the driver, and the label the integrator
// gave it is what the customer knows it by.
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
  const teclar = (tecla: string): void => aoExecutar("tecla", { ok: true, valor: tecla });
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
  // A speaker in a group keeps its input for the group, so the chips stay
  // visible and locked while the card says why.
  const emGrupo = papel === "escravo" || papel === "mestre" || papel === "alheio";
  // A speaker held in a group this hub does not lead refuses volume,
  // transport, radios and input, and nothing routes them to a master the hub does not know,
  // so those controls stay visible and locked while the card says why.
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

  // A bar the device has no level for is a promise that dragging it moves something, and
  // on a television whose sound leaves by ARC to a receiver every drag comes back an error.
  // The capability says the equipment CAN take a level; a null says it has none right now.
  const temNivel = painel.volume && estado.volume !== null;
  const volumeEMudo = (temNivel || painel.mudo) && (
    <Grupo rotulo={t("controles_volume")}>
      <div className="controle-volume">
        {temNivel && (
          <>
            <span className="controle-volume-valor">{volume}</span>
            <input
              type="range"
              min={0}
              max={equipamento?.nivel_maximo ?? 100}
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

  // The keys of a television are a remote control and a remote control has a SHAPE: the
  // cross is a cross because a thumb finds up without reading it, the two rockers sit in a
  // column because volume and channel are pressed over and over, and the digits are a keypad
  // because that is what fingers know a keypad to be. Laid out as one wrapped row of pills,
  // every press costs a read, which on the one screen used standing in front of a TV is the
  // difference between a control and a list. Only what the driver declares is drawn, so a
  // model without a guide key has no hole where one would be.
  const teclado = painel.teclas && (
    <Grupo rotulo={t("controles_teclas")}>
      <Remoto teclas={item?.teclas ?? []} ocupado={ocupado} aoTeclar={teclar} />
    </Grupo>
  );

  if (player) {
    const tocando = estado.reproduzindo === true;
    return (
      <div className="painel-controles player">
        {dicaDePapel}
        <div className={`agora ${tocando ? "agora-tocando" : ""}`} aria-live="polite">
          <span className="controle-rotulo">{t("controles_agora")}</span>
          {/* A speaker that plays a raw stream with no metadata has no title to show, */}
          {/* And a line inventing one would be the panel guessing out loud. */}
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
            // A driver that cannot say whether the transport plays leaves reproduzindo
            // empty, and one key that guesses would never send the other half.
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
              disabled={semSetpoint(estado)}
              aria-label={t("acao_temperatura")}
              onChange={(evento) => setGraus(evento.target.value)}
            />
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado || semSetpoint(estado)}
              onClick={() => {
                aoExecutar("temperatura", prepararTemperatura(temperatura));
                setGraus(null);
              }}
            >
              {t("acao_aplicar")}
            </button>
          </div>
          {semSetpoint(estado) && <p className="dica">{t("clima_sem_setpoint")}</p>}
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


// The shape of a remote: the cross, the two rockers, the keypad, and what is left over.
const CRUZ = ["cima", "esquerda", "ok", "direita", "baixo"] as const;
const BALANCINS: readonly (readonly [string, string, string])[] = [
  ["mais", "menos", "acao_volume"],
  ["canal_mais", "canal_menos", "controles_canal"],
];
const DIGITOS = Array.from({ length: 10 }, (_ignorado, numero) => `digito_${numero}`);
const SETAS: Record<string, string> = {
  cima: "M12 5l0 14M12 5l-6 6M12 5l6 6",
  baixo: "M12 19l0-14M12 19l-6-6M12 19l6-6",
  esquerda: "M5 12l14 0M5 12l6-6M5 12l6 6",
  direita: "M19 12l-14 0M19 12l-6-6M19 12l-6 6",
};

function Remoto({
  teclas,
  ocupado,
  aoTeclar,
}: {
  teclas: readonly string[];
  ocupado: boolean;
  aoTeclar: (tecla: string) => void;
}) {
  const tem = (tecla: string): boolean => teclas.includes(tecla);
  const botao = (tecla: string, classe = "") => (
    <button
      key={tecla}
      type="button"
      className={`remoto-tecla ${classe}`.trim()}
      style={{ gridArea: classe === "" ? undefined : classe }}
      disabled={ocupado}
      aria-label={palavra("tecla", tecla)}
      title={palavra("tecla", tecla)}
      onClick={() => aoTeclar(tecla)}
    >
      {SETAS[tecla] !== undefined ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={SETAS[tecla]} />
        </svg>
      ) : (
        palavra("tecla", tecla)
      )}
    </button>
  );
  const naCruz = CRUZ.filter(tem);
  const balancins = BALANCINS.filter(([mais, menos]) => tem(mais) || tem(menos));
  const digitos = DIGITOS.filter(tem);
  const restantes = teclas.filter(
    (tecla) =>
      !CRUZ.includes(tecla as (typeof CRUZ)[number]) &&
      !DIGITOS.includes(tecla) &&
      !BALANCINS.some(([mais, menos]) => tecla === mais || tecla === menos),
  );
  return (
    <div className="remoto">
      {(naCruz.length > 0 || balancins.length > 0) && (
        <div className="remoto-topo">
          {naCruz.length > 0 && (
            <div className="remoto-cruz" role="group" aria-label={t("controles_teclas")}>
              {naCruz.map((tecla) => botao(tecla, tecla))}
            </div>
          )}
          {balancins.length > 0 && (
            <div className="remoto-balancins">
              {balancins.map(([mais, menos, rotulo]) => (
                <div key={mais} className="remoto-balancim">
                  {tem(mais) && botao(mais)}
                  <span className="remoto-balancim-rotulo">{t(rotulo as Chave)}</span>
                  {tem(menos) && botao(menos)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {restantes.length > 0 && (
        <div className="remoto-acoes">{restantes.map((tecla) => botao(tecla))}</div>
      )}
      {digitos.length > 0 && (
        <div className="remoto-digitos">
          {digitos.map((tecla) => botao(tecla))}
        </div>
      )}
    </div>
  );
}
