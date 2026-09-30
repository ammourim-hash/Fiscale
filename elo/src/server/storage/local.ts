/**
 * Storage em disco — desenvolvimento.
 *
 * ---------------------------------------------------------------------
 *  Por que disco e não MinIO
 * ---------------------------------------------------------------------
 *  MinIO daria fidelidade com S3, e custaria mais um container para subir,
 *  monitorar e manter no `docker compose` de quem só quer rodar o projeto.
 *  A fidelidade que importa está na INTERFACE, não no provedor: se o
 *  contrato de `StorageProvider` estiver certo, trocar por S3 é escrever
 *  uma classe nova e mudar uma variável de ambiente.
 *
 *  O que este provedor deliberadamente NÃO faz — e o de S3 fará — é emitir
 *  URL assinada. Devolve `null`, e o download passa pela rota autenticada.
 *
 * ---------------------------------------------------------------------
 *  A pasta fica FORA do projeto servido
 * ---------------------------------------------------------------------
 *  Nada de `public/`. Arquivo em `public/` vira URL pública permanente, que
 *  é exatamente o que não pode existir: nota fiscal de cliente não fica
 *  acessível a quem adivinhar o caminho. O padrão é `.storage/` na raiz do
 *  Elo, ignorada pelo git.
 *
 *  E `resolverCaminho` recusa qualquer chave que escape da raiz. Sem isso,
 *  uma chave com `../` leria arquivo do sistema — travessia de caminho é
 *  o defeito clássico de storage em disco.
 */
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { access, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { open } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";

import {
  StorageError,
  type FaixaLida,
  type MetadadosDoObjeto,
  type ObjetoGuardado,
  type StorageProvider,
} from "./provider";

export class LocalStorageProvider implements StorageProvider {
  readonly nome = "local";
  private readonly raiz: string;

  constructor(raiz: string) {
    this.raiz = resolve(raiz);
  }

  /**
   * Chave -> caminho absoluto, recusando fuga da raiz.
   *
   * A verificação é feita no caminho JÁ resolvido: comparar strings antes
   * de resolver não pega `a/../../b`, que é justamente o caso que
   * interessa.
   */
  private resolverCaminho(key: string): string {
    if (key.length === 0) throw new StorageError("chave vazia", "resolver");

    const alvo = resolve(this.raiz, key);
    if (alvo !== this.raiz && !alvo.startsWith(this.raiz + sep)) {
      throw new StorageError("chave fora da raiz do storage", "resolver");
    }
    return alvo;
  }

  async put(key: string, dados: Buffer, _contentType: string): Promise<ObjetoGuardado> {
    const caminho = this.resolverCaminho(key); // fora do try — ver `get`
    try {
      await mkdir(dirname(caminho), { recursive: true });
      await writeFile(caminho, dados, { flag: "wx" });
    } catch (erro: unknown) {
      throw new StorageError(`falha ao gravar ${key}`, "put", { cause: erro });
    }

    return {
      key,
      provider: this.nome,
      sizeBytes: dados.byteLength,
      sha256: createHash("sha256").update(dados).digest("hex"),
    };
  }

  async get(key: string): Promise<Buffer> {
    // FORA do try, de proposito: a recusa por fuga da raiz e uma decisao
    // de seguranca, e reembalar como "falha ao ler" a esconderia no log e
    // apagaria a diferenca entre "chave maliciosa" e "arquivo sumiu".
    const caminho = this.resolverCaminho(key);
    try {
      return await readFile(caminho);
    } catch (erro: unknown) {
      throw new StorageError(`falha ao ler ${key}`, "get", { cause: erro });
    }
  }

  /**
   * Lê só o pedaço pedido, sem carregar o arquivo inteiro na memória — que
   * seria fingir suporte a `Range` gastando a mesma RAM de sempre.
   */
  async getRange(key: string, inicio: number, fim: number): Promise<FaixaLida> {
    const caminho = this.resolverCaminho(key);

    let arquivo;
    try {
      arquivo = await open(caminho, "r");
    } catch (erro: unknown) {
      throw new StorageError(`falha ao abrir ${key}`, "getRange", { cause: erro });
    }

    try {
      const info = await arquivo.stat();
      const total = info.size;

      const de = Math.max(0, Math.min(inicio, total === 0 ? 0 : total - 1));
      const ate = Math.max(de, Math.min(fim, total === 0 ? 0 : total - 1));
      const quantos = total === 0 ? 0 : ate - de + 1;

      const buffer = Buffer.alloc(quantos);
      if (quantos > 0) await arquivo.read(buffer, 0, quantos, de);

      return { corpo: buffer, inicio: de, fim: ate, tamanhoTotal: total };
    } catch (erro: unknown) {
      throw new StorageError(`falha ao ler faixa de ${key}`, "getRange", { cause: erro });
    } finally {
      await arquivo.close();
    }
  }

  async delete(key: string): Promise<void> {
    // `force` faz "não existe" deixar de ser erro — apagar duas vezes é o
    // caso normal de uma faxina que roda de novo.
    await rm(this.resolverCaminho(key), { force: true });
  }

  async metadata(key: string): Promise<MetadadosDoObjeto | null> {
    const caminho = this.resolverCaminho(key);
    try {
      await access(caminho, constants.R_OK);
    } catch {
      return null;
    }
    const info = await stat(caminho);
    // O disco não guarda content-type. Quem sabe o tipo é a linha do
    // anexo, e é de lá que o download tira o cabeçalho.
    return { sizeBytes: info.size, contentType: null };
  }

  /**
   * Sempre `null`: disco nao emite URL assinada.
   *
   * Os parametros existem para respeitar o contrato — quem programa
   * contra `StorageProvider` chama com eles, e o provedor de S3 vai
   * usa-los.
   */
  async signedUrl(_key: string, _segundos: number): Promise<string | null> {
    return null;
  }
}
