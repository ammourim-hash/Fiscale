/**
 * Arquivos de teste montados byte a byte.
 *
 * Nada de fixture binária no repositório: um PNG de verdade seriam 8 KB
 * commitados que ninguém consegue revisar. Aqui cada arquivo é construído
 * a partir da sua assinatura real, então dá para LER o que o teste está
 * afirmando — e forjar um caso inválido é mudar um byte.
 */

function comCabecalho(assinatura: number[], recheio: number): Buffer {
  return Buffer.concat([Buffer.from(assinatura), Buffer.alloc(recheio, 0x20)]);
}

/** PNG válido: assinatura de 8 bytes + corpo. */
export function pngFalso(bytes = 256): Buffer {
  return comCabecalho([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], bytes);
}

export function jpegFalso(bytes = 256): Buffer {
  return comCabecalho([0xff, 0xd8, 0xff, 0xe0], bytes);
}

export function pdfFalso(bytes = 256): Buffer {
  return comCabecalho([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37], bytes);
}

/** ZIP — é o que docx e xlsx são por dentro. */
export function docxFalso(bytes = 256): Buffer {
  return comCabecalho([0x50, 0x4b, 0x03, 0x04], bytes);
}

export function mp3Falso(bytes = 256): Buffer {
  return comCabecalho([0x49, 0x44, 0x33, 0x03], bytes);
}

/** WebM/Opus — o que Chrome e Edge produzem ao gravar voz. */
export function webmFalso(bytes = 256): Buffer {
  return comCabecalho([0x1a, 0x45, 0xdf, 0xa3], bytes);
}

/** Executável de Windows. O disfarce clássico: `foto.jpg.exe`. */
export function exeFalso(bytes = 256): Buffer {
  return comCabecalho([0x4d, 0x5a, 0x90, 0x00], bytes);
}
