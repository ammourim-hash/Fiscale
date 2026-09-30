// @vitest-environment jsdom
/**
 * A tela de Admin > Equipe.
 *
 * Os testes que carregam o arquivo:
 *
 *   - o item "Equipe" aparece para quem administra e SOME para quem não
 *     administra — sabendo que sumir do menu não é autorização, e que a
 *     rota é quem recusa de verdade (ver `equipe.test.ts`);
 *   - a área principal só oferece áreas SELECIONADAS, e desmarcar a área
 *     principal limpa o principal junto;
 *   - a cobertura mostra o que o servidor calculou, incluindo o caso em
 *     que ninguém seria avisado.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { Equipe, type Cobertura, type Pessoa } from "@/components/equipe";
import { ThemeProvider } from "@/components/theme";

/**
 * `forbidden()` e `redirect()` interrompem a renderização lançando — é
 * assim que o App Router transporta 403 e 3xx. Os substitutos lançam um
 * marcador, que é o que o teste consegue pegar.
 */
const nav = vi.hoisted(() => ({
  forbidden: vi.fn(() => {
    throw new Error("FORBIDDEN");
  }),
  redirect: vi.fn((destino: string) => {
    throw new Error(`REDIRECT:${destino}`);
  }),
}));

const sessaoFalsa = vi.hoisted(() => ({ atual: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/equipe",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  forbidden: nav.forbidden,
  redirect: nav.redirect,
}));

vi.mock("@/server/auth/page-session", () => ({
  pageSession: () => sessaoFalsa.atual(),
}));

const FISCAL = { id: "d-fiscal", slug: "fiscal", name: "Fiscal" };
const CONTABIL = { id: "d-contabil", slug: "contabil", name: "Contábil" };
const DP = { id: "d-dp", slug: "dp", name: "DP" };

const ALINE: Pessoa = {
  membershipId: "m1",
  identityId: "i1",
  name: "Aline Exemplo",
  email: "aline@escritorio.teste",
  role: "OWNER",
  active: true,
  identityActive: true,
  availableForAssignment: true,
  visibleToCustomers: false,
  fiscaleUid: "aline",
  departments: [FISCAL],
  primaryDepartmentId: FISCAL.id,
};

const CARLOS: Pessoa = {
  membershipId: "m2",
  identityId: "i2",
  name: "Carlos Souza",
  email: "carlos@escritorio.teste",
  role: "AGENT",
  active: false,
  identityActive: true,
  availableForAssignment: false,
  visibleToCustomers: true,
  fiscaleUid: "carlos",
  departments: [CONTABIL],
  primaryDepartmentId: null,
};

const COBERTURA: Cobertura[] = [
  { departmentId: FISCAL.id, slug: "fiscal", name: "Fiscal", elegiveis: 3, gestores: 1, vinculados: 3 },
  { departmentId: CONTABIL.id, slug: "contabil", name: "Contábil", elegiveis: 0, gestores: 0, vinculados: 2 },
  { departmentId: DP.id, slug: "dp", name: "DP", elegiveis: 1, gestores: 0, vinculados: 1 },
];

function servidor(opcoes: { status?: number } = {}) {
  const enviados: { url: string; init?: RequestInit | undefined }[] = [];

  const falso = vi.fn(async (url: string | URL, init?: RequestInit) => {
    enviados.push({ url: String(url), init: init ?? undefined });

    if (String(url) === "/api/team" && (!init || init.method === undefined)) {
      if (opcoes.status && opcoes.status !== 200) {
        return new Response("{}", { status: opcoes.status });
      }
      return new Response(
        JSON.stringify({
          people: [ALINE, CARLOS],
          departments: [FISCAL, CONTABIL, DP],
          coverage: COBERTURA,
        }),
        { headers: { "content-type": "application/json" } },
      );
    }

    return new Response(JSON.stringify({ person: ALINE }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });
  });

  vi.stubGlobal("fetch", falso);
  return { enviados };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/* ══ 1. o item no menu ══════════════════════════════════════════════ */

describe("o item Equipe na navegação", () => {
  function sessao(role: string) {
    return {
      name: "Alguém",
      email: "alguem@escritorio.teste",
      role,
      primaryDepartment: null,
      departments: [],
    };
  }

  async function renderCasca(role: string) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ naoLidos: 0 }), { status: 200 })),
    );
    const { AppShell } = await import("@/components/app-shell");
    render(
      <ThemeProvider>
        <AppShell session={sessao(role)}>
          <p>conteúdo</p>
        </AppShell>
      </ThemeProvider>,
    );
  }

  it.each(["OWNER", "ADMIN"])("aparece para %s", async (role) => {
    await renderCasca(role);
    expect(screen.getByRole("link", { name: /equipe/i })).toHaveAttribute(
      "href",
      "/admin/equipe",
    );
  });

  it.each(["MANAGER", "AGENT", "VIEWER"])("NÃO aparece para %s", async (role) => {
    // E não aparecer não é a proteção: quem chamar /api/team leva 403 do
    // mesmo jeito. O item some para não oferecer o que não se pode usar.
    await renderCasca(role);
    expect(screen.queryByRole("link", { name: /equipe/i })).toBeNull();
  });
});

/* ══ 2. cobertura ═══════════════════════════════════════════════════ */

describe("cobertura no topo", () => {
  it("mostra elegíveis, gestores e o alerta de ninguém avisado", async () => {
    servidor();
    render(<Equipe />);

    const bloco = await screen.findByLabelText("Cobertura dos setores");

    expect(within(bloco).getByText("3 de 3")).toBeInTheDocument();
    expect(within(bloco).getByText(/cobertura completa/i)).toBeInTheDocument();

    // O caso que a tela existe para gritar.
    expect(within(bloco).getByText(/ninguém será avisado/i)).toBeInTheDocument();
    // E a diferença entre vinculado e elegível fica visível, não escondida.
    expect(within(bloco).getByText(/2 vinculados/)).toBeInTheDocument();

    expect(within(bloco).getByText(/faltam 2/i)).toBeInTheDocument();
  });
});

/* ══ 3. lista e filtros ═════════════════════════════════════════════ */

describe("lista", () => {
  it("mostra nome, e-mail, papel, áreas e as duas chaves", async () => {
    servidor();
    render(<Equipe />);

    await screen.findByText("Aline Exemplo");

    // Escopo na LINHA da pessoa: o rótulo do papel também existe como
    // opção do filtro, e procurar solto acharia os dois.
    const linhaAline = screen.getByRole("button", { name: /aline amorim/i });
    expect(within(linhaAline).getByText("aline@escritorio.teste")).toBeInTheDocument();
    expect(within(linhaAline).getByText("Responsável pelo escritório")).toBeInTheDocument();
    expect(within(linhaAline).getByText(/· principal/)).toBeInTheDocument();
    expect(within(linhaAline).getByText(/assume atendimento/i)).toBeInTheDocument();

    const linhaCarlos = screen.getByRole("button", { name: /carlos souza/i });
    expect(within(linhaCarlos).getByText("carlos@escritorio.teste")).toBeInTheDocument();
    expect(within(linhaCarlos).getByText(/não assume/i)).toBeInTheDocument();
    expect(within(linhaCarlos).getByText(/visível ao cliente/i)).toBeInTheDocument();
    expect(within(linhaCarlos).getByText("Inativo")).toBeInTheDocument();
  });

  it("filtra por papel", async () => {
    servidor();
    render(<Equipe />);
    await screen.findByText("Aline Exemplo");

    fireEvent.change(screen.getByLabelText("Papel"), { target: { value: "AGENT" } });

    expect(screen.queryByText("Aline Exemplo")).toBeNull();
    expect(screen.getByText("Carlos Souza")).toBeInTheDocument();
  });

  it("filtra por área", async () => {
    servidor();
    render(<Equipe />);
    await screen.findByText("Aline Exemplo");

    fireEvent.change(screen.getByLabelText("Área"), { target: { value: DP.id } });
    expect(screen.getByText(/nenhuma pessoa corresponde/i)).toBeInTheDocument();
  });

  it("filtra por situação", async () => {
    servidor();
    render(<Equipe />);
    await screen.findByText("Aline Exemplo");

    fireEvent.change(screen.getByLabelText("Situação"), { target: { value: "inativos" } });
    expect(screen.queryByText("Aline Exemplo")).toBeNull();
    expect(screen.getByText("Carlos Souza")).toBeInTheDocument();
  });

  it("403 do servidor vira recado, não tela quebrada", async () => {
    servidor({ status: 403 });
    render(<Equipe />);
    expect(await screen.findByText(/não tem acesso/i)).toBeInTheDocument();
  });
});

/* ══ 4. formulário ══════════════════════════════════════════════════ */

describe("formulário", () => {
  async function abrirNova() {
    const s = servidor();
    render(<Equipe />);
    await screen.findByText("Aline Exemplo");
    fireEvent.click(screen.getByRole("button", { name: /nova pessoa/i }));
    return s;
  }

  it("abre vazio, com o aviso sobre o login do FISCALE", async () => {
    await abrirNova();

    const gaveta = screen.getByRole("dialog", { name: /nova pessoa/i });
    expect(
      within(gaveta).getByText(/o mesmo login que a pessoa usa para entrar no FISCALE, não o/i),
    ).toBeInTheDocument();
    expect(within(gaveta).getByLabelText(/^Nome$/)).toHaveValue("");
  });

  it("marca VÁRIAS áreas e só então oferece a principal", async () => {
    await abrirNova();
    const gaveta = screen.getByRole("dialog", { name: /nova pessoa/i });

    const principal = within(gaveta).getByLabelText(/área principal/i);
    // Nada selecionado: só "Nenhuma".
    expect(within(principal).getAllByRole("option")).toHaveLength(1);

    fireEvent.click(within(gaveta).getByLabelText("Fiscal"));
    fireEvent.click(within(gaveta).getByLabelText("DP"));

    const opcoes = within(principal)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(opcoes).toEqual(["Nenhuma", "Fiscal", "DP"]);
  });

  it("desmarcar a área principal limpa o principal", async () => {
    await abrirNova();
    const gaveta = screen.getByRole("dialog", { name: /nova pessoa/i });

    fireEvent.click(within(gaveta).getByLabelText("Fiscal"));
    const principal = within(gaveta).getByLabelText(/área principal/i);
    fireEvent.change(principal, { target: { value: FISCAL.id } });
    expect(principal).toHaveValue(FISCAL.id);

    // Assinar por uma área a que não se pertence não é estado válido.
    fireEvent.click(within(gaveta).getByLabelText("Fiscal"));
    expect(principal).toHaveValue("");
  });

  it("envia POST com os padrões do modelo", async () => {
    const s = await abrirNova();
    const gaveta = screen.getByRole("dialog", { name: /nova pessoa/i });

    fireEvent.change(within(gaveta).getByLabelText(/^Nome$/), {
      target: { value: "Nova Pessoa" },
    });
    fireEvent.change(within(gaveta).getByLabelText(/^E-mail$/), {
      target: { value: "nova@escritorio.teste" },
    });
    fireEvent.change(within(gaveta).getByLabelText(/login do fiscale/i), {
      target: { value: "nova" },
    });

    fireEvent.click(within(gaveta).getByRole("button", { name: /salvar/i }));

    const envio = s.enviados.find((e) => e.init?.method === "POST");
    expect(envio).toBeDefined();
    const corpo = JSON.parse(String(envio!.init!.body)) as Record<string, unknown>;

    expect(corpo.name).toBe("Nova Pessoa");
    expect(corpo.fiscaleUid).toBe("nova");
    expect(corpo.active).toBe(true);
    expect(corpo.availableForAssignment).toBe(true);
    // O padrão que protege.
    expect(corpo.visibleToCustomers).toBe(false);
  });

  it("editar abre com os valores da pessoa, e e-mail e login travados", async () => {
    servidor();
    render(<Equipe />);
    await screen.findByText("Carlos Souza");

    fireEvent.click(screen.getByRole("button", { name: /carlos souza/i }));

    const gaveta = screen.getByRole("dialog", { name: /editar pessoa/i });
    expect(within(gaveta).getByLabelText(/^Nome$/)).toHaveValue("Carlos Souza");
    expect(within(gaveta).getByLabelText(/^E-mail$/)).toBeDisabled();
    // O login é a chave da entrada pelo FISCALE; trocá-lo depois seria
    // desligar a pessoa do próprio acesso sem aviso.
    expect(within(gaveta).getByLabelText(/login do fiscale/i)).toBeDisabled();
    expect(within(gaveta).getByLabelText("Contábil")).toBeChecked();
    expect(within(gaveta).getByLabelText("Fiscal")).not.toBeChecked();
  });

  it("editar manda PATCH e PUT — os dados e as áreas", async () => {
    const s = servidor();
    render(<Equipe />);
    await screen.findByText("Carlos Souza");

    fireEvent.click(screen.getByRole("button", { name: /carlos souza/i }));
    const gaveta = screen.getByRole("dialog", { name: /editar pessoa/i });

    fireEvent.click(within(gaveta).getByLabelText("DP"));
    fireEvent.click(within(gaveta).getByRole("button", { name: /salvar/i }));

    await screen.findByText("Aline Exemplo");

    expect(s.enviados.some((e) => e.init?.method === "PATCH")).toBe(true);
    const put = s.enviados.find((e) => e.init?.method === "PUT");
    expect(put?.url).toBe("/api/team/m2/departments");
    const corpo = JSON.parse(String(put!.init!.body)) as { departmentIds: string[] };
    expect(corpo.departmentIds.sort()).toEqual([CONTABIL.id, DP.id].sort());
  });
});

/* ══ 5. a porta da página ═══════════════════════════════════════════ */

/**
 * A página cobra `users.manage`, e não `users.read`.
 *
 * Quem só lê não teria o que fazer aqui: cada botão da tela chama uma
 * rota que exige `users.manage` e devolveria 403. Abrir a tela para
 * depois negar tudo é pior do que negar a tela.
 *
 * E a recusa é 403, não 404: dizer "não existe" a alguém que TEM conta
 * neste escritório manda a pessoa procurar um defeito que não existe.
 */
describe("quem abre /admin/equipe", () => {
  async function abrir(role: string, permissions: string[]) {
    nav.forbidden.mockClear();
    nav.redirect.mockClear();
    sessaoFalsa.atual.mockResolvedValue({
      context: { role, tenantId: "t", membershipId: "m" },
      permissions,
      primaryDepartment: null,
      departments: [],
    });

    const { default: Pagina } = await import("@/app/(app)/admin/equipe/page");
    return Pagina();
  }

  const COM_MANAGE = ["users.read", "users.manage", "departments.read"];
  const SEM_MANAGE = ["users.read", "departments.read"];

  it.each(["OWNER", "ADMIN"])("%s abre a página", async (role) => {
    await expect(abrir(role, COM_MANAGE)).resolves.toBeTruthy();
    expect(nav.forbidden).not.toHaveBeenCalled();
  });

  it.each([
    ["MANAGER", SEM_MANAGE],
    ["AGENT", SEM_MANAGE],
    ["VIEWER", ["departments.read"]],
  ])("%s leva 403", async (role, permissoes) => {
    // O MANAGER é o caso que motivou a mudança: ele tem `users.read` e
    // abriria a tela na versão anterior, para levar 403 em cada botão.
    await expect(abrir(role, permissoes as string[])).rejects.toThrow("FORBIDDEN");
    expect(nav.forbidden).toHaveBeenCalledTimes(1);
  });

  it("sem sessão, vai para a porta de entrada", async () => {
    nav.forbidden.mockClear();
    nav.redirect.mockClear();
    sessaoFalsa.atual.mockResolvedValue(null);

    const { default: Pagina } = await import("@/app/(app)/admin/equipe/page");
    await expect(Pagina()).rejects.toThrow("REDIRECT:/entrar");
    expect(nav.forbidden).not.toHaveBeenCalled();
  });
});
