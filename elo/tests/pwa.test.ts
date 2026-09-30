/**
 * A PWA: manifesto, ícones e service worker.
 *
 * ---------------------------------------------------------------------
 *  O que estes testes fazem — e o que só o navegador pode fazer
 * ---------------------------------------------------------------------
 *  Instalabilidade tem critérios objetivos e verificáveis fora do
 *  navegador: manifesto válido, `start_url`, `display`, ícone de 192 e de
 *  512, service worker registrado. É isso que se confere aqui, e é o que
 *  falha quando alguém mexe num arquivo e não reinstala a PWA para olhar.
 *
 *  O que NÃO dá para provar sem hardware está declarado no relatório e no
 *  README: a instalação de verdade, a notificação do sistema operacional
 *  aparecendo na tela e o clique nela.
 *
 *  O teste que carrega o arquivo é o do CACHE: o service worker não pode
 *  guardar nada de `/api`. Um worker que cacheia resposta autenticada
 *  deixa histórico de atendimento em disco, fora do banco, sem RLS e sem
 *  expiração — numa máquina compartilhada, à disposição da pessoa
 *  seguinte.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/** Arquivos de uma pasta, menos o cliente gerado pelo Prisma. */
function varrer(dir: string, padrao = /\.tsx?$/): string[] {
  const saida: string[] = [];
  for (const entrada of readdirSync(dir, { withFileTypes: true })) {
    const caminho = join(dir, entrada.name);
    if (entrada.isDirectory()) {
      if (entrada.name !== "generated") saida.push(...varrer(caminho, padrao));
      continue;
    }
    if (padrao.test(entrada.name)) saida.push(caminho);
  }
  return saida;
}

const RAIZ = join(process.cwd(), "public");
const sw = readFileSync(join(RAIZ, "sw.js"), "utf8");
const manifesto = JSON.parse(
  readFileSync(join(RAIZ, "manifest.webmanifest"), "utf8"),
) as {
  name: string;
  short_name: string;
  start_url: string;
  scope: string;
  display: string;
  theme_color: string;
  background_color: string;
  lang: string;
  icons: { src: string; sizes: string; type: string; purpose?: string }[];
};

/* ══ manifesto ══════════════════════════════════════════════════════ */

describe("manifesto", () => {
  it("tem o que o navegador exige para oferecer a instalação", () => {
    expect(manifesto.name).toBeTruthy();
    expect(manifesto.short_name).toBeTruthy();
    expect(manifesto.start_url).toBe("/");
    expect(manifesto.scope).toBe("/");
    expect(["standalone", "fullscreen", "minimal-ui"]).toContain(manifesto.display);
    expect(manifesto.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(manifesto.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("tem ícone de 192 e de 512, que é o mínimo do Chrome", () => {
    const tamanhos = manifesto.icons.map((i) => i.sizes);
    expect(tamanhos).toContain("192x192");
    expect(tamanhos).toContain("512x512");
    for (const i of manifesto.icons) expect(i.type).toBe("image/png");
  });

  it("tem ícone maskable — sem ele o Android corta as pontas dos elos", () => {
    const maskable = manifesto.icons.find((i) => i.purpose === "maskable");
    expect(maskable).toBeDefined();
    expect(maskable?.sizes).toBe("512x512");
  });

  it("é o ELO, e não o WhatsApp: identidade do FISCALE, sem verde", () => {
    expect(manifesto.short_name).toBe("ELO");
    expect(manifesto.name).toContain("Fiscale");
    expect(manifesto.lang).toBe("pt-BR");
    // Petróleo. A cor de ação do Elo é azul; verde é do outro produto.
    expect(manifesto.theme_color.toLowerCase()).toBe("#10444e");
    expect(manifesto.background_color.toLowerCase()).toBe("#10444e");
  });

  it("os arquivos de ícone existem de verdade e são PNG", () => {
    for (const i of manifesto.icons) {
      const bytes = readFileSync(join(RAIZ, i.src.replace(/^\//, "")));
      // Assinatura PNG.
      expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");

      // A largura declarada bate com a do IHDR — um ícone de 512 com 64
      // pixels passaria no manifesto e ficaria borrado na tela inicial.
      const largura = bytes.readUInt32BE(16);
      expect(String(largura)).toBe(i.sizes.split("x")[0]);
    }
  });
});

/* ══ service worker ═════════════════════════════════════════════════ */

describe("service worker", () => {
  it("trata push, clique e instalação", () => {
    for (const evento of ["install", "activate", "push", "notificationclick", "fetch"]) {
      expect(sw).toContain(`addEventListener("${evento}"`);
    }
  });

  it("trata `pushsubscriptionchange` — o defeito mais difícil de diagnosticar", () => {
    // O navegador troca a inscrição sozinho de tempos em tempos. Sem
    // tratar isso, o aparelho para de receber e ninguém descobre por quê.
    expect(sw).toContain('addEventListener("pushsubscriptionchange"');
  });

  it("NÃO CACHEIA NADA DE /api", () => {
    // A linha que não se atravessa. `/api` é mensagem, cliente, anexo e
    // sessão.
    expect(sw).toContain('url.pathname.startsWith("/api/")');
    const trecho = sw.slice(sw.indexOf('url.pathname.startsWith("/api/")'));
    // Logo depois vem um `return` sem `respondWith`: a requisição volta
    // para o navegador como se o worker não existisse.
    expect(trecho.slice(0, 120)).toContain("return;");
  });

  it("a lista do que ele guarda tem só a casca", () => {
    const casca = /const CASCA = \[([\s\S]*?)\]/.exec(sw)?.[1] ?? "";
    const itens = [...casca.matchAll(/"([^"]+)"/g)].map((m) => m[1]);

    expect(itens).toContain("/offline");
    // Nada de conversa, cliente, anexo ou rota autenticada.
    for (const item of itens) {
      expect(item).not.toContain("/api");
      expect(item).not.toContain("atendimento");
      expect(item).not.toContain("cliente");
      expect(item).not.toContain("attachment");
    }
  });

  it("só intercepta GET — escrita nunca passa por cache", () => {
    expect(sw).toContain('req.method !== "GET"');
  });

  it("navegação é REDE PRIMEIRO, com a página offline como saída", () => {
    // Dado de minutos atrás apresentado como atual faria alguém responder
    // ao cliente pela informação errada.
    const bloco = sw.slice(sw.indexOf('req.mode === "navigate"'));
    expect(bloco).toContain("fetch(req)");
    expect(bloco).toContain('match("/offline")');
    expect(bloco.indexOf("fetch(req)")).toBeLessThan(bloco.indexOf('match("/offline")'));
  });

  it("não faz `skipWaiting` sozinho: quem escolhe a hora é quem está usando", () => {
    // Trocar o worker debaixo de quem está digitando a resposta de um
    // cliente recarrega a página e perde o texto.
    const instalacao = sw.slice(sw.indexOf('addEventListener("install"'), sw.indexOf('addEventListener("activate"'));
    expect(instalacao).not.toContain("skipWaiting");
    // Só pela mensagem da aba.
    expect(sw).toContain("ATUALIZAR_AGORA");
    expect(sw).toContain("self.skipWaiting()");
  });

  it("a versão do cache é usada para limpar as anteriores", () => {
    expect(sw).toMatch(/const VERSAO = "elo-v\d+"/);
    expect(sw).toContain("caches.delete");
  });

  it("o crachá vem do servidor, não de uma contagem de notificações", () => {
    // Duas notificações do mesmo atendimento não são dois não lidos, e um
    // aviso já lido em outro aparelho não é nenhum.
    expect(sw).toContain("/api/notifications/badge");
    expect(sw).toContain("clearAppBadge");
  });

  it("o clique FOCA a janela existente em vez de abrir outra aba", () => {
    const bloco = sw.slice(sw.indexOf("async function abrir("));
    expect(bloco).toContain("matchAll");
    expect(bloco).toContain(".focus()");
    expect(bloco).toContain("openWindow");
    expect(bloco.indexOf(".focus()")).toBeLessThan(bloco.indexOf("openWindow"));
  });

  it("o destino do clique vai por query, e o servidor é quem autoriza", () => {
    expect(sw).toContain("/atendimentos?abrir=");
  });

  it("push sem payload legível ainda vira notificação", () => {
    // Em vários navegadores, receber push e NÃO notificar faz o sistema
    // revogar a permissão do site.
    expect(sw).toContain("showNotification");
    expect(sw).toContain('"ELO"');
    expect(sw).toContain("Você recebeu um novo aviso.");
  });

  it("nenhum segredo mora no service worker", () => {
    // Ele é servido em claro para qualquer visitante da origem.
    expect(sw).not.toContain("VAPID_PRIVATE");
    expect(sw).not.toContain(process.env.ELO_VAPID_PRIVATE_KEY ?? "###");
    expect(sw).not.toContain("DATABASE_URL");
  });
});

/* ══ o segredo não vai para o cliente ═══════════════════════════════ */

describe("a chave privada VAPID não atravessa para o navegador", () => {
  it("só dois arquivos de src/ conhecem a variável da chave privada", () => {
    const arquivos = varrer(join(process.cwd(), "src")).filter((f) =>
      readFileSync(f, "utf8").includes("ELO_VAPID_PRIVATE_KEY"),
    );

    const relativos = arquivos
      .map((f) => f.slice(process.cwd().length + 1).replace(/\\/g, "/"))
      .sort();

    // O schema do ambiente e o módulo de configuração do servidor. Um
    // componente de tela nessa lista seria o segredo indo para o bundle —
    // e é exatamente esse acidente que este teste existe para pegar.
    expect(relativos).toEqual(["src/env.ts", "src/server/push/config.ts"]);
  });

  it("nenhum componente de cliente importa `push/config` ou `push/webpush`", () => {
    // A barreira estrutural: `"use client"` no topo significa que o
    // arquivo VAI para o navegador. Um `import` de lá para o módulo que
    // toca a chave privada arrastaria o segredo junto.
    const clientes = varrer(join(process.cwd(), "src")).filter((f) =>
      readFileSync(f, "utf8").trimStart().startsWith('"use client"'),
    );
    expect(clientes.length).toBeGreaterThan(0);

    for (const f of clientes) {
      const texto = readFileSync(f, "utf8");
      expect(texto, `${f} não pode importar o módulo de push do servidor`).not.toContain(
        "@/server/push/",
      );
      expect(texto).not.toContain("@/env");
    }
  });

  it("não existe variável NEXT_PUBLIC_ de VAPID", () => {
    // `NEXT_PUBLIC_` vai para o bundle por definição. Duas variáveis com
    // nomes parecidos, uma pública e outra secreta, é como o segredo
    // escorrega.
    const exemplo = readFileSync(join(process.cwd(), ".env.example"), "utf8");
    expect(exemplo).not.toMatch(/NEXT_PUBLIC_.*VAPID/);
    for (const chave of Object.keys(process.env)) {
      expect(chave.startsWith("NEXT_PUBLIC_") && chave.includes("VAPID")).toBe(false);
    }
  });
});

/* ══ o bundle já construído ═════════════════════════════════════════ */

describe("o que foi para o navegador, depois do build", () => {
  const estaticos = join(process.cwd(), ".next", "static");
  const construido = existsSync(estaticos);

  /**
   * Roda só quando há um build no disco.
   *
   * `npm run verify` executa os testes ANTES do build, então numa máquina
   * limpa este bloco é pulado — e isso é dito em voz alta em vez de
   * passar em silêncio, que daria a impressão de uma garantia que não
   * houve. Depois de `npm run build`, ele confere o artefato de verdade.
   */
  it.skipIf(!construido)("nenhum segredo aparece nos arquivos do cliente", () => {
    const arquivos = varrer(estaticos, /\.(js|css|map)$/);
    expect(arquivos.length).toBeGreaterThan(0);

    const segredos = [
      process.env.ELO_VAPID_PRIVATE_KEY,
      process.env.ELO_VAPID_PUBLIC_KEY,
      process.env.DATABASE_URL,
      process.env.ELO_IP_HASH_SALT,
    ].filter((s): s is string => typeof s === "string" && s.length > 12);

    for (const arquivo of arquivos) {
      const conteudo = readFileSync(arquivo, "utf8");
      for (const segredo of segredos) {
        expect(
          conteudo.includes(segredo),
          `${arquivo.slice(process.cwd().length + 1)} carrega um segredo do .env`,
        ).toBe(false);
      }
    }
  });

  it.skipIf(!construido)("a chave PÚBLICA também não é embutida — ela vem por rota", () => {
    // Embutir a pública seria inofensivo em si, e é justamente por isso
    // que o erro acontece: alguém a coloca numa variável `NEXT_PUBLIC_`,
    // e um dia a privada vai junto por copiar a linha de cima.
    const arquivos = varrer(estaticos, /\.js$/);
    const chave = process.env.ELO_VAPID_PUBLIC_KEY ?? "";
    if (chave.length < 20) return;
    for (const arquivo of arquivos) {
      expect(readFileSync(arquivo, "utf8").includes(chave)).toBe(false);
    }
  });
});
