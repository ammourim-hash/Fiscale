/**
 * A abstração de storage.
 *
 * ---------------------------------------------------------------------
 *  Por que uma interface, e não "usar o disco por enquanto"
 * ---------------------------------------------------------------------
 *  O Elo vai para um VPS, e mídia acaba em S3 ou R2. A diferença entre
 *  trocar isso em uma tarde e trocar num fim de semana inteiro é ter,
 *  desde o começo, um lugar só que sabe o que é um "arquivo guardado".
 *
 *  O contrato é deliberadamente pequeno — cinco operações. Cada método a
 *  mais é um método que o provedor seguinte precisa implementar, e a
 *  tentação é sempre acrescentar o que o provedor ATUAL faz de graça.
 *
 * ---------------------------------------------------------------------
 *  `getRange` não é luxo
 * ---------------------------------------------------------------------
 *  Áudio precisa de seek. Sem leitura por faixa, arrastar a barra de
 *  progresso obrigaria a baixar o arquivo inteiro de novo — e o navegador
 *  simplesmente não deixa arrastar num `<audio>` servido sem suporte a
 *  `Range`. Por isso a leitura parcial está no contrato, e não como um
 *  detalhe de quem implementa.
 */

export interface ObjetoGuardado {
  key: string;
  provider: string;
  sizeBytes: number;
  sha256: string;
}

export interface MetadadosDoObjeto {
  sizeBytes: number;
  contentType: string | null;
}

export interface FaixaLida {
  /** Os bytes pedidos — só eles. */
  corpo: Buffer;
  /** Início e fim EFETIVOS, já ajustados ao tamanho real. */
  inicio: number;
  fim: number;
  tamanhoTotal: number;
}

export interface StorageProvider {
  /** Nome curto, gravado na linha do anexo: "local", "s3"… */
  readonly nome: string;

  put(key: string, dados: Buffer, contentType: string): Promise<ObjetoGuardado>;

  get(key: string): Promise<Buffer>;

  /** Leitura parcial. `fim` é INCLUSIVO, como no cabeçalho HTTP `Range`. */
  getRange(key: string, inicio: number, fim: number): Promise<FaixaLida>;

  /** Idempotente: apagar o que já não existe não é erro. */
  delete(key: string): Promise<void>;

  /** `null` quando não existe — ausência não é exceção. */
  metadata(key: string): Promise<MetadadosDoObjeto | null>;

  /**
   * URL temporária, quando o provedor souber emitir uma.
   *
   * Devolve `null` no provedor local, e isso é uma resposta legítima: o
   * download passa pela rota autenticada do Elo, que é o caminho que
   * sempre funciona. Quem chama precisa lidar com `null` em vez de supor
   * que existe link — supor é como se acaba servindo arquivo por uma URL
   * pública permanente sem perceber.
   */
  signedUrl(key: string, segundos: number): Promise<string | null>;
}

/** Erro de storage. Separado para a mídia falhar sem derrubar o texto. */
export class StorageError extends Error {
  constructor(
    message: string,
    readonly operacao: string,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.name = "StorageError";
  }
}
