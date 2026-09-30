/**
 * Validacao do ambiente. Roda uma vez, na primeira importacao.
 *
 * Ambiente errado deve derrubar a aplicacao na partida, com o nome da
 * variavel que falta — nao virar `undefined` que so aparece as 3h da manha
 * como "connection string invalida". A mensagem cita o NOME, nunca o VALOR.
 */
import { z } from "zod";

const schema = z.object({
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),

  DATABASE_URL: z
    .string()
    .min(1)
    .refine((v) => v.startsWith("postgres://") || v.startsWith("postgresql://"), {
      message: "precisa ser uma URL postgresql://",
    }),

  // So o `prisma migrate` usa. A aplicacao nunca abre esta conexao.
  DATABASE_MIGRATION_URL: z.string().optional(),

  PORT: z.coerce.number().int().positive().default(3000),

  LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),

  // --- Token de troca Fiscale -> Elo --------------------------------
  ELO_EXCHANGE_ISSUER: z.string().min(1).default("fiscale.local"),
  ELO_EXCHANGE_AUDIENCE: z.string().min(1).default("elo"),

  /**
   * JSON `{"<kid>": "<chave publica Ed25519 em SPKI DER, base64>"}`.
   * Mais de uma entrada e o que permite rotacionar sem parada: durante a
   * virada as duas ficam validas e o `kid` do token escolhe a certa.
   * So chave PUBLICA. A privada nunca sai do Fiscale.
   */
  ELO_EXCHANGE_PUBLIC_KEYS: z
    .string()
    .min(2)
    .default("{}")
    .refine((v) => {
      try {
        const o: unknown = JSON.parse(v);
        return typeof o === "object" && o !== null && !Array.isArray(o);
      } catch {
        return false;
      }
    }, { message: "precisa ser um objeto JSON { kid: chavePublicaBase64 }" }),

  /** Teto de vida do token de troca. O Fiscale emite 90 s; isto e o limite. */
  ELO_EXCHANGE_MAX_LIFETIME_SECONDS: z.coerce.number().int().positive().max(600).default(120),

  // --- Sessao -------------------------------------------------------
  ELO_SESSION_TTL_HOURS: z.coerce.number().int().positive().max(720).default(12),

  /** Falso so em desenvolvimento, onde o Elo roda em http. */
  ELO_COOKIE_SECURE: z
    .enum(["true", "false"])
    .default("false")
    .transform((v) => v === "true"),

  /** Sal do hash de IP. Sem ele, o hash de um IPv4 e quebravel por forca bruta. */
  ELO_IP_HASH_SALT: z.string().min(8).default("elo-dev-salt-troque-em-producao"),

  // --- Integracao de maquina (sincronizacao de clientes) ------------
  /**
   * JSON `{"<kid>": {"tenantId": "<uuid>", "source": "FISCALE",
   *                  "publicKey": "<Ed25519 SPKI DER base64>"}}`.
   *
   * O tenant vem DAQUI, nunca do corpo da requisicao — e a chave que diz
   * qual escritorio aquela integracao pode alimentar. Chave publica so;
   * a privada fica na maquina do Fiscale.
   */
  ELO_INTEGRATION_PUBLIC_KEYS: z
    .string()
    .min(2)
    .default("{}")
    .refine((v) => {
      try {
        const o: unknown = JSON.parse(v);
        return typeof o === "object" && o !== null && !Array.isArray(o);
      } catch {
        return false;
      }
    }, { message: "precisa ser um objeto JSON { kid: {tenantId, publicKey} }" }),

  /** Teto de clientes por requisicao de sincronizacao. */
  ELO_SYNC_MAX_CUSTOMERS: z.coerce.number().int().positive().max(5000).default(1000),

  // --- Web Push (MVP 1.7) -------------------------------------------
  /**
   * Chave PUBLICA VAPID: 65 bytes crus em base64url (87 caracteres).
   *
   * Esta chave PODE ir para o navegador — e o `applicationServerKey` que
   * o `pushManager.subscribe` exige. Mesmo assim ela nao usa o prefixo
   * `NEXT_PUBLIC_`: quem a entrega e uma rota autenticada. Duas variaveis
   * com nomes parecidos, uma publica por convencao do framework e outra
   * secreta, e como o segredo acaba num bundle.
   */
  ELO_VAPID_PUBLIC_KEY: z.string().default(""),

  /**
   * Chave PRIVADA VAPID: PKCS#8 DER em base64.
   *
   * SO SERVIDOR. Nunca vai para o cliente, nunca aparece em resposta de
   * API, nunca entra em log. Quem a tem consegue assinar avisos em nome do
   * Elo para qualquer aparelho inscrito.
   */
  ELO_VAPID_PRIVATE_KEY: z.string().default(""),

  /**
   * Contato exigido pelo RFC 8292 — `mailto:` ou `https:`. O provedor de
   * push usa para avisar quando algo esta errado com os nossos envios.
   */
  ELO_VAPID_SUBJECT: z
    .string()
    .default("mailto:elo@fiscale.local")
    .refine((v) => v.startsWith("mailto:") || v.startsWith("https://"), {
      message: "precisa comecar com mailto: ou https://",
    }),

  /**
   * Desliga o envio sem apagar as chaves. Util em homologacao e para
   * cortar o trafego de push sem mexer no resto do sistema.
   */
  ELO_PUSH_ENABLED: z
    .enum(["true", "false"])
    .default("true")
    .transform((v) => v === "true"),

  // --- WhatsApp Cloud API (MVP 1.8.1 — NÚMERO DE TESTE) --------------
  //
  // Nenhum destes valores vai para o navegador. O teste estrutural em
  // tests/pwa.test.ts confere que só `env.ts` e os módulos de servidor os
  // conhecem — a mesma trava criada para a chave VAPID.

  /**
   * App Secret do aplicativo Meta.
   *
   * SO SERVIDOR, e usado para UMA coisa: conferir o
   * `X-Hub-Signature-256` de cada webhook. Quem o tem consegue FORJAR
   * webhook em nome da Meta.
   */
  ELO_WHATSAPP_APP_SECRET: z.string().default(""),

  /**
   * Token de verificação do webhook. Valor inventado por nós, colado nos
   * dois lados: aqui e no painel da Meta. So aparece no handshake inicial.
   */
  ELO_WHATSAPP_VERIFY_TOKEN: z.string().default(""),

  /**
   * Token de acesso para CHAMAR a Graph API.
   *
   * Na fase de teste e o token temporario do painel; em producao vira
   * token de system user. Quem o tem envia mensagem em nome do numero.
   */
  ELO_WHATSAPP_ACCESS_TOKEN: z.string().default(""),

  /**
   * Qual número pertence a qual escritório.
   *
   * JSON `{"<phone_number_id>": {"tenantId": "<uuid>"}}`.
   *
   * O TENANT VEM DAQUI, nunca do corpo do webhook — mesma regra da
   * integração de sincronização (S19). E há um motivo técnico além da
   * segurança: o webhook chega com `phone_number_id` e SEM tenant, então
   * descobrir o dono lendo uma tabela exigiria abrir o contexto de RLS
   * antes de saber qual é o tenant. É a mesma circularidade de S12, e a
   * saída é a mesma: a configuração é a autoridade.
   *
   * Com muitos números (Embedded Signup, fase 1.8.4) isto vira tabela, e
   * a circularidade volta a ser um problema a resolver — está registrado.
   */
  ELO_WHATSAPP_NUMBERS: z
    .string()
    .min(2)
    .default("{}")
    .refine((v) => {
      try {
        const o: unknown = JSON.parse(v);
        return typeof o === "object" && o !== null && !Array.isArray(o);
      } catch {
        return false;
      }
    }, { message: "precisa ser um objeto JSON { phoneNumberId: {tenantId} }" }),

  /** Versão da Graph API. Fixa de propósito: subir versão é decisão. */
  ELO_WHATSAPP_API_VERSION: z.string().default("v23.0"),

  /**
   * Desliga o ENVIO sem apagar as credenciais. O recebimento continua —
   * é útil para observar webhook sem responder nada.
   */
  ELO_WHATSAPP_ENABLED: z
    .enum(["true", "false"])
    .default("false")
    .transform((v) => v === "true"),
});

export type Env = z.infer<typeof schema>;

function load(): Env {
  const parsed = schema.safeParse(process.env);

  if (!parsed.success) {
    // Só os nomes. O valor de uma variavel mal preenchida costuma ser
    // exatamente o segredo que nao pode aparecer em log.
    const nomes = parsed.error.issues
      .map((i) => `${i.path.join(".")}: ${i.message}`)
      .join("; ");
    throw new Error(`Ambiente invalido — ${nomes}`);
  }

  return parsed.data;
}

export const env: Env = load();

export const isProduction = env.NODE_ENV === "production";
