/**
 * GET /api/attachments/:id — os bytes, para quem pode.
 *
 * ---------------------------------------------------------------------
 *  Não existe URL pública
 * ---------------------------------------------------------------------
 *  Guia de cliente, contrato, foto de documento. Nada disso pode ficar
 *  acessível a quem adivinhar um caminho. Toda leitura passa por aqui:
 *  sessão válida, permissão, e o RLS decidindo se a linha existe para
 *  este tenant. A `storageKey` nunca sai — ela só é lida depois que o
 *  banco já disse que o anexo é seu.
 *
 * ---------------------------------------------------------------------
 *  `Content-Disposition`: quando abrir, quando baixar
 * ---------------------------------------------------------------------
 *  `inline` só para o que o navegador exibe com segurança: imagem e
 *  áudio. Documento vai como `attachment` — PDF aberto no visualizador
 *  interno é script rodando na NOSSA origem, e não há motivo para
 *  conceder isso a um arquivo que chegou de fora.
 *
 *  Junto vai `X-Content-Type-Options: nosniff`: sem ele, o navegador pode
 *  "corrigir" o tipo declarado e executar como HTML o que dissemos ser
 *  outra coisa.
 *
 * ---------------------------------------------------------------------
 *  `Range`
 * ---------------------------------------------------------------------
 *  Arrastar a barra do áudio depende disso. Sem `206 Partial Content` e
 *  `Accept-Ranges`, o `<audio>` do navegador simplesmente não deixa
 *  buscar posição — e a barra de progresso vira enfeite.
 */
import type { NextRequest } from "next/server";

import { requireAuth } from "@/server/auth/request";
import { requirePermission } from "@/server/auth/context";
import { nomeSeguro } from "@/server/media/tipos";
import { storage } from "@/server/storage";
import { withTenant } from "@/server/tenancy";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { logger, newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Tipos que podem ser exibidos na própria página sem executar nada. */
const EXIBIVEIS = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "audio/mpeg",
  "audio/mp4",
  "audio/aac",
  "audio/ogg",
  "audio/opus",
  "audio/wav",
  "audio/webm",
]);

/** `bytes=0-1023` -> `{inicio, fim}`. `null` quando não há ou não dá. */
function lerRange(cabecalho: string | null, total: number): { inicio: number; fim: number } | null {
  if (!cabecalho) return null;
  const m = /^bytes=(\d*)-(\d*)$/.exec(cabecalho.trim());
  if (!m) return null;

  const [, cru1 = "", cru2 = ""] = m;

  // `bytes=-500` = os últimos 500.
  if (cru1 === "") {
    if (cru2 === "") return null;
    const ultimos = Number(cru2);
    if (!Number.isFinite(ultimos) || ultimos <= 0) return null;
    return { inicio: Math.max(0, total - ultimos), fim: total - 1 };
  }

  const inicio = Number(cru1);
  if (!Number.isFinite(inicio) || inicio < 0) return null;
  const fim = cru2 === "" ? total - 1 : Number(cru2);
  if (!Number.isFinite(fim) || fim < inicio) return null;

  return { inicio, fim: Math.min(fim, total - 1) };
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);
    requirePermission(ctx, "messages.read");

    const { id } = await params;

    // O RLS decide se existe. Anexo de outro tenant não é 403: é 404 —
    // confirmar a existência já seria contar algo.
    const anexo = await withTenant(ctx.tenantId, async (tx) =>
      tx.messageAttachment.findUnique({
        where: { id },
        select: {
          storageKey: true,
          mimeType: true,
          originalFileName: true,
          sizeBytes: true,
          status: true,
          scanStatus: true,
        },
      }),
    );

    if (!anexo) throw new AppError("NOT_FOUND", "anexo não encontrado");

    if (anexo.scanStatus === "BLOCKED") {
      // O gancho de antivírus: nada a implementar aqui além de recusar.
      throw new AppError("FORBIDDEN", "anexo bloqueado pela análise", {
        publicMessage: "Este arquivo foi bloqueado por segurança.",
      });
    }

    const provedor = storage();
    const disposicao = EXIBIVEIS.has(anexo.mimeType) ? "inline" : "attachment";
    const nome = nomeSeguro(anexo.originalFileName);

    const comuns: Record<string, string> = {
      "content-type": anexo.mimeType,
      // `filename*` em UTF-8 preserva acento; o `filename` simples é a
      // reserva para cliente antigo.
      "content-disposition": `${disposicao}; filename="${nome}"; filename*=UTF-8''${encodeURIComponent(nome)}`,
      "x-content-type-options": "nosniff",
      "accept-ranges": "bytes",
      // Privado: nenhum proxy compartilhado pode guardar isto.
      "cache-control": "private, max-age=0, must-revalidate",
      "x-request-id": requestId,
    };

    const faixa = lerRange(req.headers.get("range"), anexo.sizeBytes);

    if (faixa) {
      if (faixa.inicio >= anexo.sizeBytes) {
        return new Response(null, {
          status: 416,
          headers: { ...comuns, "content-range": `bytes */${anexo.sizeBytes}` },
        });
      }

      const parte = await provedor.getRange(anexo.storageKey, faixa.inicio, faixa.fim);
      return new Response(new Uint8Array(parte.corpo), {
        status: 206,
        headers: {
          ...comuns,
          "content-range": `bytes ${parte.inicio}-${parte.fim}/${parte.tamanhoTotal}`,
          "content-length": String(parte.corpo.byteLength),
        },
      });
    }

    const bytes = await provedor.get(anexo.storageKey);
    return new Response(new Uint8Array(bytes), {
      status: 200,
      headers: { ...comuns, "content-length": String(bytes.byteLength) },
    });
  } catch (erro) {
    // Storage fora do ar devolve erro só desta mídia. A conversa, o texto
    // e o resto da tela continuam de pé.
    if (erro instanceof Error && erro.name === "StorageError") {
      logger.error("media.download.storage", { erro: erro.message });
      return errorResponse(
        new AppError("DATABASE", erro.message, {
          publicMessage: "Arquivo indisponível no momento.",
        }),
        { requestId },
      );
    }
    return errorResponse(erro, { requestId });
  }
}
