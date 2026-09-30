// Flat config nativo. O eslint-config-next 16 ja exporta flat config pronto;
// passar por FlatCompat quebra (referencia circular no plugin react).
import coreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "src/generated/**",
      "next-env.d.ts",
    ],
  },
  ...coreWebVitals,
  ...nextTypescript,
  {
    rules: {
      // Log estruturado existe justamente para nao haver console solto.
      "no-console": ["error", { allow: ["error"] }],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      eqeqeq: ["error", "smart"],
    },
  },
  {
    // O logger e o unico lugar que escreve na saida padrao.
    files: ["src/server/logging/**"],
    rules: { "no-console": "off" },
  },
  {
    files: ["tests/**", "scripts/**"],
    rules: { "no-console": "off" },
  },
];

export default config;
