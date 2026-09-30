/**
 * Fundacao: saude, log e erro. Coisas pequenas, mas que definem o que o
 * sistema conta ao mundo quando algo da errado.
 */
import { afterAll, describe, expect, it, vi } from "vitest";

import { checkDatabase } from "@/server/db/health";
import {
  AppError,
  DatabaseError,
  TenantContextError,
  ValidationError,
  toAppError,
} from "@/server/errors/app-error";
import { logger, redact } from "@/server/logging/logger";

import { fecharPrisma } from "./helpers";

afterAll(async () => {
  await fecharPrisma();
});

describe("sonda do banco", () => {
  it("responde que o banco esta no ar, com latencia", async () => {
    const r = await checkDatabase();
    expect(r.reachable).toBe(true);
    expect(r.latencyMs).toBeGreaterThanOrEqual(0);
  });

  it("nao devolve nada alem de alcance e latencia", async () => {
    const r = await checkDatabase();
    expect(Object.keys(r).sort()).toEqual(["latencyMs", "reachable"]);
    // Nenhuma credencial pode aparecer no que sai da sonda.
    expect(JSON.stringify(r)).not.toContain("postgresql://");
  });
});

describe("erros", () => {
  it("cada tipo tem o status certo", () => {
    expect(new ValidationError("campo x").status).toBe(400);
    expect(new TenantContextError("sem tenant").status).toBe(403);
    expect(new DatabaseError("caiu").status).toBe(503);
    expect(new AppError("INTERNAL", "bug").status).toBe(500);
    expect(new AppError("NOT_FOUND", "sumiu").status).toBe(404);
  });

  it("a mensagem publica nunca repete a interna", () => {
    const erro = new DatabaseError(
      "conexao recusada em postgresql://elo_app:senha@127.0.0.1:5433/elo",
    );
    expect(erro.publicMessage).toBe("Servico indisponivel no momento.");
    expect(erro.publicMessage).not.toContain("postgresql");
    expect(erro.publicMessage).not.toContain("senha");
  });

  it("erro desconhecido vira INTERNAL sem perder a causa", () => {
    const original = new Error("estourou");
    const app = toAppError(original);
    expect(app.kind).toBe("INTERNAL");
    expect(app.message).toBe("estourou");
    expect(app.cause).toBe(original);
  });

  it("AppError atravessa toAppError sem virar outra coisa", () => {
    const erro = new ValidationError("email invalido");
    expect(toAppError(erro)).toBe(erro);
  });
});

describe("log estruturado", () => {
  it("corta senha, token e connection string", () => {
    const limpo = redact({
      usuario: "ana",
      senha: "segredo",
      password: "segredo",
      META_TOKEN: "EAAG...",
      DATABASE_URL: "postgresql://elo_app:x@127.0.0.1:5433/elo",
      aninhado: { authorization: "Bearer abc", ok: 1 },
    }) as Record<string, unknown>;

    expect(limpo.usuario).toBe("ana");
    expect(limpo.senha).toBe("[REDIGIDO]");
    expect(limpo.password).toBe("[REDIGIDO]");
    expect(limpo.META_TOKEN).toBe("[REDIGIDO]");
    expect(limpo.DATABASE_URL).toBe("[REDIGIDO]");
    expect((limpo.aninhado as Record<string, unknown>).authorization).toBe("[REDIGIDO]");
    expect((limpo.aninhado as Record<string, unknown>).ok).toBe(1);
  });

  it("emite uma linha JSON com os campos de correlacao", () => {
    const escrito: string[] = [];
    const spy = vi
      .spyOn(process.stdout, "write")
      .mockImplementation((t: string | Uint8Array) => {
        escrito.push(String(t));
        return true;
      });

    logger
      .child({ requestId: "req-1", tenantId: "ten-1", userId: "usr-1" })
      .info("conversa.aberta", { conversationId: "c-1", senha: "nao pode sair" });

    spy.mockRestore();

    expect(escrito).toHaveLength(1);
    const linha = JSON.parse(escrito[0] as string);
    expect(linha.level).toBe("info");
    expect(linha.event).toBe("conversa.aberta");
    expect(linha.requestId).toBe("req-1");
    expect(linha.tenantId).toBe("ten-1");
    expect(linha.userId).toBe("usr-1");
    expect(linha.conversationId).toBe("c-1");
    expect(linha.senha).toBe("[REDIGIDO]");
    expect(typeof linha.ts).toBe("string");
  });

  it("child nao contamina o logger de origem", () => {
    const escrito: string[] = [];
    const spy = vi
      .spyOn(process.stdout, "write")
      .mockImplementation((t: string | Uint8Array) => {
        escrito.push(String(t));
        return true;
      });

    logger.child({ tenantId: "ten-x" }).info("a");
    logger.info("b");

    spy.mockRestore();

    expect(JSON.parse(escrito[0] as string).tenantId).toBe("ten-x");
    expect(JSON.parse(escrito[1] as string).tenantId).toBeUndefined();
  });
});
