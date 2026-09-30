// @vitest-environment jsdom
/**
 * Modo embutido: a regra que decide, e a que preserva.
 *
 * O modo mora na URL de propósito (nada de cookie ou localStorage), e por
 * isso ele só sobrevive se cada navegação interna o levar junto. Uma
 * navegação que esquecesse o parâmetro traria a casca do Elo de volta
 * DENTRO da casca do FISCALE — que é exatamente o que o modo existe para
 * evitar.
 *
 * Os dois lados são testados: ligar o modo quando a URL manda, e NÃO ligar
 * quando ela não manda. Um teste só do primeiro deixaria passar uma regra
 * que liga o modo em qualquer URL.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { comModo, modoEmbutido } from "@/lib/embutido";
import { LinkModo, ProvedorDeModo, useIrPara } from "@/components/embutido";

const empurrar = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/atendimentos",
  useRouter: () => ({ push: empurrar, replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  cleanup();
  empurrar.mockClear();
});

describe("modoEmbutido — quando o modo liga", () => {
  it("liga com ?embutido=1", () => {
    expect(modoEmbutido("/atendimentos?embutido=1")).toBe(true);
  });

  it("liga também quando vem junto de outros parâmetros", () => {
    expect(modoEmbutido("/atendimentos?abrir=abc&embutido=1")).toBe(true);
    expect(modoEmbutido("/atendimentos?embutido=1&abrir=abc")).toBe(true);
  });
});

describe("modoEmbutido — quando NÃO liga", () => {
  it("não liga com embutido=11", () => {
    expect(modoEmbutido("/atendimentos?embutido=11")).toBe(false);
  });

  it("não liga com valor diferente de 1", () => {
    expect(modoEmbutido("/atendimentos?embutido=0")).toBe(false);
    expect(modoEmbutido("/atendimentos?embutido=true")).toBe(false);
  });

  it("não liga com nome parecido", () => {
    expect(modoEmbutido("/atendimentos?naoembutido=1")).toBe(false);
    expect(modoEmbutido("/atendimentos?embutidos=1")).toBe(false);
  });

  it("não liga sem o parâmetro", () => {
    expect(modoEmbutido("/atendimentos")).toBe(false);
    expect(modoEmbutido("/atendimentos?abrir=abc")).toBe(false);
  });

  it("não liga sem caminho nenhum", () => {
    expect(modoEmbutido(null)).toBe(false);
    expect(modoEmbutido(undefined)).toBe(false);
    expect(modoEmbutido("")).toBe(false);
  });
});

describe("comModo — preservar sem poluir", () => {
  it("acrescenta o modo a um caminho simples", () => {
    expect(comModo("/clientes", true)).toBe("/clientes?embutido=1");
  });

  it("acrescenta com & quando já existe query", () => {
    expect(comModo("/atendimentos?abrir=abc", true)).toBe("/atendimentos?abrir=abc&embutido=1");
  });

  it("não duplica o que já está lá", () => {
    expect(comModo("/clientes?embutido=1", true)).toBe("/clientes?embutido=1");
  });

  it("preserva a âncora no fim", () => {
    expect(comModo("/clientes#conteudo", true)).toBe("/clientes?embutido=1#conteudo");
  });

  it("não toca em destino externo", () => {
    expect(comModo("https://exemplo.com.br/x", true)).toBe("https://exemplo.com.br/x");
    expect(comModo("//exemplo.com.br/x", true)).toBe("//exemplo.com.br/x");
  });

  it("fora do modo, devolve o destino intocado", () => {
    expect(comModo("/clientes", false)).toBe("/clientes");
    expect(comModo("/atendimentos?abrir=abc", false)).toBe("/atendimentos?abrir=abc");
  });
});

describe("navegação por link", () => {
  it("embutido: o link leva o modo junto", () => {
    render(
      <ProvedorDeModo embutido>
        <LinkModo href="/clientes">Clientes</LinkModo>
      </ProvedorDeModo>,
    );
    expect(screen.getByRole("link", { name: "Clientes" })).toHaveAttribute(
      "href",
      "/clientes?embutido=1",
    );
  });

  it("normal: o link NÃO ganha o parâmetro", () => {
    render(
      <ProvedorDeModo embutido={false}>
        <LinkModo href="/clientes">Clientes</LinkModo>
      </ProvedorDeModo>,
    );
    expect(screen.getByRole("link", { name: "Clientes" })).toHaveAttribute("href", "/clientes");
  });

  it("sem provedor, o padrão é o modo normal", () => {
    render(<LinkModo href="/clientes">Clientes</LinkModo>);
    expect(screen.getByRole("link", { name: "Clientes" })).toHaveAttribute("href", "/clientes");
  });
});

describe("navegação por código (clique em notificação)", () => {
  function Botao() {
    const irPara = useIrPara();
    return (
      <button type="button" onClick={() => irPara("/atendimentos?abrir=abc")}>
        ir
      </button>
    );
  }

  it("embutido: o push leva o modo junto", () => {
    render(
      <ProvedorDeModo embutido>
        <Botao />
      </ProvedorDeModo>,
    );
    screen.getByRole("button", { name: "ir" }).click();
    expect(empurrar).toHaveBeenCalledWith("/atendimentos?abrir=abc&embutido=1");
  });

  it("normal: o push vai como está", () => {
    render(
      <ProvedorDeModo embutido={false}>
        <Botao />
      </ProvedorDeModo>,
    );
    screen.getByRole("button", { name: "ir" }).click();
    expect(empurrar).toHaveBeenCalledWith("/atendimentos?abrir=abc");
  });
});
