/**
 * GET  /api/webhooks/whatsapp   verificação (handshake da Meta)
 * POST /api/webhooks/whatsapp   eventos
 *
 * =====================================================================
 *  A ÚNICA rota do Elo sem sessão — e por isso a mais cuidadosa
 * =====================================================================
 *  Todo o resto do sistema exige `requireAuth`. Esta não pode: quem chama
 *  é a Meta, que não tem cookie nosso. O que substitui a sessão é a
 *  assinatura `X-Hub-Signature-256`, e ela é conferida ANTES de o payload
 *  virar qualquer coisa.
 *
 *  A ordem importa e está escrita como código:
 *
 *      1. ler o corpo CRU (sem `JSON.parse` no caminho)
 *      2. conferir a assinatura contra o App Secret
 *      3. só então interpretar
 *      4. resolver o tenant pela CONFIGURAÇÃO, nunca pelo payload
 *      5. gravar pelos serviços que já existem
 *
 *  Payload sem assinatura válida **não entra no domínio** e não vira log
 *  com o conteúdo dentro — vira contador. Guardar o corpo do que um
 *  atacante mandou é como se constrói um arquivo do ataque alheio.
 *
 * =====================================================================
 *  Por que quase tudo responde 200
 * =====================================================================
 *  A Meta REENTREGA por dias o que não recebeu 200, e desativa o webhook
 *  depois de muita falha. Então:
 *
 *      assinatura inválida       → **403**, e nada é processado
 *      número que não é nosso    → 200, ignorado e contado
 *      tipo que não tratamos     → 200, ignorado e contado
 *      erro nosso ao gravar      → 200, registrado no log
 *
 *  Devolver 500 num defeito nosso faria a Meta reenviar o mesmo evento
 *  por dias — e a idempotência absorveria, mas o barulho seria enorme. O
 *  403 na assinatura é diferente: ali NÃO queremos reentrega, queremos
 *  que pare.
 */
import { NextResponse, type NextRequest } from "next/server";

import { audit } from "@/server/auth/audit";
import {
  appSecret,
  estadoDoRecebimento,
  numerosConfigurados,
  tenantDoNumero,
  verifyToken,
} from "@/server/channels/whatsapp/config";
import { processar } from "@/server/channels/whatsapp/inbound";
import { interpretar } from "@/server/channels/whatsapp/payload";
import { conferirAssinatura } from "@/server/channels/whatsapp/signature";
import { logger, newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/* ─── verificação ────────────────────────────────────────────────────── */

/**
 * O handshake. A Meta chama uma vez, ao salvar a URL no painel.
 *
 * Devolve o `hub.challenge` **em texto puro** — a Meta compara byte a
 * byte, e um JSON aqui faz a verificação falhar sem dizer por quê.
 */
export function GET(req: NextRequest): NextResponse {
  const params = new URL(req.url).searchParams;
  const modo = params.get("hub.mode");
  const token = params.get("hub.verify_token");
  const desafio = params.get("hub.challenge");

  const esperado = verifyToken();

  // Sem token configurado, ninguém verifica nada. Falha fechada: aceitar
  // o handshake com o segredo vazio ligaria o webhook de qualquer um.
  if (!esperado || modo !== "subscribe" || token !== esperado || !desafio) {
    logger.warn("whatsapp.webhook.verificacao_recusada", {
      modo: modo ?? null,
      temToken: Boolean(token),
    });
    return new NextResponse("forbidden", { status: 403 });
  }

  logger.info("whatsapp.webhook.verificado", {});
  return new NextResponse(desafio, {
    status: 200,
    headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
  });
}

/* ─── eventos ────────────────────────────────────────────────────────── */

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const log = logger.child({ requestId });

  // 1. O corpo CRU. O HMAC é sobre estes bytes: ler como JSON e
  //    reserializar muda espaço e escape, e a assinatura deixa de bater
  //    por motivo nenhum.
  const corpoCru = await req.text().catch(() => "");

  // 2. Assinatura, antes de qualquer interpretação.
  const assinatura = conferirAssinatura(
    corpoCru,
    req.headers.get("x-hub-signature-256"),
    appSecret(),
  );

  if (!assinatura.valida) {
    // O MOTIVO vai para o log; o CORPO não. Quem manda payload forjado
    // não vai ter o próprio texto arquivado no nosso banco.
    log.warn("whatsapp.webhook.assinatura_invalida", { motivo: assinatura.motivo });

    // Auditoria só quando há segredo configurado: sem ele, todo tráfego
    // de sondagem viraria linha de auditoria sem tenant a que pertencer.
    if (assinatura.motivo === "NAO_CONFERE") {
      const alvo = primeiroTenantConfigurado();
      if (alvo) {
        await audit({
          tenantId: alvo,
          membershipId: null,
          action: "WHATSAPP_WEBHOOK_REJECTED",
          metadata: { motivo: assinatura.motivo },
        });
      }
    }

    return NextResponse.json({ erro: "assinatura inválida" }, { status: 403 });
  }

  const estado = estadoDoRecebimento();
  if (!estado.pronto) {
    // Assinatura boa mas configuração incompleta: 200 para a Meta não
    // reentregar em cima de um problema que é nosso e não se resolve
    // sozinho.
    log.error("whatsapp.webhook.nao_configurado", { motivo: estado.motivo });
    return NextResponse.json({ ok: true }, { status: 200 });
  }

  // 3. Interpretar. Nada aqui lança: o que não se entende vira contagem.
  let bruto: unknown = null;
  try {
    bruto = JSON.parse(corpoCru);
  } catch {
    log.warn("whatsapp.webhook.json_invalido", {});
    return NextResponse.json({ ok: true }, { status: 200 });
  }

  const { eventos, ignorados } = interpretar(bruto);

  // 4 e 5. Tenant pela configuração, gravação pelos serviços de sempre.
  try {
    const r = await processar(eventos, tenantDoNumero);

    log.info("whatsapp.webhook.processado", {
      eventos: eventos.length,
      criadas: r.criadas,
      duplicadas: r.duplicadas,
      recusadas: r.recusadas,
      ...(ignorados.length > 0
        ? { ignorados: ignorados.map((i) => `${i.motivo}:${i.quantidade}`).join(",") }
        : {}),
    });
  } catch (erro: unknown) {
    // Defeito nosso. 200 mesmo assim — ver o cabeçalho.
    log.error("whatsapp.webhook.falhou", { erro: String(erro) });
  }

  return NextResponse.json({ ok: true }, { status: 200, headers: { "cache-control": "no-store" } });
}

/**
 * Um tenant para pendurar a auditoria de webhook forjado.
 *
 * A trilha é por tenant, e um payload recusado ainda não tem dono — a
 * assinatura falhou justamente antes de descobrir de quem ele seria. Com
 * um número configurado, que é o caso desta fase, o dono é evidente.
 */
function primeiroTenantConfigurado(): string | null {
  const todos = numerosConfigurados();
  // Com mais de um numero nao da para saber de quem seria o payload
  // recusado — e chutar um tenant para pendurar auditoria seria pior do
  // que nao registrar. O log fica de qualquer forma.
  return todos.length === 1 ? (todos[0]?.tenantId ?? null) : null;
}
