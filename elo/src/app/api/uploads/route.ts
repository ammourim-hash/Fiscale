/**
 * POST /api/uploads — recebe o arquivo, devolve um id.
 *
 * ---------------------------------------------------------------------
 *  Por que upload direto ao backend, e não URL assinada
 * ---------------------------------------------------------------------
 *  A URL assinada é mais escalável e, para este MVP, é o desenho errado:
 *  ela entrega os bytes ao storage SEM passar pelo Elo, e é justamente o
 *  Elo que precisa olhar os magic bytes antes de o arquivo existir. Com
 *  upload direto ao bucket, a validação de conteúdo viraria um passo
 *  depois — sobre um objeto que já está lá.
 *
 *  Quinze pessoas, arquivos de dezenas de MB: passar pelo processo é
 *  barato. Quando não for, entra URL assinada + validação no callback, e
 *  a decisão fica documentada.
 *
 *  Dois estágios (upload e depois a mensagem) porque o envio precisa ser
 *  idempotente: o `clientMessageId` cuida da mensagem, e o `uploadId`
 *  cuida do arquivo. Um retry reenvia os MESMOS ids e não duplica nada.
 */
import { NextResponse, type NextRequest } from "next/server";

import { requireAuth } from "@/server/auth/request";
import { receberUpload, talvezFaxinar } from "@/server/media/service";
import { limiteAbsoluto } from "@/server/media/tipos";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function numeroOuNulo(v: FormDataEntryValue | null): number | null {
  if (typeof v !== "string") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);

    const forma = await req.formData().catch(() => null);
    if (!forma) throw new AppError("VALIDATION", "corpo não é multipart");

    const arquivo = forma.get("file");
    if (!(arquivo instanceof File)) {
      throw new AppError("VALIDATION", "campo `file` ausente");
    }

    // Corte grosseiro ANTES de ler os bytes para a memória. Sem isto, um
    // arquivo de 2 GB seria carregado só para depois ser recusado.
    if (arquivo.size > limiteAbsoluto()) {
      throw new AppError("VALIDATION", `arquivo acima do limite (${arquivo.size} bytes)`, {
        publicMessage: "Arquivo grande demais.",
      });
    }

    const dados = Buffer.from(await arquivo.arrayBuffer());

    const r = await receberUpload(ctx, {
      nome: arquivo.name,
      mimeDeclarado: arquivo.type,
      dados,
      gravacaoDeVoz: forma.get("voice") === "1",
      durationMs: numeroOuNulo(forma.get("durationMs")),
      width: numeroOuNulo(forma.get("width")),
      height: numeroOuNulo(forma.get("height")),
    });

    if (!r.ok) {
      // Storage fora do ar é indisponibilidade, não erro de quem enviou.
      throw r.motivo === "STORAGE"
        ? new AppError("DATABASE", r.message, { publicMessage: r.message })
        : new AppError("VALIDATION", r.message, { publicMessage: r.message });
    }

    // Aproveita a passagem para recolher o que ficou para trás.
    talvezFaxinar(ctx.tenantId);

    return NextResponse.json(r.value, {
      headers: { "cache-control": "no-store", "x-request-id": requestId },
    });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
