/**
 * Upload, vínculo e faxina de anexos.
 *
 * ---------------------------------------------------------------------
 *  Banco e storage não compartilham transação
 * ---------------------------------------------------------------------
 *  Não existe `BEGIN` que cubra o Postgres e o disco (ou o S3). Então o
 *  fluxo é feito para que a falha em qualquer ponto deixe lixo — nunca
 *  buraco:
 *
 *    1. grava os BYTES no storage;
 *    2. grava a LINHA como PENDING, com prazo de validade;
 *    3. a mensagem é criada e ADOTA o anexo (PENDING -> ATTACHED), na
 *       mesma transação em que a mensagem nasce;
 *    4. o que não foi adotado até o prazo é apagado — linha e bytes.
 *
 *  A ordem importa. Gravar a linha antes dos bytes daria uma linha
 *  apontando para arquivo que não existe: a tela mostraria um anexo
 *  quebrado, que é pior do que um arquivo esquecido no disco.
 *
 *  Se o passo 2 falhar depois do 1, o arquivo é apagado ali mesmo, na
 *  compensação — e se ATÉ ISSO falhar, ele fica sem linha nenhuma e vira
 *  assunto do débito registrado no README (a faxina varre pela tabela, e
 *  arquivo sem linha ela não vê).
 */
import { createHash } from "node:crypto";

import { audit } from "@/server/auth/audit";
import { requirePermission, type AuthContext } from "@/server/auth/context";
import { logger } from "@/server/logging/logger";
import { montarChave, storage } from "@/server/storage";
import { withTenant } from "@/server/tenancy";
import type { MessageType } from "@/generated/prisma/enums";

import {
  avaliarArquivo,
  mensagemDeRecusa,
  type ArquivoRecebido,
  type MotivoRecusa,
} from "./tipos";

/** Quanto tempo um upload pode ficar sem virar mensagem. */
const VALIDADE_PENDENTE_MS = 2 * 60 * 60 * 1000;

export interface AnexoCriado {
  id: string;
  type: MessageType;
  originalFileName: string;
  mimeType: string;
  sizeBytes: number;
  durationMs: number | null;
  width: number | null;
  height: number | null;
}

export type ResultadoUpload =
  | { ok: true; value: AnexoCriado }
  | { ok: false; motivo: MotivoRecusa | "STORAGE"; message: string };

export interface OpcoesUpload extends ArquivoRecebido {
  /** Medida pelo navegador: evita baixar o áudio inteiro para saber "0:37". */
  durationMs?: number | null;
  width?: number | null;
  height?: number | null;
}

/* ─── 1 e 2: guardar ─────────────────────────────────────────────────── */

export async function receberUpload(
  ctx: AuthContext,
  arquivo: OpcoesUpload,
): Promise<ResultadoUpload> {
  requirePermission(ctx, "messages.send");

  const avaliacao = avaliarArquivo(arquivo);
  if (!avaliacao.ok) {
    logger.warn("media.upload.rejeitado", {
      tenantId: ctx.tenantId,
      motivo: avaliacao.motivo,
      detalhe: avaliacao.detalhe,
    });
    return {
      ok: false,
      motivo: avaliacao.motivo,
      message: mensagemDeRecusa(avaliacao.motivo, avaliacao.detalhe),
    };
  }

  const chave = montarChave(ctx.tenantId);
  const provedor = storage();

  // Passo 1: os bytes.
  try {
    await provedor.put(chave, arquivo.dados, avaliacao.mimeType);
  } catch (erro: unknown) {
    // Storage fora do ar não pode derrubar a conversa: quem falha é o
    // envio de mídia, com mensagem própria, e o texto continua indo.
    logger.error("media.storage.indisponivel", { tenantId: ctx.tenantId, erro: String(erro) });
    return {
      ok: false,
      motivo: "STORAGE",
      message: "Não foi possível guardar o arquivo agora. Tente de novo em instantes.",
    };
  }

  const sha256 = createHash("sha256").update(arquivo.dados).digest("hex");

  // Passo 2: a linha, já com prazo.
  try {
    const anexo = await withTenant(ctx.tenantId, async (tx) =>
      tx.messageAttachment.create({
        data: {
          tenantId: ctx.tenantId,
          type: avaliacao.tipo,
          storageProvider: provedor.nome,
          storageKey: chave,
          originalFileName: avaliacao.nome,
          mimeType: avaliacao.mimeType,
          sizeBytes: arquivo.dados.byteLength,
          sha256,
          status: "PENDING",
          expiresAt: new Date(Date.now() + VALIDADE_PENDENTE_MS),
          ...(arquivo.durationMs != null ? { durationMs: Math.round(arquivo.durationMs) } : {}),
          ...(arquivo.width != null ? { width: Math.round(arquivo.width) } : {}),
          ...(arquivo.height != null ? { height: Math.round(arquivo.height) } : {}),
        },
        select: {
          id: true,
          type: true,
          originalFileName: true,
          mimeType: true,
          sizeBytes: true,
          durationMs: true,
          width: true,
          height: true,
        },
      }),
    );

    await audit({
      tenantId: ctx.tenantId,
      membershipId: ctx.membershipId,
      action: "ATTACHMENT_UPLOADED",
      targetType: "attachment",
      targetId: anexo.id,
      // Nome e tamanho, nunca o conteúdo.
      metadata: { tipo: anexo.type, bytes: anexo.sizeBytes },
    });

    return { ok: true, value: anexo };
  } catch (erro: unknown) {
    // Compensação: a linha não existe, então os bytes não podem ficar.
    await provedor.delete(chave).catch(() => {
      logger.error("media.compensacao.falhou", { chave });
    });
    throw erro;
  }
}

/* ─── 3: adoção pela mensagem ────────────────────────────────────────── */

export interface AnexosParaMensagem {
  ids: string[];
  /** O tipo da mensagem sai do primeiro anexo; texto puro continua TEXT. */
  tipo: MessageType;
}

/**
 * Marca os anexos como pertencentes a uma mensagem.
 *
 * Roda DENTRO da transação que cria a mensagem — por isso recebe o `tx`.
 * Fora dela, uma falha depois do insert da mensagem deixaria mensagem sem
 * anexo, que é o caso "buraco" que este desenho evita.
 *
 * O `where` exige `status: PENDING` e `messageId: null`: anexo já usado
 * não é reaproveitado por outra mensagem, nem por um retry malicioso.
 */
export async function vincularAnexos(
  tx: Parameters<Parameters<typeof withTenant>[1]>[0],
  tenantId: string,
  messageId: string,
  ids: string[],
): Promise<number> {
  if (ids.length === 0) return 0;

  const r = await tx.messageAttachment.updateMany({
    where: { id: { in: ids }, tenantId, messageId: null, status: "PENDING" },
    data: { messageId, status: "ATTACHED", expiresAt: null },
  });
  return r.count;
}

/**
 * Lê os anexos pendentes para decidir o tipo da mensagem antes de criá-la.
 *
 * Uma mensagem com uma imagem é `IMAGE`; com um áudio gravado, `VOICE`.
 * Com mais de um anexo de tipos diferentes, vence o primeiro — e a
 * interface envia um anexo por vez, então o caso é teórico.
 */
export async function tipoDosAnexos(
  tx: Parameters<Parameters<typeof withTenant>[1]>[0],
  tenantId: string,
  ids: string[],
): Promise<MessageType | null> {
  if (ids.length === 0) return null;
  const anexos = await tx.messageAttachment.findMany({
    where: { id: { in: ids }, tenantId, messageId: null, status: "PENDING" },
    select: { type: true },
    orderBy: { createdAt: "asc" },
  });
  return anexos[0]?.type ?? null;
}

/* ─── 4: faxina ──────────────────────────────────────────────────────── */

export interface ResultadoFaxina {
  apagados: number;
  falhas: number;
}

/**
 * Apaga o que subiu e nunca virou mensagem — DENTRO de um tenant.
 *
 * Recebe o tenant porque `message_attachments` tem RLS forcado: sem
 * contexto, a consulta nao devolve linha nenhuma e a faxina varreria o
 * vazio para sempre, dizendo que esta tudo limpo. Isso aconteceu de
 * verdade na primeira versao deste codigo, e o teste pegou.
 *
 * A alternativa seria um papel com BYPASSRLS so para a faxina. Recusada:
 * abriria no projeto exatamente a porta que o MVP 1.0 fechou, e abriria
 * para sempre, por causa de uma tarefa de manutencao.
 *
 * A consequencia aceita: quem faxina precisa saber de qual escritorio.
 * Na pratica ha sempre um — o upload sabe o seu, e o script recebe por
 * argumento.
 *
 * Apaga os BYTES primeiro. Na ordem inversa, uma falha no meio deixaria
 * arquivo sem linha — invisivel para a faxina seguinte.
 */
export async function limparAnexosOrfaos(
  tenantId: string,
  agora = new Date(),
): Promise<ResultadoFaxina> {
  const provedor = storage();

  const orfaos = await withTenant(tenantId, async (tx) =>
    tx.messageAttachment.findMany({
      where: { status: "PENDING", expiresAt: { lt: agora } },
      select: { id: true, storageKey: true },
      take: 500,
    }),
  );

  let apagados = 0;
  let falhas = 0;

  for (const o of orfaos) {
    try {
      await provedor.delete(o.storageKey);
      await withTenant(tenantId, async (tx) => {
        await tx.messageAttachment.delete({ where: { id: o.id } });
      });
      apagados += 1;
    } catch (erro: unknown) {
      // A linha fica para a proxima passagem. Insistir agora so
      // transformaria um storage instavel em laco de erro.
      falhas += 1;
      logger.warn("media.faxina.falhou", { id: o.id, erro: String(erro) });
    }
  }

  if (apagados > 0 || falhas > 0) {
    logger.info("media.faxina", { tenantId, apagados, falhas });
  }
  return { apagados, falhas };
}

/** Trava simples para a faxina oportunista não rodar a cada upload. */
const faxina = globalThis as unknown as { eloUltimaFaxina?: number };
const INTERVALO_FAXINA_MS = 15 * 60 * 1000;

export function talvezFaxinar(tenantId: string): void {
  const agora = Date.now();
  if (faxina.eloUltimaFaxina && agora - faxina.eloUltimaFaxina < INTERVALO_FAXINA_MS) return;
  faxina.eloUltimaFaxina = agora;

  // Deliberadamente sem `await`: a faxina nao pode atrasar o upload de
  // quem esta esperando na tela.
  void limparAnexosOrfaos(tenantId).catch((erro: unknown) =>
    logger.warn("media.faxina.erro", { erro: String(erro) }),
  );
}
