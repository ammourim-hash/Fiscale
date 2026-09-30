/**
 * A entrada de mensagem de DESENVOLVIMENTO.
 *
 * O WhatsApp ainda não existe, e sem uma forma de fazer chegar mensagem
 * não há como testar recebimento, não lidas, realtime nem visualização
 * individual. Isto é o substituto — e precisa ser impossível de deixar
 * ligado por acidente.
 *
 * Três travas, as mesmas do seed, pelo mesmo motivo:
 *
 *   1. recusa com NODE_ENV=production;
 *   2. exige ELO_ALLOW_DEV_INBOUND=1;
 *   3. tudo que entra por aqui é marcado com o canal "DEV" e o remetente
 *      externo "dev", então o que veio daqui é reconhecível na tabela.
 *
 * A verificação mora NESTE arquivo, e não na rota, porque um dia haverá
 * outro chamador (um script, um teste) e a trava não pode depender de
 * quem chama lembrar dela.
 */
import { env } from "@/env";

export const CANAL_DEV = "DEV";
export const REMETENTE_DEV = "dev";

export interface EstadoDaEntradaDev {
  liberada: boolean;
  motivo: string | null;
}

export function entradaDevLiberada(): EstadoDaEntradaDev {
  if (env.NODE_ENV === "production") {
    return { liberada: false, motivo: "NODE_ENV=production" };
  }
  if (process.env.ELO_ALLOW_DEV_INBOUND !== "1") {
    return { liberada: false, motivo: "ELO_ALLOW_DEV_INBOUND ausente" };
  }
  return { liberada: true, motivo: null };
}
