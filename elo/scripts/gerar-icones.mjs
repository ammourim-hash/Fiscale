/**
 * Gera os icones PNG da PWA a partir do MESMO desenho do `EloLogo`.
 *
 *   npm run icones
 *
 * ---------------------------------------------------------------------
 *  Por que um gerador, e nao arquivos soltos em public/
 * ---------------------------------------------------------------------
 *  O simbolo do Elo vive em `src/components/elo-logo.tsx`, em SVG. Um PNG
 *  exportado a mao envelhece: alguem ajusta o SVG, e o icone instalado
 *  continua sendo o desenho de tres meses atras — so que ninguem percebe,
 *  porque ninguem reinstala a PWA para conferir. Com o desenho descrito em
 *  codigo, `npm run icones` refaz os quatro arquivos de uma vez.
 *
 *  E nao ha dependencia nova: PNG e um formato simples e o `zlib` ja vem
 *  no Node. Sao ~80 linhas contra uma arvore de dependencia de imagem.
 *
 * ---------------------------------------------------------------------
 *  Os quatro arquivos, e por que cada um existe
 * ---------------------------------------------------------------------
 *  icone-192.png            o minimo que o Chrome exige para instalar
 *  icone-512.png            tela de abertura e listagens grandes
 *  icone-maskable-512.png   Android RECORTA o icone na forma do sistema
 *                           (circulo, quadrado arredondado, gota). Este
 *                           tem o simbolo menor, dentro da zona segura de
 *                           80%, senao os elos ficam sem as pontas.
 *  badge-96.png             o pontinho monocromatico da barra de status do
 *                           Android. Precisa ser BRANCO sobre transparente:
 *                           o sistema o pinta, e um icone colorido vira uma
 *                           mancha preta.
 *
 * A identidade e a do FISCALE/ELO: petroleo e ciano, os dois elos cujo vao
 * forma um balao. Nada de verde — o Elo nao e o WhatsApp.
 */
import { deflateSync } from "node:zlib";
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const PUBLICO = join(AQUI, "..", "public", "icones");

/* ─── cores (as mesmas de elo-logo.tsx) ──────────────────────────────── */

const PETROLEO = [0x10, 0x44, 0x4e, 255];
const CIANO = [0x7f, 0xd1, 0xde, 255];
const CLARO = [0xe8, 0xef, 0xf3, 255];
const BRANCO = [0xff, 0xff, 0xff, 255];
const NADA = [0, 0, 0, 0];

/* ─── geometria ──────────────────────────────────────────────────────── */

/** Distancia com sinal a um retangulo de cantos redondos. Negativa dentro. */
function sdRetanguloRedondo(px, py, cx, cy, hx, hy, r) {
  const dx = Math.abs(px - cx) - (hx - r);
  const dy = Math.abs(py - cy) - (hy - r);
  const fora = Math.hypot(Math.max(dx, 0), Math.max(dy, 0));
  return fora + Math.min(Math.max(dx, dy), 0) - r;
}

function dentroDoTriangulo(px, py, a, b, c) {
  const sinal = (p, q, r) => (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1]);
  const d1 = sinal([px, py], a, b);
  const d2 = sinal([px, py], b, c);
  const d3 = sinal([px, py], c, a);
  const neg = d1 < 0 || d2 < 0 || d3 < 0;
  const pos = d1 > 0 || d2 > 0 || d3 > 0;
  return !(neg && pos);
}

/**
 * A cor de um ponto no espaco 64x64 do SVG original.
 *
 * @param fundo  desenhar o quadrado petroleo atras (false no badge)
 * @param mono   tudo em branco solido (badge do Android)
 */
function corEm(x, y, { fundo, mono, escala, deslocamento }) {
  // `escala` encolhe o simbolo dentro da moldura — a zona segura do
  // icone maskable.
  const sx = (x - 32) / escala + 32 - deslocamento;
  const sy = (y - 32) / escala + 32;

  const traco = 2.5; // metade da largura de 5 do SVG

  const eloTras = Math.abs(sdRetanguloRedondo(sx, sy, 26, 32, 13.5, 12.5, 12.5)) <= traco;
  const eloFrente = Math.abs(sdRetanguloRedondo(sx, sy, 38, 32, 13.5, 12.5, 12.5)) <= traco;
  const rabicho = dentroDoTriangulo(sx, sy, [30, 45.5], [30, 52], [36.5, 45.5]);

  if (mono) {
    return eloTras || eloFrente || rabicho ? BRANCO : NADA;
  }

  // A ordem importa: o elo da frente cobre o de tras, e o vao entre os
  // dois e o que forma o balao.
  if (eloFrente || rabicho) return CLARO;
  if (eloTras) return CIANO;

  if (!fundo) return NADA;
  // O quadrado de fundo, com o mesmo raio proporcional do SVG (15/64).
  return sdRetanguloRedondo(x, y, 32, 32, 32, 32, 15) <= 0 ? PETROLEO : NADA;
}

/* ─── rasterizacao ───────────────────────────────────────────────────── */

/** 4x4 por pixel. Sem isto as curvas dos elos ficam serrilhadas em 192px. */
const AMOSTRAS = 4;

function desenhar(tamanho, opcoes) {
  const pixels = Buffer.alloc(tamanho * tamanho * 4);

  for (let y = 0; y < tamanho; y += 1) {
    for (let x = 0; x < tamanho; x += 1) {
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;

      for (let sy = 0; sy < AMOSTRAS; sy += 1) {
        for (let sx = 0; sx < AMOSTRAS; sx += 1) {
          const px = ((x + (sx + 0.5) / AMOSTRAS) / tamanho) * 64;
          const py = ((y + (sy + 0.5) / AMOSTRAS) / tamanho) * 64;
          const c = corEm(px, py, opcoes);
          // Media ponderada pelo alfa: sem isso a borda de um simbolo
          // opaco sobre transparente vira uma franja escura.
          const peso = c[3] / 255;
          r += c[0] * peso;
          g += c[1] * peso;
          b += c[2] * peso;
          a += c[3];
        }
      }

      const n = AMOSTRAS * AMOSTRAS;
      const alfa = a / n;
      const i = (y * tamanho + x) * 4;
      const soma = a / 255 || 1;
      pixels[i] = Math.round(r / soma);
      pixels[i + 1] = Math.round(g / soma);
      pixels[i + 2] = Math.round(b / soma);
      pixels[i + 3] = Math.round(alfa);
    }
  }

  return pixels;
}

/* ─── PNG ────────────────────────────────────────────────────────────── */

const CRC = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function pedaco(tipo, dados) {
  const tamanho = Buffer.alloc(4);
  tamanho.writeUInt32BE(dados.length, 0);
  const corpo = Buffer.concat([Buffer.from(tipo, "ascii"), dados]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(corpo), 0);
  return Buffer.concat([tamanho, corpo, crc]);
}

function png(tamanho, pixels) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(tamanho, 0);
  ihdr.writeUInt32BE(tamanho, 4);
  ihdr[8] = 8; // 8 bits por canal
  ihdr[9] = 6; // RGBA
  // Sem filtro por linha (byte 0 na frente de cada uma): o deflate ja
  // comprime muito bem um desenho de tres cores.
  const cru = Buffer.alloc(tamanho * (tamanho * 4 + 1));
  for (let y = 0; y < tamanho; y += 1) {
    cru[y * (tamanho * 4 + 1)] = 0;
    pixels.copy(cru, y * (tamanho * 4 + 1) + 1, y * tamanho * 4, (y + 1) * tamanho * 4);
  }

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pedaco("IHDR", ihdr),
    pedaco("IDAT", deflateSync(cru, { level: 9 })),
    pedaco("IEND", Buffer.alloc(0)),
  ]);
}

/* ─── saida ──────────────────────────────────────────────────────────── */

mkdirSync(PUBLICO, { recursive: true });

const arquivos = [
  ["icone-192.png", 192, { fundo: true, mono: false, escala: 1, deslocamento: 0 }],
  ["icone-512.png", 512, { fundo: true, mono: false, escala: 1, deslocamento: 0 }],
  // 0.72 deixa o simbolo dentro da zona segura de 80% com folga; o recorte
  // do Android varia entre fabricantes e a margem paga por si.
  ["icone-maskable-512.png", 512, { fundo: true, mono: false, escala: 0.72, deslocamento: 0 }],
  ["badge-96.png", 96, { fundo: false, mono: true, escala: 0.9, deslocamento: 0 }],
];

for (const [nome, tamanho, opcoes] of arquivos) {
  const buf = png(tamanho, desenhar(tamanho, opcoes));
  writeFileSync(join(PUBLICO, nome), buf);
  console.log(`${nome}  ${tamanho}x${tamanho}  ${(buf.length / 1024).toFixed(1)} kB`);
}

console.log("\nIcones em public/icones/. Referenciados por public/manifest.webmanifest.");
