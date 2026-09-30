/**
 * O que o Elo aceita, e como ele confere.
 *
 * ---------------------------------------------------------------------
 *  Três checagens, e a terceira é a que importa
 * ---------------------------------------------------------------------
 *  1. EXTENSÃO — a ÚLTIMA do nome. `foto.jpg.exe` tem extensão `exe`, e é
 *     assim que precisa ser lida. Olhar "se contém .jpg" é o erro que
 *     transforma a lista de permitidos em decoração.
 *  2. MIME declarado — o que o navegador disse. Barato de conferir e
 *     trivial de forjar, então nunca decide sozinho.
 *  3. MAGIC BYTES — o que o arquivo É. Um `.png` que começa com `MZ` é um
 *     executável renomeado, e só a terceira checagem percebe.
 *
 *  As três precisam concordar. Concordar duas em três é o suficiente para
 *  passar em muitos sistemas, e é exatamente o que se explora.
 *
 * ---------------------------------------------------------------------
 *  Os limites
 * ---------------------------------------------------------------------
 *  Configuráveis por ambiente, num lugar só. Espalhar `if (tamanho >
 *  10_000_000)` pelo código dá o sistema em que o limite muda numa rota e
 *  não muda na outra.
 *
 *  Os padrões estão dimensionados para escritório de contabilidade:
 *  imagem é foto de documento tirada no celular; documento é PDF de guia,
 *  balancete ou contrato; áudio é recado de cliente. O canal externo terá
 *  os SEUS limites (o WhatsApp é mais apertado), e eles não são estes.
 */
import type { MessageType } from "@/generated/prisma/enums";

export type CategoriaMidia = "IMAGE" | "DOCUMENT" | "AUDIO" | "VOICE";

export interface FormatoPermitido {
  categoria: CategoriaMidia;
  /** Extensão sem o ponto, minúscula. */
  extensoes: readonly string[];
  /** MIME aceitos para esta extensão. O primeiro é o canônico. */
  mimes: readonly string[];
}

/**
 * A lista de permitidos. Nada entra por ser "parecido": o que não está
 * aqui é recusado, e acrescentar é uma linha — visível numa revisão.
 */
export const FORMATOS: readonly FormatoPermitido[] = [
  { categoria: "IMAGE", extensoes: ["jpg", "jpeg"], mimes: ["image/jpeg"] },
  { categoria: "IMAGE", extensoes: ["png"], mimes: ["image/png"] },
  { categoria: "IMAGE", extensoes: ["webp"], mimes: ["image/webp"] },

  { categoria: "DOCUMENT", extensoes: ["pdf"], mimes: ["application/pdf"] },
  { categoria: "DOCUMENT", extensoes: ["doc"], mimes: ["application/msword"] },
  {
    categoria: "DOCUMENT",
    extensoes: ["docx"],
    mimes: ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  },
  { categoria: "DOCUMENT", extensoes: ["xls"], mimes: ["application/vnd.ms-excel"] },
  {
    categoria: "DOCUMENT",
    extensoes: ["xlsx"],
    mimes: ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
  },

  { categoria: "AUDIO", extensoes: ["mp3"], mimes: ["audio/mpeg", "audio/mp3"] },
  { categoria: "AUDIO", extensoes: ["m4a"], mimes: ["audio/mp4", "audio/x-m4a"] },
  { categoria: "AUDIO", extensoes: ["aac"], mimes: ["audio/aac"] },
  { categoria: "AUDIO", extensoes: ["ogg"], mimes: ["audio/ogg"] },
  { categoria: "AUDIO", extensoes: ["opus"], mimes: ["audio/ogg", "audio/opus"] },
  { categoria: "AUDIO", extensoes: ["wav"], mimes: ["audio/wav", "audio/x-wav"] },
  // Gravação do navegador. Nenhum navegador grava MP3 nativamente: o
  // Chrome/Edge produzem WebM/Opus e o Safari, MP4/AAC. Aceitar os dois é
  // o que faz o gravador funcionar sem transcodificar no servidor.
  { categoria: "VOICE", extensoes: ["webm"], mimes: ["audio/webm"] },
  { categoria: "VOICE", extensoes: ["m4a", "mp4"], mimes: ["audio/mp4"] },
  { categoria: "VOICE", extensoes: ["ogg"], mimes: ["audio/ogg"] },
] as const;

/* ─── limites ────────────────────────────────────────────────────────── */

function mb(valor: string | undefined, padrao: number): number {
  const n = valor ? Number.parseInt(valor, 10) : Number.NaN;
  return (Number.isFinite(n) && n > 0 ? n : padrao) * 1024 * 1024;
}

export function limites(): Record<CategoriaMidia, number> {
  return {
    // Foto de documento pelo celular passa longe de 10 MB.
    IMAGE: mb(process.env.ELO_UPLOAD_MAX_IMAGE_MB, 10),
    // PDF de balancete com anexos chega perto de 25 MB.
    DOCUMENT: mb(process.env.ELO_UPLOAD_MAX_DOCUMENT_MB, 25),
    AUDIO: mb(process.env.ELO_UPLOAD_MAX_AUDIO_MB, 25),
    // Recado de voz. Cinco minutos de Opus não passam de 4 MB; 15 MB é
    // folga, não convite.
    VOICE: mb(process.env.ELO_UPLOAD_MAX_VOICE_MB, 15),
  };
}

/** O maior de todos — o corte grosseiro antes de olhar o conteúdo. */
export function limiteAbsoluto(): number {
  return Math.max(...Object.values(limites()));
}

/* ─── extensão e nome ────────────────────────────────────────────────── */

/** A ÚLTIMA extensão. `foto.jpg.exe` devolve `exe`. */
export function extensaoDe(nome: string): string {
  const limpo = nome.trim().toLowerCase();
  const ponto = limpo.lastIndexOf(".");
  if (ponto <= 0 || ponto === limpo.length - 1) return "";
  return limpo.slice(ponto + 1);
}

/**
 * Nome seguro para MOSTRAR e para pôr num cabeçalho HTTP.
 *
 * Tira caminho (`../`, `\`, `/`), caracteres de controle, aspas e quebras
 * de linha. As duas últimas importam mais do que parecem: aspa e `\r\n`
 * dentro de `Content-Disposition` deixam quem enviou o arquivo escrever
 * cabeçalho de resposta.
 */
export function nomeSeguro(nome: string): string {
  const soArquivo = nome.split(/[\\/]/).pop() ?? "arquivo";
  const limpo = soArquivo
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .replace(/["\r\n]/g, "")
    .replace(/^\.+/, "")
    .trim();
  return limpo.length > 0 ? limpo.slice(0, 180) : "arquivo";
}

/* ─── magic bytes ────────────────────────────────────────────────────── */

function comecaCom(dados: Buffer, assinatura: number[], deslocamento = 0): boolean {
  if (dados.length < deslocamento + assinatura.length) return false;
  return assinatura.every((b, i) => dados[deslocamento + i] === b);
}

function ehZip(d: Buffer): boolean {
  // docx/xlsx são ZIP. "PK\x03\x04" — e as variantes de arquivo vazio ou
  // dividido, que aparecem em arquivo gerado por ferramenta antiga.
  return (
    comecaCom(d, [0x50, 0x4b, 0x03, 0x04]) ||
    comecaCom(d, [0x50, 0x4b, 0x05, 0x06]) ||
    comecaCom(d, [0x50, 0x4b, 0x07, 0x08])
  );
}

function ehOleAntigo(d: Buffer): boolean {
  // .doc e .xls no formato antigo: cabeçalho OLE2.
  return comecaCom(d, [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]);
}

function ehRiff(d: Buffer, marca: string): boolean {
  return (
    comecaCom(d, [0x52, 0x49, 0x46, 0x46]) && d.subarray(8, 12).toString("latin1") === marca
  );
}

function ehMp4(d: Buffer): boolean {
  // "ftyp" no byte 4. Cobre m4a, mp4 e a gravação do Safari.
  return d.length > 12 && d.subarray(4, 8).toString("latin1") === "ftyp";
}

function ehMp3(d: Buffer): boolean {
  // "ID3" ou um frame MPEG cru (0xFF Ex/Fx).
  if (comecaCom(d, [0x49, 0x44, 0x33])) return true;
  return d.length > 1 && d[0] === 0xff && ((d[1]! & 0xe0) === 0xe0);
}

/**
 * O conteúdo bate com a extensão declarada?
 *
 * Devolve `null` quando não há assinatura confiável para aquele formato —
 * é o caso de `.aac` cru e de alguns `.wav` exóticos. `null` significa
 * "não sei", e não "pode passar": quem chama trata os dois de forma
 * diferente, e nenhum formato sem assinatura entra na lista de permitidos
 * sem essa decisão estar escrita.
 */
export function conteudoBate(extensao: string, dados: Buffer): boolean | null {
  switch (extensao) {
    case "jpg":
    case "jpeg":
      return comecaCom(dados, [0xff, 0xd8, 0xff]);
    case "png":
      return comecaCom(dados, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    case "webp":
      return ehRiff(dados, "WEBP");
    case "pdf":
      return comecaCom(dados, [0x25, 0x50, 0x44, 0x46]); // %PDF
    case "docx":
    case "xlsx":
      return ehZip(dados);
    case "doc":
    case "xls":
      // Office antigo é OLE2; alguns .doc modernos salvos como docx
      // renomeado aparecem como ZIP, e isso também é aceitável.
      return ehOleAntigo(dados) || ehZip(dados);
    case "mp3":
      return ehMp3(dados);
    case "m4a":
    case "mp4":
      return ehMp4(dados);
    case "wav":
      return ehRiff(dados, "WAVE");
    case "ogg":
    case "opus":
      return comecaCom(dados, [0x4f, 0x67, 0x67, 0x53]); // OggS
    case "webm":
      return comecaCom(dados, [0x1a, 0x45, 0xdf, 0xa3]); // EBML
    case "aac":
      // ADTS começa com 0xFFF…, que é indistinguível de MP3 cru. Sem
      // assinatura confiável.
      return null;
    default:
      return false;
  }
}

/* ─── a decisão ──────────────────────────────────────────────────────── */

export type MotivoRecusa =
  | "EXTENSAO_NAO_PERMITIDA"
  | "MIME_NAO_PERMITIDO"
  | "CONTEUDO_NAO_CONFERE"
  | "ARQUIVO_VAZIO"
  | "GRANDE_DEMAIS";

export type Avaliacao =
  | {
      ok: true;
      categoria: CategoriaMidia;
      tipo: MessageType;
      extensao: string;
      /** O MIME canônico do formato — não o que o navegador mandou. */
      mimeType: string;
      nome: string;
    }
  | { ok: false; motivo: MotivoRecusa; detalhe: string };

export interface ArquivoRecebido {
  nome: string;
  mimeDeclarado: string;
  dados: Buffer;
  /** `true` quando veio do gravador: decide VOICE em vez de AUDIO. */
  gravacaoDeVoz?: boolean;
}

export function avaliarArquivo(arquivo: ArquivoRecebido): Avaliacao {
  const nome = nomeSeguro(arquivo.nome);
  const extensao = extensaoDe(nome);

  if (arquivo.dados.byteLength === 0) {
    return { ok: false, motivo: "ARQUIVO_VAZIO", detalhe: "0 bytes" };
  }

  // Se veio do gravador, só os formatos de VOICE valem — o cliente não
  // escolhe ser "voz" para escapar do limite de outra categoria.
  const candidatos = FORMATOS.filter((f) =>
    arquivo.gravacaoDeVoz ? f.categoria === "VOICE" : f.categoria !== "VOICE",
  ).filter((f) => f.extensoes.includes(extensao));

  if (candidatos.length === 0) {
    return {
      ok: false,
      motivo: "EXTENSAO_NAO_PERMITIDA",
      detalhe: extensao === "" ? "sem extensão" : extensao,
    };
  }

  const mime = arquivo.mimeDeclarado.split(";")[0]?.trim().toLowerCase() ?? "";
  const formato = candidatos.find((f) => f.mimes.includes(mime));
  if (!formato) {
    return { ok: false, motivo: "MIME_NAO_PERMITIDO", detalhe: mime || "ausente" };
  }

  const bate = conteudoBate(extensao, arquivo.dados);
  if (bate === false) {
    return {
      ok: false,
      motivo: "CONTEUDO_NAO_CONFERE",
      detalhe: `o conteúdo não parece ${extensao}`,
    };
  }

  const teto = limites()[formato.categoria];
  if (arquivo.dados.byteLength > teto) {
    return {
      ok: false,
      motivo: "GRANDE_DEMAIS",
      detalhe: `${Math.ceil(arquivo.dados.byteLength / 1024 / 1024)} MB (máximo ${Math.floor(teto / 1024 / 1024)} MB)`,
    };
  }

  return {
    ok: true,
    categoria: formato.categoria,
    tipo: formato.categoria as MessageType,
    extensao,
    mimeType: formato.mimes[0]!,
    nome,
  };
}

/** Texto para a tela. O log guarda o detalhe técnico. */
export function mensagemDeRecusa(motivo: MotivoRecusa, detalhe: string): string {
  switch (motivo) {
    case "EXTENSAO_NAO_PERMITIDA":
      return `Tipo de arquivo não aceito (${detalhe}).`;
    case "MIME_NAO_PERMITIDO":
      return "Tipo de arquivo não aceito.";
    case "CONTEUDO_NAO_CONFERE":
      return "O conteúdo do arquivo não corresponde à extensão.";
    case "ARQUIVO_VAZIO":
      return "Arquivo vazio.";
    case "GRANDE_DEMAIS":
      return `Arquivo grande demais: ${detalhe}.`;
  }
}
