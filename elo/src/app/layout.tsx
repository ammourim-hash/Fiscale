import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { ThemeProvider, THEME_SCRIPT } from "@/components/theme";

import "./globals.css";
import "./app.css";

export const metadata: Metadata = {
  title: "Fiscale Elo",
  description: "Central de atendimento do escritório",
  // A PWA. O nome instalado é "ELO"; o completo, "Fiscale Elo —
  // atendimento", para que o ícone na barra de tarefas do Windows não
  // vire só mais um "Elo" sem sobrenome. Ver public/manifest.webmanifest.
  manifest: "/manifest.webmanifest",
  applicationName: "ELO",
  appleWebApp: {
    // O iOS ignora o manifesto para isto e usa as suas próprias metatags.
    // Ver as limitações reais do iOS no README.
    capable: true,
    title: "ELO",
    statusBarStyle: "black-translucent",
  },
  icons: {
    icon: [
      { url: "/icones/icone-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icones/icone-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/icones/icone-192.png", sizes: "192x192" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#eef3f5" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1620" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <head>
        {/* Aplica o tema ANTES da primeira pintura. Sem isto, quem usa o
            modo escuro leva um flash branco a cada carregamento. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
