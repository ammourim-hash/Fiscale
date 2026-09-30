// @vitest-environment jsdom
/**
 * Interface: tema, estados, perfil do contato e formatação.
 *
 * Roda em jsdom, sem banco — o que se testa aqui é comportamento de tela.
 * O isolamento entre tenants continua sendo garantido no servidor e tem
 * testes próprios; esconder um elemento nunca foi autorização.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CustomerBrowser, CustomerProfileDrawer, type Customer } from "@/components/customers";
import { ConversationsScreen, EmptyConversation, OutgoingPreview } from "@/components/conversations";
import { THEME_STORAGE_KEY, ThemeProvider, ThemeToggle } from "@/components/theme";
import { EmployeeBadge, ErrorState, LoadingState } from "@/components/ui";
import { formatDocument, formatPhone, formatSyncedAt, saudacao } from "@/lib/format";
import { ehAdministrador, rotuloPapel, rotuloPermissao } from "@/lib/permission-labels";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

const CLIENTE: Customer = {
  id: "c1",
  source: "FISCALE",
  externalId: "7",
  displayName: "Padaria do Joao Ltda",
  documentDigits: "11222333000181",
  email: "contato@padaria.com",
  active: true,
  syncedAt: new Date().toISOString(),
  missingSince: null,
  phones: [
    { raw: "(81) 99999-1111", e164: "+5581999991111", status: "OK" },
    { raw: "3333-4444", e164: null, status: "NO_AREA_CODE" },
  ],
};

/* ─── tema ───────────────────────────────────────────────────────────── */

describe("tema", () => {
  beforeEach(() => {
    // jsdom nao implementa matchMedia.
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: false,
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  });

  it("comeca em Sistema e marca a opcao ativa", async () => {
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sistema/i })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
  });

  it("escolher Escuro aplica no documento e persiste", async () => {
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /escuro/i }));

    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: /escuro/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("volta para Claro", async () => {
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /escuro/i }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    fireEvent.click(screen.getByRole("button", { name: /claro/i }));

    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("le a preferencia guardada ao montar", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
  });

  it("as tres opcoes sao botoes de verdade, nao divs", () => {
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    const grupo = screen.getByRole("group", { name: /apar[êe]ncia/i });
    expect(within(grupo).getAllByRole("button")).toHaveLength(3);
  });
});

/* ─── clientes ───────────────────────────────────────────────────────── */

describe("lista de clientes", () => {
  it("mostra carregando e depois os clientes da API real", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ customers: [CLIENTE] }), { status: 200 })),
    );

    render(<CustomerBrowser />);
    expect(screen.getByRole("status")).toHaveTextContent(/carregando/i);

    expect(await screen.findByText("Padaria do Joao Ltda")).toBeTruthy();
    // Documento e telefone formatados na linha, sem ocupar a tela toda.
    expect(screen.getByText(/11\.222\.333\/0001-81/)).toBeTruthy();
  });

  it("estado vazio quando a carteira nao tem ninguem", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ customers: [] }), { status: 200 })),
    );

    render(<CustomerBrowser />);
    expect(await screen.findByText(/nenhum cliente sincronizado ainda/i)).toBeTruthy();
  });

  it("estado vazio de BUSCA diz outra coisa", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ customers: [] }), { status: 200 })),
    );

    render(<CustomerBrowser termoInicial="xyz" />);
    expect(await screen.findByText(/nenhum cliente encontrado/i)).toBeTruthy();
  });

  it("estado de erro oferece tentar de novo, e o retry chama a API", async () => {
    const chamada = vi
      .fn()
      .mockResolvedValueOnce(new Response("erro", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ customers: [CLIENTE] }), { status: 200 }));
    vi.stubGlobal("fetch", chamada);

    render(<CustomerBrowser />);

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/não foi possível carregar os clientes/i);

    fireEvent.click(screen.getByRole("button", { name: /tentar novamente/i }));
    expect(await screen.findByText("Padaria do Joao Ltda")).toBeTruthy();
    expect(chamada).toHaveBeenCalledTimes(2);
  });

  it("busca vai para a API com o termo digitado", async () => {
    // O parametro precisa existir na assinatura para `mock.calls[0][0]`
    // ser tipado — vi.fn(async () => …) produz tupla vazia.
    const chamada = vi.fn(
      async (url: string) =>
        new Response(JSON.stringify({ customers: [] }), { status: 200, headers: { "x-url": url } }),
    );
    vi.stubGlobal("fetch", chamada);

    render(<CustomerBrowser />);
    fireEvent.change(screen.getByRole("searchbox", { name: /procurar cliente/i }), {
      target: { value: "padaria" },
    });

    await waitFor(() => {
      const urls = chamada.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("q=padaria"))).toBe(true);
    });
  });

  it("o filtro de inativos chega na consulta", async () => {
    // O parametro precisa existir na assinatura para `mock.calls[0][0]`
    // ser tipado — vi.fn(async () => …) produz tupla vazia.
    const chamada = vi.fn(
      async (url: string) =>
        new Response(JSON.stringify({ customers: [] }), { status: 200, headers: { "x-url": url } }),
    );
    vi.stubGlobal("fetch", chamada);

    render(<CustomerBrowser />);
    fireEvent.click(screen.getByLabelText(/mostrar inativos/i));

    await waitFor(() => {
      const urls = chamada.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("inactive=1"))).toBe(true);
    });
  });

  it("cada cliente e um button — nao uma div clicavel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ customers: [CLIENTE] }), { status: 200 })),
    );

    render(<CustomerBrowser />);
    const item = await screen.findByRole("button", { name: /padaria do joao/i });
    expect(item.tagName).toBe("BUTTON");
  });

  it("clicar abre a gaveta do perfil, e ela fecha", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ customers: [CLIENTE] }), { status: 200 })),
    );

    render(<CustomerBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: /padaria do joao/i }));

    const gaveta = await screen.findByRole("dialog");
    expect(gaveta).toBeTruthy();

    fireEvent.click(within(gaveta).getByRole("button", { name: /fechar perfil/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

/* ─── perfil do contato ──────────────────────────────────────────────── */

describe("perfil do contato", () => {
  it("mostra documento, telefones, e-mail, origem e sincronizacao", () => {
    render(<CustomerProfileDrawer customer={CLIENTE} onClose={() => {}} />);

    expect(screen.getByText("11.222.333/0001-81")).toBeTruthy();
    expect(screen.getByText("(81) 99999-1111")).toBeTruthy();
    expect(screen.getByText("contato@padaria.com")).toBeTruthy();
    expect(screen.getByText(/FISCALE · nº 7/)).toBeTruthy();
  });

  it("telefone sem DDD aparece marcado, e nao como numero valido", () => {
    render(<CustomerProfileDrawer customer={CLIENTE} onClose={() => {}} />);
    expect(screen.getByText("3333-4444")).toBeTruthy();
    expect(screen.getByText(/sem DDD/i)).toBeTruthy();
  });

  it("NAO mostra dado fiscal — ele nem existe na projecao", () => {
    render(<CustomerProfileDrawer customer={CLIENTE} onClose={() => {}} />);
    const texto = document.body.textContent ?? "";
    for (const proibido of ["Simples Nacional", "Inscrição Estadual", "certificado", ".pfx"]) {
      expect(texto).not.toContain(proibido);
    }
  });

  it("Esc fecha a gaveta", () => {
    const fechar = vi.fn();
    render(<CustomerProfileDrawer customer={CLIENTE} onClose={fechar} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(fechar).toHaveBeenCalled();
  });

  it("e um dialog acessivel, com nome", () => {
    render(<CustomerProfileDrawer customer={CLIENTE} onClose={() => {}} />);
    const d = screen.getByRole("dialog");
    expect(d).toHaveAttribute("aria-modal", "true");
    expect(d.getAttribute("aria-label")).toContain("Padaria do Joao Ltda");
  });

  it("avisa quando o registro nao veio na ultima carga", () => {
    render(
      <CustomerProfileDrawer
        customer={{ ...CLIENTE, missingSince: new Date().toISOString() }}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/não veio na última sincronização/i)).toBeTruthy();
  });
});

/* ─── atendimentos ───────────────────────────────────────────────────── */

describe("atendimentos", () => {
  it("a area principal convida a selecionar", () => {
    render(<EmptyConversation total={0} />);
    expect(screen.getByText(/selecione um atendimento para começar/i)).toBeTruthy();
  });

  it("os quatro estados aparecem separados", () => {
    render(<EmptyConversation total={0} />);
    for (const e of ["Recebida", "Visualizada", "Assumida", "Respondida"]) {
      expect(screen.getByText(e)).toBeTruthy();
    }
  });

  it("deixa claro que abrir nao e assumir", () => {
    render(<EmptyConversation total={0} />);
    expect(screen.getByText(/abrir uma conversa não é assumi-la/i)).toBeTruthy();
  });
});

/* ─── identidade do funcionario ──────────────────────────────────────── */

describe("assinatura do funcionario", () => {
  it("mostra nome e area", () => {
    render(<EmployeeBadge name="Aline" department="Fiscal" />);
    expect(screen.getByText("Aline")).toBeTruthy();
    expect(screen.getByText("Fiscal")).toBeTruthy();
  });

  it("sem departamento principal, mostra so o nome — nao inventa area", () => {
    render(<EmployeeBadge name="Aline" department={null} />);
    expect(screen.getByText("Aline")).toBeTruthy();
    expect(screen.queryByText("•")).toBeNull();
  });

  it("a previa da mensagem traz a assinatura pronta", () => {
    render(<OutgoingPreview name="Aline" department="Fiscal" />);
    expect(screen.getByText(/Aline/)).toBeTruthy();
    expect(screen.getByText(/• Fiscal/)).toBeTruthy();
    expect(screen.getByText(/preenchida automaticamente/i)).toBeTruthy();
  });

  it("sem area, a previa explica como resolver", () => {
    render(<OutgoingPreview name="Aline" department={null} />);
    expect(screen.getByText(/defina uma área principal/i)).toBeTruthy();
  });
});

/* ─── estados genericos ──────────────────────────────────────────────── */

describe("estados", () => {
  it("carregando e anunciado a leitor de tela", () => {
    render(<LoadingState />);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("erro e anunciado como alerta", () => {
    render(<ErrorState description="sem rede" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/sem rede/);
  });

  it("erro sem retry nao mostra botao que nao faz nada", () => {
    render(<ErrorState />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

/* ─── formatacao ─────────────────────────────────────────────────────── */

describe("formatacao", () => {
  it("telefone brasileiro vira mascara legivel", () => {
    expect(formatPhone("+5581999991111")).toBe("(81) 99999-1111");
    expect(formatPhone("+558133334444")).toBe("(81) 3333-4444");
  });

  it("telefone nao normalizado mostra o original", () => {
    expect(formatPhone(null, "3333-4444")).toBe("3333-4444");
    expect(formatPhone(null)).toBe("—");
  });

  it("numero de outro pais sai como esta, sem mascara errada", () => {
    expect(formatPhone("+351912345678")).toBe("+351912345678");
  });

  it("documento ganha mascara conforme o tamanho", () => {
    expect(formatDocument("11222333000181")).toBe("11.222.333/0001-81");
    expect(formatDocument("02699999999")).toBe("026.999.999-99");
    expect(formatDocument(null)).toBe("—");
    expect(formatDocument("123")).toBe("123");
  });

  it("sincronizacao de hoje diz Hoje; de outro dia mostra a data", () => {
    expect(formatSyncedAt(new Date())).toMatch(/^Hoje, /);
    const antigo = new Date();
    antigo.setDate(antigo.getDate() - 10);
    expect(formatSyncedAt(antigo)).not.toMatch(/^Hoje/);
    expect(formatSyncedAt(null)).toBe("—");
  });

  it("saudacao acompanha a hora", () => {
    expect(saudacao(new Date(2026, 0, 1, 9))).toBe("Bom dia");
    expect(saudacao(new Date(2026, 0, 1, 14))).toBe("Boa tarde");
    expect(saudacao(new Date(2026, 0, 1, 21))).toBe("Boa noite");
  });
});

/* ─── ajustes da revisão visual ──────────────────────────────────────── */

function respostaLista(counts: Record<string, number> = {}) {
  return new Response(
    JSON.stringify({
      conversations: [],
      counts: { novos: 0, naoLidos: 0, meus: 0, aguardando: 0, semSetor: 0, todos: 0, ...counts },
    }),
    { status: 200 },
  );
}

describe("filtros de atendimentos", () => {
  it("os seis recortes existem, na ordem de prioridade", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respostaLista()));
    render(<ConversationsScreen membershipId="m1" podeEnviar />);

    const grupo = await screen.findByRole("group", { name: /filtrar atendimentos/i });
    const nomes = within(grupo)
      .getAllByRole("button")
      .map((b) => b.textContent);
    // Primeiro o que exige acao; "Todos" por ultimo. "Sem setor" e a fila
    // da triagem: quem esta la nao foi para setor nenhum e precisa de gente.
    expect(nomes).toEqual([
      "Novos",
      "Não lidos",
      "Meus",
      "Aguardando",
      "Sem setor",
      "Todos",
    ]);
  });

  it("começa em Novos — a tela abre no que exige acao", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respostaLista()));
    render(<ConversationsScreen membershipId="m1" podeEnviar />);
    expect(await screen.findByRole("button", { name: /^Novos/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("trocar o filtro consulta a API com o recorte pedido", async () => {
    const chamada = vi.fn(async (url: string) =>
      new Response(
        JSON.stringify({
          conversations: [],
          counts: { novos: 0, naoLidos: 0, meus: 0, aguardando: 0, todos: 0 },
        }),
        { status: 200, headers: { "x-url": url } },
      ),
    );
    vi.stubGlobal("fetch", chamada);

    render(<ConversationsScreen membershipId="m1" podeEnviar />);
    fireEvent.click(await screen.findByRole("button", { name: /^Meus/ }));

    await waitFor(() => {
      const urls = chamada.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("filtro=meus"))).toBe(true);
    });
  });

  it("o contador só aparece quando há algo para contar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respostaLista({ novos: 3 })));
    render(<ConversationsScreen membershipId="m1" podeEnviar />);

    const novos = await screen.findByRole("button", { name: /^Novos/ });
    expect(novos.textContent).toContain("3");
    expect(screen.getByRole("button", { name: /^Meus/ }).textContent).toBe("Meus");
  });

  it("nenhum filtro inventa conversa", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respostaLista()));
    render(<ConversationsScreen membershipId="m1" podeEnviar />);

    for (const f of [/^Novos/, /^Não lidos/, /^Meus/, /^Aguardando/, /^Todos/]) {
      fireEvent.click(await screen.findByRole("button", { name: f }));
      expect(await screen.findByText(/nenhum|ninguém|já abriu/i)).toBeTruthy();
    }
  });

  it("falha ao carregar oferece tentar de novo", async () => {
    const chamada = vi
      .fn()
      .mockResolvedValueOnce(new Response("erro", { status: 500 }))
      .mockResolvedValueOnce(respostaLista());
    vi.stubGlobal("fetch", chamada);

    render(<ConversationsScreen membershipId="m1" podeEnviar />);
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/não foi possível carregar os atendimentos/i);

    fireEvent.click(screen.getByRole("button", { name: /tentar novamente/i }));
    await waitFor(() => expect(chamada).toHaveBeenCalledTimes(2));
  });
});

describe("status discreto na lista", () => {
  it("ativo e inativo são anunciados por texto, não só por cor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              customers: [CLIENTE, { ...CLIENTE, id: "c2", displayName: "Fora Ltda", active: false }],
            }),
            { status: 200 },
          ),
      ),
    );

    render(<CustomerBrowser />);
    await screen.findByText("Padaria do Joao Ltda");

    // Cor sozinha não é informação para quem não a enxerga.
    expect(screen.getAllByTitle("Ativo").length).toBe(1);
    expect(screen.getAllByTitle("Inativo").length).toBe(1);
  });

  it("a etiqueta grande de inativo saiu da lista", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ customers: [{ ...CLIENTE, active: false }] }),
            { status: 200 },
          ),
      ),
    );

    render(<CustomerBrowser />);
    await screen.findByText("Padaria do Joao Ltda");

    // A palavra continua existindo para o leitor de tela — o que saiu foi
    // a pílula colorida repetida em toda linha.
    const inativo = screen.getByText(/^Inativo$/);
    expect(inativo).toHaveClass("so-leitor");
    expect(inativo.closest(".etiqueta")).toBeNull();
  });
});

describe("permissões em português", () => {
  it("traduz o identificador técnico", () => {
    expect(rotuloPermissao("users.manage")).toBe("Gerenciar a equipe");
    expect(rotuloPermissao("audit.read")).toBe("Consultar o histórico de acessos");
  });

  it("identificador desconhecido não some da tela", () => {
    expect(rotuloPermissao("algo.novo")).toBe("algo.novo");
  });

  it("só OWNER e ADMIN veem a lista de permissões", () => {
    expect(ehAdministrador("OWNER")).toBe(true);
    expect(ehAdministrador("ADMIN")).toBe(true);
    expect(ehAdministrador("MANAGER")).toBe(false);
    expect(ehAdministrador("AGENT")).toBe(false);
    expect(ehAdministrador("VIEWER")).toBe(false);
  });

  it("o papel também aparece em português", () => {
    expect(rotuloPapel("ADMIN")).toBe("Administrador");
    expect(rotuloPapel("AGENT")).toBe("Atendimento");
  });
});

describe("a explicação some quando houver atendimento real", () => {
  it("com zero atendimentos, o guia dos quatro estados aparece", () => {
    render(<EmptyConversation total={0} />);
    expect(screen.getByText(/como o atendimento vai funcionar/i)).toBeTruthy();
    expect(screen.getByText(/selecione um atendimento para começar/i)).toBeTruthy();
  });

  it("havendo atendimento, o guia sai da frente", () => {
    // Texto didático competindo com trabalho real vira ruído.
    render(<EmptyConversation total={3} />);
    expect(screen.queryByText(/como o atendimento vai funcionar/i)).toBeNull();
    expect(screen.queryByText(/recebida/i)).toBeNull();
    expect(screen.getByText("Selecione um atendimento.")).toBeTruthy();
  });

  it("com atendimentos, o vazio do filtro sugere outro recorte", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              conversations: [],
              counts: { novos: 0, naoLidos: 0, meus: 0, aguardando: 0, todos: 5 },
            }),
            { status: 200 },
          ),
      ),
    );
    render(<ConversationsScreen membershipId="m1" podeEnviar />);
    // Ha atendimentos no escritorio, so nao neste recorte.
    expect(await screen.findByText(/nenhum atendimento novo/i)).toBeTruthy();
  });
});
