/**
 * O barramento do realtime.
 *
 * ---------------------------------------------------------------------
 *  O isolamento é a ESTRUTURA, não uma verificação
 * ---------------------------------------------------------------------
 *  Os inscritos ficam guardados num mapa cuja CHAVE é o tenantId:
 *
 *      Map<tenantId, Set<handler>>
 *
 *  `publish(tenantId, evento)` entrega ao conjunto daquela chave e a mais
 *  nenhum. Não existe "todos os inscritos" nem lista global para alguém
 *  varrer com um `if` errado — para vazar entre escritórios seria preciso
 *  reescrever esta função, e não esquecer uma condição.
 *
 *  E o tenantId que entra aqui nunca vem do navegador: quem se inscreve é
 *  a rota SSE, depois de `requireAuth(req)`, com o tenant da sessão
 *  validada. O parâmetro que o cliente controla é apenas a conversa que
 *  ele quer acompanhar — e isso só REDUZ o que ele recebe (ver a rota).
 *
 * ---------------------------------------------------------------------
 *  Por que um emissor em processo, e não Redis
 * ---------------------------------------------------------------------
 *  Um escritório, quinze pessoas, um processo Node. Redis resolveria o
 *  caso de duas instâncias, que não existe hoje — e um serviço a mais para
 *  manter, monitorar e ter no ar é custo real contra um ganho hipotético.
 *  A superfície trocada é pequena de propósito: `publish` e `subscribe`.
 *  Quando houver segunda instância, o adaptador entra aqui dentro e nada
 *  mais no projeto muda. Está registrado como débito.
 */
import { logger } from "@/server/logging/logger";

import type { EloEvent } from "./events";

export type Assinante = (evento: EloEvent) => void;

interface Barramento {
  porTenant: Map<string, Set<Assinante>>;
}

/**
 * Singleton amarrado ao globalThis — em desenvolvimento o Next recarrega
 * módulos a cada alteração, e um mapa novo por recarga deixaria conexões
 * abertas ouvindo um barramento que ninguém mais alimenta.
 */
const global = globalThis as unknown as { __eloBus?: Barramento };
const bus: Barramento = (global.__eloBus ??= { porTenant: new Map() });

export function publish(tenantId: string, evento: EloEvent): void {
  const inscritos = bus.porTenant.get(tenantId);
  if (!inscritos || inscritos.size === 0) return;

  for (const enviar of inscritos) {
    try {
      enviar(evento);
    } catch (erro: unknown) {
      // Uma conexão morta não pode derrubar a entrega das outras, e muito
      // menos a requisição que originou o evento.
      logger.warn("realtime.deliver_failed", { erro: String(erro) });
    }
  }
}

/** Devolve a função de cancelamento. Sem ela, aba fechada vira vazamento. */
export function subscribe(tenantId: string, assinante: Assinante): () => void {
  let inscritos = bus.porTenant.get(tenantId);
  if (!inscritos) {
    inscritos = new Set();
    bus.porTenant.set(tenantId, inscritos);
  }
  inscritos.add(assinante);

  return () => {
    const atual = bus.porTenant.get(tenantId);
    if (!atual) return;
    atual.delete(assinante);
    if (atual.size === 0) bus.porTenant.delete(tenantId);
  };
}

/** Quantas conexões este tenant tem abertas. Usado em teste e diagnóstico. */
export function subscriberCount(tenantId: string): number {
  return bus.porTenant.get(tenantId)?.size ?? 0;
}
