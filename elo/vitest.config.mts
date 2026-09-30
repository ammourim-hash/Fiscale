import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // Padrao: Node, porque a maioria dos testes fala com o banco. Os de
    // interface pedem jsdom com `// @vitest-environment jsdom` no topo do
    // arquivo — mais simples do que manter dois projetos de teste.
    environment: "node",
    globals: true,
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    setupFiles: ["tests/setup.ts"],
    // Os testes de isolamento compartilham o mesmo banco e criam tenants.
    // Em paralelo eles se atrapalhariam e o resultado deixaria de significar
    // alguma coisa. Um arquivo por vez.
    fileParallelism: false,
    hookTimeout: 30_000,
    testTimeout: 30_000,
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
