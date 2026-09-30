/**
 * Notificações por HTTP de verdade: preferências, inscrição, crachá,
 * presença e o destino preservado quando a sessão expira.
 *
 * Os testes que carregam o arquivo:
 *
 *   - a chave PRIVADA VAPID nunca sai por nenhuma rota;
 *   - o endpoint de push nunca sai por nenhuma rota;
 *   - o crachá é PESSOAL: Aline e Carlos, no mesmo atendimento, têm
 *     números diferentes;
 *   - clicar numa notificação com a sessão vencida devolve a pessoa ao
 *     atendimento pretendido, e não à home.
 */
import { generateKeyPairSync } from "node:crypto";

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { provisionMembership } from "@/server/admin/provisioning";
import { exchange } from "@/server/auth/exchange";
import { COOKIE_DESTINO, caminhoSeguro } from "@/server/auth/destination";
import { criarAtendimento } from "@/server/conversations/service";
import { registrarMensagemRecebida } from "@/server/messages/service";
import { hashDoEndpoint } from "@/server/notifications/subscriptions";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import {
  assinarToken,
  criarTenantComPessoa,
  gerarPar,
  instalarChaves,
  removerTenant,
  type ParDeChaves,
  type TenantFixture,
} from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let par: ParDeChaves;
let A: TenantFixture;
let B: TenantFixture;
let cookieAline: string;
let cookieCarlos: string;
let cookieB: string;
let carlos: { membershipId: string };

const CHAVE_NAVEGADOR = (() => {
  const p = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const jwk = p.publicKey.export({ format: "jwk" }) as { x: string; y: string };
  return Buffer.concat([
    Buffer.from([4]),
    Buffer.from(jwk.x, "base64url"),
    Buffer.from(jwk.y, "base64url"),
  ]).toString("base64url");
})();

const AUTH_NAVEGADOR = Buffer.alloc(16, 3).toString("base64url");

async function entrar(t: TenantFixture, uid = t.fiscaleUid): Promise<string> {
  const r = await exchange(assinarToken(par, { sub: uid, tid: t.id }));
  if (!r.ok) throw new Error(`troca falhou: ${r.reason}`);
  return r.session.cookieValue;
}

function req(caminho: string, cookie: string | null, init: RequestInit = {}): NextRequest {
  return new NextRequest(
    new Request(`http://localhost${caminho}`, {
      ...init,
      headers: {
        ...(init.headers as Record<string, string> | undefined),
        ...(cookie ? { cookie: `elo_sessao=${cookie}` } : {}),
      },
    }),
  );
}

function json(corpo: unknown): RequestInit {
  return { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(corpo) };
}

beforeAll(async () => {
  par = gerarPar();
  instalarChaves(par);

  A = await criarTenantComPessoa("nha", "OWNER" as Role);
  B = await criarTenantComPessoa("nhb", "OWNER" as Role);

  const c = await provisionMembership({
    tenantId: A.id,
    role: "AGENT" as Role,
    email: `carlos.${A.slug}@teste`,
    name: "Carlos Souza",
    fiscaleUid: `carlos.${A.slug}`,
  });
  carlos = { membershipId: c.membershipId };

  cookieAline = await entrar(A);
  cookieCarlos = await entrar(A, `carlos.${A.slug}`);
  cookieB = await entrar(B);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

/* ══ GET /api/notifications ═════════════════════════════════════════ */

describe("GET /api/notifications", () => {
  it("sem sessão, 401", async () => {
    const { GET } = await import("@/app/api/notifications/route");
    expect((await GET(req("/api/notifications", null))).status).toBe(401);
  });

  it("devolve preferências, aparelhos e a chave PÚBLICA", async () => {
    const { GET } = await import("@/app/api/notifications/route");
    const r = await GET(req("/api/notifications", cookieAline));
    expect(r.status).toBe(200);

    const d = (await r.json()) as {
      preferencias: { showPreview: boolean };
      dispositivos: unknown[];
      push: { disponivel: boolean; vapidPublicKey?: string };
    };

    expect(d.preferencias.showPreview).toBe(false);
    expect(d.dispositivos).toEqual([]);
    expect(d.push.disponivel).toBe(true);
    // 65 bytes crus em base64url.
    expect(d.push.vapidPublicKey).toHaveLength(87);
  });

  it("A CHAVE PRIVADA VAPID NÃO SAI POR AQUI", async () => {
    // Quem a tem assina avisos em nome do Elo para qualquer aparelho
    // inscrito. Ela existe num arquivo só do servidor, e este teste é o
    // que impede uma serialização distraída de mudar isso.
    const { GET } = await import("@/app/api/notifications/route");
    const corpo = await (await GET(req("/api/notifications", cookieAline))).text();

    expect(corpo).not.toContain(process.env.ELO_VAPID_PRIVATE_KEY ?? "###");
    expect(corpo.toLowerCase()).not.toContain("privatekey");
    expect(corpo).not.toContain("PRIVATE");
  });
});

/* ══ PATCH /api/notifications/preferences ═══════════════════════════ */

describe("PATCH /api/notifications/preferences", () => {
  it("salva e devolve o estado inteiro", async () => {
    const { PATCH } = await import("@/app/api/notifications/preferences/route");
    const r = await PATCH(
      req("/api/notifications/preferences", cookieAline, {
        ...json({ showPreview: true }),
        method: "PATCH",
      }),
    );

    expect(r.status).toBe(200);
    const d = (await r.json()) as { preferencias: { showPreview: boolean; soundEnabled: boolean } };
    expect(d.preferencias.showPreview).toBe(true);
    // O que não foi mandado não é zerado.
    expect(d.preferencias.soundEnabled).toBe(true);
  });

  it("campo desconhecido é 400, com a razão em português", async () => {
    const { PATCH } = await import("@/app/api/notifications/preferences/route");
    const r = await PATCH(
      req("/api/notifications/preferences", cookieAline, {
        ...json({ inventado: true }),
        method: "PATCH",
      }),
    );

    expect(r.status).toBe(400);
    const d = (await r.json()) as { error?: { message?: string } };
    expect(JSON.stringify(d)).toContain("inventado");
  });

  it("não existe forma de alterar a preferência de outra pessoa", async () => {
    // A rota não tem parâmetro de pessoa: o membership vem da sessão. Um
    // corpo que tente informar um id é recusado como campo desconhecido —
    // o que também prova que o campo não é apenas ignorado.
    const { PATCH } = await import("@/app/api/notifications/preferences/route");
    const r = await PATCH(
      req("/api/notifications/preferences", cookieAline, {
        ...json({ membershipId: carlos.membershipId, soundEnabled: false }),
        method: "PATCH",
      }),
    );
    expect(r.status).toBe(400);

    const doCarlos = await withTenant(A.id, async (tx) =>
      tx.notificationPreference.findUnique({ where: { membershipId: carlos.membershipId } }),
    );
    expect(doCarlos).toBeNull();
  });
});

/* ══ inscrição ══════════════════════════════════════════════════════ */

describe("/api/notifications/subscription", () => {
  const endpoint = "https://push.exemplo.test/http-aparelho";

  it("inscreve e NÃO devolve o endpoint", async () => {
    const { POST } = await import("@/app/api/notifications/subscription/route");
    const r = await POST(
      req("/api/notifications/subscription", cookieAline, {
        ...json({ endpoint, p256dh: CHAVE_NAVEGADOR, auth: AUTH_NAVEGADOR }),
        headers: {
          "content-type": "application/json",
          "user-agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        },
      }),
    );

    expect(r.status).toBe(200);
    const corpo = await r.text();

    // O endpoint é um segredo de capacidade: quem o tem entrega
    // notificação naquele aparelho.
    expect(corpo).not.toContain("push.exemplo.test");
    expect(corpo).not.toContain(CHAVE_NAVEGADOR);
    expect(corpo).not.toContain(AUTH_NAVEGADOR);
    expect(corpo).toContain(hashDoEndpoint(endpoint));
    expect(corpo).toContain("Chrome no Windows");
  });

  it("o endpoint e as chaves também não saem na listagem", async () => {
    const { GET } = await import("@/app/api/notifications/route");
    const corpo = await (await GET(req("/api/notifications", cookieAline))).text();
    expect(corpo).not.toContain("push.exemplo.test");
    expect(corpo).not.toContain(AUTH_NAVEGADOR);
  });

  it("a auditoria registra o aparelho, nunca o endpoint", async () => {
    const linhas = await withTenant(A.id, async (tx) =>
      tx.auditLog.findMany({ where: { action: "PUSH_SUBSCRIBED" } }),
    );
    expect(linhas.length).toBeGreaterThan(0);
    const texto = JSON.stringify(linhas);
    expect(texto).not.toContain("push.exemplo.test");
    expect(texto).not.toContain(AUTH_NAVEGADOR);
    expect(texto).toContain("Chrome no Windows");
  });

  it("a inscrição da Aline é invisível para o tenant B", async () => {
    const hash = hashDoEndpoint(endpoint);
    const doB = await withTenant(B.id, async (tx) =>
      tx.pushSubscription.findMany({ where: { endpointHash: hash } }),
    );
    expect(doB).toEqual([]);

    // E o DELETE do B não alcança a linha do A.
    const { DELETE } = await import("@/app/api/notifications/subscription/route");
    const r = await DELETE(
      req("/api/notifications/subscription", cookieB, {
        ...json({ endpointHash: hash }),
        method: "DELETE",
      }),
    );
    expect(r.status).toBe(200);
    expect((await r.json()) as { revogadas: number }).toEqual({ revogadas: 0 });

    const doA = await withTenant(A.id, async (tx) =>
      tx.pushSubscription.count({ where: { endpointHash: hash, revokedAt: null } }),
    );
    expect(doA).toBe(1);
  });

  it("desativar neste dispositivo revoga — e revogar de novo é 200 com zero", async () => {
    const { DELETE } = await import("@/app/api/notifications/subscription/route");
    const corpo = { endpointHash: hashDoEndpoint(endpoint) };

    const um = await DELETE(
      req("/api/notifications/subscription", cookieAline, { ...json(corpo), method: "DELETE" }),
    );
    expect((await um.json()) as { revogadas: number }).toEqual({ revogadas: 1 });

    const dois = await DELETE(
      req("/api/notifications/subscription", cookieAline, { ...json(corpo), method: "DELETE" }),
    );
    expect(dois.status).toBe(200);
    expect((await dois.json()) as { revogadas: number }).toEqual({ revogadas: 0 });
  });

  it("endpoint http:// é recusado com 400", async () => {
    const { POST } = await import("@/app/api/notifications/subscription/route");
    const r = await POST(
      req("/api/notifications/subscription", cookieAline, {
        ...json({
          endpoint: "http://interno.rede.local/push",
          p256dh: CHAVE_NAVEGADOR,
          auth: AUTH_NAVEGADOR,
        }),
      }),
    );
    expect(r.status).toBe(400);
  });

  it("sem sessão, 401 — em POST e em DELETE", async () => {
    const { POST, DELETE } = await import("@/app/api/notifications/subscription/route");
    expect(
      (
        await POST(
          req("/api/notifications/subscription", null, {
            ...json({ endpoint, p256dh: CHAVE_NAVEGADOR, auth: AUTH_NAVEGADOR }),
          }),
        )
      ).status,
    ).toBe(401);
    expect(
      (
        await DELETE(
          req("/api/notifications/subscription", null, {
            ...json({ endpointHash: hashDoEndpoint(endpoint) }),
            method: "DELETE",
          }),
        )
      ).status,
    ).toBe(401);
  });
});

/* ══ crachá de não lidos ════════════════════════════════════════════ */

describe("GET /api/notifications/badge", () => {
  it("O CRACHÁ É PESSOAL: Aline e Carlos têm números diferentes", async () => {
    const { GET } = await import("@/app/api/notifications/badge/route");
    const contar = async (cookie: string) =>
      ((await (await GET(req("/api/notifications/badge", cookie))).json()) as { naoLidos: number })
        .naoLidos;

    const antesAline = await contar(cookieAline);
    const antesCarlos = await contar(cookieCarlos);

    const { id } = await criarAtendimento(A.id, {});
    const m = await registrarMensagemRecebida(A.id, id, { content: "bom dia" });
    expect(m.ok).toBe(true);

    // Os dois sobem: ninguém leu.
    expect(await contar(cookieAline)).toBe(antesAline + 1);
    expect(await contar(cookieCarlos)).toBe(antesCarlos + 1);

    // A Aline lê. Só o número DELA cai.
    const { marcarVisualizadas } = await import("@/server/messages/service");
    if (m.ok) {
      await marcarVisualizadas(
        {
          sessionId: "00000000-0000-4000-8000-000000000001",
          tenantId: A.id,
          membershipId: A.membership.membershipId,
          identityId: A.membership.identityId,
          email: A.email,
          name: "Aline",
          role: "OWNER" as Role,
        },
        id,
        [m.value.id],
      );
    }

    expect(await contar(cookieAline)).toBe(antesAline);
    expect(await contar(cookieCarlos)).toBe(antesCarlos + 1);
  });

  it("o crachá do tenant B não enxerga o atendimento do tenant A", async () => {
    const { GET } = await import("@/app/api/notifications/badge/route");
    const d = (await (await GET(req("/api/notifications/badge", cookieB))).json()) as {
      naoLidos: number;
    };
    expect(d.naoLidos).toBe(0);
  });

  it("sem sessão, 401", async () => {
    const { GET } = await import("@/app/api/notifications/badge/route");
    expect((await GET(req("/api/notifications/badge", null))).status).toBe(401);
  });
});

/* ══ presença ═══════════════════════════════════════════════════════ */

describe("POST /api/presence", () => {
  it("um id de conexão inventado não é aceito", async () => {
    const { POST } = await import("@/app/api/presence/route");
    const r = await POST(
      req("/api/presence", cookieAline, {
        ...json({ connection: "11111111-1111-4111-8111-111111111111", visivel: false }),
      }),
    );
    expect(r.status).toBe(200);
    expect((await r.json()) as { aplicado: boolean }).toEqual({ aplicado: false });
  });

  it("a conexão de OUTRA pessoa não pode ser alterada", async () => {
    const { entrar: entrarPresenca, sair, atencaoDe } = await import(
      "@/server/realtime/presence"
    );

    const chave = entrarPresenca(A.id, {
      membershipId: carlos.membershipId,
      conversationId: null,
      visivel: true,
    });

    try {
      const { POST } = await import("@/app/api/presence/route");
      // A Aline tentando dizer que o Carlos escondeu a aba.
      const r = await POST(
        req("/api/presence", cookieAline, { ...json({ connection: chave, visivel: false }) }),
      );
      expect((await r.json()) as { aplicado: boolean }).toEqual({ aplicado: false });

      // Continua presente.
      expect(atencaoDe(A.id, carlos.membershipId, null)).toBe("NO_ELO");
    } finally {
      sair(chave);
    }
  });

  it("a própria conexão é alterada, e a presença muda de verdade", async () => {
    const { entrar: entrarPresenca, sair, atencaoDe } = await import(
      "@/server/realtime/presence"
    );

    const chave = entrarPresenca(A.id, {
      membershipId: A.membership.membershipId,
      conversationId: null,
      visivel: true,
    });

    try {
      expect(atencaoDe(A.id, A.membership.membershipId, null)).toBe("NO_ELO");

      const { POST } = await import("@/app/api/presence/route");
      const r = await POST(
        req("/api/presence", cookieAline, { ...json({ connection: chave, visivel: false }) }),
      );
      expect((await r.json()) as { aplicado: boolean }).toEqual({ aplicado: true });

      expect(atencaoDe(A.id, A.membership.membershipId, null)).toBe("AUSENTE");
    } finally {
      sair(chave);
    }
  });

  it("sem sessão, 401", async () => {
    const { POST } = await import("@/app/api/presence/route");
    const r = await POST(
      req("/api/presence", null, {
        ...json({ connection: "11111111-1111-4111-8111-111111111111", visivel: true }),
      }),
    );
    expect(r.status).toBe(401);
  });
});

/* ══ o destino sobrevive ao login ═══════════════════════════════════ */

describe("clicar na notificação com a sessão vencida", () => {
  it("guarda o destino e devolve a pessoa ao atendimento depois do login", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const destino = `/atendimentos?abrir=${id}`;

    // 1. sem sessão, o layout manda para cá
    const { GET } = await import("@/app/api/auth/destino/route");
    const guardar = await GET(
      req(`/api/auth/destino?para=${encodeURIComponent(destino)}`, null),
    );
    expect(guardar.status).toBe(303);
    expect(guardar.headers.get("location")).toContain("/entrar");

    const cookieDestino = guardar.cookies.get(COOKIE_DESTINO);
    expect(cookieDestino?.value).toBe(destino);
    expect(cookieDestino?.httpOnly).toBe(true);

    // 2. a pessoa faz login no Fiscale e volta com o token de troca
    const { POST } = await import("@/app/api/auth/exchange/route");
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });

    const troca = await POST(
      new NextRequest(
        new Request("http://localhost/api/auth/exchange", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${COOKIE_DESTINO}=${encodeURIComponent(destino)}`,
          },
          body: JSON.stringify({ token }),
        }),
      ),
    );

    // 3. e cai no atendimento pretendido, não na home
    expect(troca.status).toBe(303);
    expect(troca.headers.get("location")).toBe(`http://localhost${destino}`);

    // 4. o destino é queimado junto com o token: um destino que
    //    sobrevivesse sequestraria os próximos logins deste navegador.
    expect(troca.cookies.get(COOKIE_DESTINO)?.value).toBe("");
  });

  it("sem destino guardado, o login continua indo para a home", async () => {
    const { POST } = await import("@/app/api/auth/exchange/route");
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });

    const troca = await POST(
      new NextRequest(
        new Request("http://localhost/api/auth/exchange", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ token }),
        }),
      ),
    );
    expect(troca.headers.get("location")).toBe("http://localhost/");
  });

  it("NÃO vira redirecionador aberto", async () => {
    // O caminho vem de uma URL. Sem esta validação, `?para=//evil.com`
    // faria o Elo mandar a pessoa para outro site depois do login — com a
    // credibilidade do domínio do escritório emprestada ao golpe.
    for (const perigoso of [
      "//evil.com",
      "https://evil.com",
      "/\\evil.com",
      "/atendimentos\\..\\x",
      "http://evil.com/x",
      "javascript:alert(1)",
    ]) {
      expect(caminhoSeguro(perigoso)).toBeNull();
    }

    expect(caminhoSeguro("/atendimentos?abrir=abc")).toBe("/atendimentos?abrir=abc");
    expect(caminhoSeguro("/")).toBe("/");

    // E a rota simplesmente não grava o cookie.
    const { GET } = await import("@/app/api/auth/destino/route");
    const r = await GET(req("/api/auth/destino?para=https://evil.com", null));
    expect(r.status).toBe(303);
    expect(r.cookies.get(COOKIE_DESTINO)).toBeUndefined();
  });

  it("o destino não concede acesso: o atendimento de outro tenant não abre", async () => {
    // O conversationId veio de um push. Ele decide a URL, e nada mais:
    // sessão, tenant e RLS continuam valendo do outro lado.
    const { id } = await criarAtendimento(A.id, {});
    const { obterAtendimento } = await import("@/server/conversations/queries");

    const visto = await obterAtendimento(
      {
        sessionId: "00000000-0000-4000-8000-000000000001",
        tenantId: B.id,
        membershipId: B.membership.membershipId,
        identityId: B.membership.identityId,
        email: B.email,
        name: "Do B",
        role: "OWNER" as Role,
      },
      id,
    );
    expect(visto).toBeNull();
  });
});
