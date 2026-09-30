/**
 * GET  /api/team    a equipe, as áreas e a cobertura
 * POST /api/team    cadastra uma pessoa
 *
 *   GET   users.read     OWNER, ADMIN, MANAGER e AGENT
 *   POST  users.manage   OWNER e ADMIN
 *
 * O item "Equipe" some do menu de quem não administra — e isso NÃO é a
 * autorização. Esconder item de menu nunca foi autorização; quem chamar
 * esta rota direto leva 403 pelo `requirePermission`, exatamente como
 * levaria sem nunca ter visto a tela.
 *
 * O GET devolve as três coisas numa resposta só porque a tela precisa das
 * três ao abrir: a lista, as áreas (para o formulário) e a cobertura (para
 * o topo). Três chamadas dariam três estados de carregamento para o mesmo
 * instante.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { ValidationError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import {
  coberturaDosSetores,
  criarPessoa,
  listarDepartamentos,
  listarEquipe,
} from "@/server/team/service";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const PAPEIS = ["OWNER", "ADMIN", "MANAGER", "AGENT", "VIEWER"] as const;

const novaPessoa = z.object({
  name: z.string().trim().min(2).max(120),
  email: z.string().trim().email().max(180),
  // O login do FISCALE, e não o e-mail: é por ele que a troca de bilhete
  // encontra a pessoa (`exchange.ts`).
  fiscaleUid: z.string().trim().min(1).max(120),
  role: z.enum(PAPEIS),
  active: z.boolean().optional(),
  availableForAssignment: z.boolean().optional(),
  visibleToCustomers: z.boolean().optional(),
  departmentIds: z.array(z.string().uuid()).max(50).optional(),
  primaryDepartmentId: z.string().uuid().nullable().optional(),
});

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    // `listarEquipe` e `coberturaDosSetores` exigem users.read;
    // `listarDepartamentos` exige departments.read. Cada uma cobra a sua.
    const [people, departments, coverage] = await Promise.all([
      listarEquipe(ctx),
      listarDepartamentos(ctx),
      coberturaDosSetores(ctx),
    ]);

    return NextResponse.json({ people, departments, coverage }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    const corpo = novaPessoa.safeParse(await req.json().catch(() => null));
    if (!corpo.success) {
      throw new ValidationError("pessoa invalida", {
        issues: corpo.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`),
      });
    }

    const person = await criarPessoa(ctx, corpo.data);

    return NextResponse.json({ person }, { status: 201, headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
