/**
 * Aplicacao da projecao de clientes.
 *
 * Idempotente por construcao: o mesmo payload aplicado duas vezes deixa o
 * banco no mesmo estado, e a segunda vez nao escreve nada. Quem decide
 * isso e o `contentHash` calculado pelo Elo — se bater com o guardado, a
 * linha e contada como `unchanged` e nem `updatedAt` se mexe.
 *
 * O que este arquivo NUNCA faz:
 *   - apagar cliente. Conversa futura vai apontar para esta linha, e
 *     historico com referencia quebrada e pior do que linha inativa.
 *   - inativar por ausencia. Registro que nao veio no full sync ganha
 *     `missingSince`; inativar de verdade so quando a origem disser
 *     `active: false`.
 *   - aceitar o tenant do payload. O tenant vem da identidade da
 *     integracao, e chega aqui como argumento.
 */
import { withTenant } from "@/server/tenancy";
import { logger } from "@/server/logging/logger";

import type { CustomerProjectionInput } from "./contract";

export type SyncMode = "FULL" | "INCREMENTAL";

export interface SyncCounters {
  received: number;
  created: number;
  updated: number;
  unchanged: number;
  reactivated: number;
  deactivated: number;
  markedMissing: number;
}

export interface ApplyResult extends SyncCounters {
  runId: string;
  finishedAt: Date;
}

const VAZIO: SyncCounters = {
  received: 0,
  created: 0,
  updated: 0,
  unchanged: 0,
  reactivated: 0,
  deactivated: 0,
  markedMissing: 0,
};

export async function applyProjection(
  tenantId: string,
  source: string,
  mode: SyncMode,
  clientes: CustomerProjectionInput[],
): Promise<ApplyResult> {
  const agora = new Date();
  const contagem: SyncCounters = { ...VAZIO, received: clientes.length };

  const runId = await withTenant(tenantId, async (tx) => {
    const run = await tx.syncRun.create({
      data: { tenantId, source, mode, received: clientes.length },
      select: { id: true },
    });
    return run.id;
  });

  for (const c of clientes) {
    // Um cliente por transacao. Lote inteiro numa transacao so faria uma
    // linha problematica desfazer o trabalho das outras 300 — e o Fiscale
    // teria de reenviar tudo.
    await withTenant(tenantId, async (tx) => {
      const atual = await tx.externalCustomerReference.findUnique({
        where: {
          tenantId_source_externalId: { tenantId, source, externalId: c.externalId },
        },
        select: { id: true, contentHash: true, active: true, missingSince: true },
      });

      if (!atual) {
        const criado = await tx.externalCustomerReference.create({
          data: {
            tenantId,
            source,
            externalId: c.externalId,
            displayName: c.displayName,
            documentDigits: c.documentDigits,
            documentKind: c.documentKind,
            email: c.email,
            active: c.active,
            deactivatedAt: c.active ? null : agora,
            sourceVersion: c.sourceVersion,
            contentHash: c.contentHash,
            sourceUpdatedAt: c.sourceUpdatedAt,
            syncedAt: agora,
          },
          select: { id: true },
        });

        if (c.phones.length > 0) {
          await tx.externalCustomerPhone.createMany({
            data: c.phones.map((t) => ({
              tenantId,
              customerId: criado.id,
              raw: t.raw,
              e164: t.e164,
              status: t.status,
            })),
          });
        }

        contagem.created += 1;
        return;
      }

      // Nada mudou: so registra que a origem confirmou este estado agora.
      // `syncedAt` e o "visto por ultimo", nao uma alteracao — por isso
      // ele se move mesmo em unchanged, e `updatedAt` nao.
      if (atual.contentHash === c.contentHash && atual.missingSince === null) {
        await tx.$executeRaw`
          UPDATE external_customer_references
             SET synced_at = ${agora}, source_version = ${c.sourceVersion}
           WHERE id = ${atual.id}::uuid
        `;
        contagem.unchanged += 1;
        return;
      }

      await tx.externalCustomerReference.update({
        where: { id: atual.id },
        data: {
          displayName: c.displayName,
          documentDigits: c.documentDigits,
          documentKind: c.documentKind,
          email: c.email,
          active: c.active,
          // Voltou a ativo: limpa a data. Acabou de ser inativado: marca
          // agora. Ja estava inativo: preserva a data original, que e
          // quando de fato saiu.
          ...(c.active
            ? { deactivatedAt: null }
            : atual.active
              ? { deactivatedAt: agora }
              : {}),
          sourceVersion: c.sourceVersion,
          contentHash: c.contentHash,
          sourceUpdatedAt: c.sourceUpdatedAt,
          syncedAt: agora,
          // Reapareceu: deixa de estar ausente.
          missingSince: null,
        },
      });

      // Telefones sao substituidos em bloco. Sao poucos por cliente, e
      // reconciliar item a item traria estado intermediario inconsistente
      // no meio da transacao sem ganho nenhum.
      await tx.externalCustomerPhone.deleteMany({ where: { customerId: atual.id } });
      if (c.phones.length > 0) {
        await tx.externalCustomerPhone.createMany({
          data: c.phones.map((t) => ({
            tenantId,
            customerId: atual.id,
            raw: t.raw,
            e164: t.e164,
            status: t.status,
          })),
        });
      }

      contagem.updated += 1;
      if (!atual.active && c.active) contagem.reactivated += 1;
      if (atual.active && !c.active) contagem.deactivated += 1;
    });
  }

  // Full sync: quem nao veio fica MARCADO como ausente, nao apagado nem
  // inativado. Ausencia pode ser exclusao na origem, mas tambem filtro
  // novo ou exportacao pela metade — e as tres se parecem daqui.
  if (mode === "FULL") {
    const presentes = clientes.map((c) => c.externalId);
    contagem.markedMissing = await withTenant(tenantId, async (tx) => {
      const r = await tx.externalCustomerReference.updateMany({
        where: {
          source,
          missingSince: null,
          ...(presentes.length > 0 ? { externalId: { notIn: presentes } } : {}),
        },
        data: { missingSince: agora },
      });
      return r.count;
    });
  }

  const finishedAt = new Date();
  await withTenant(tenantId, async (tx) => {
    await tx.syncRun.update({
      where: { id: runId },
      data: { ...contagem, finishedAt, status: "OK" },
    });
  });

  logger.info("customers.sync.applied", { tenantId, source, mode, runId, ...contagem });

  return { ...contagem, runId, finishedAt };
}

export async function failRun(tenantId: string, runId: string): Promise<void> {
  await withTenant(tenantId, async (tx) => {
    await tx.syncRun.updateMany({
      where: { id: runId },
      data: { status: "FAILED", finishedAt: new Date() },
    });
  });
}
