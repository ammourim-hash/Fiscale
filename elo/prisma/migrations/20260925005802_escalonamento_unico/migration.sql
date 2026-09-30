-- Um escalonamento por conversa, garantido pelo BANCO.
--
-- A primeira versao lia "ja existe ESCALATED?" e so entao gravava. Duas
-- varreduras simultaneas — o webhook e a tarefa periodica caindo no mesmo
-- segundo — leem as duas "nao existe" e gravam as duas. Transacao nao
-- resolve: em READ COMMITTED nenhuma enxerga a linha que a outra ainda
-- nao confirmou, e o INSERT nao tem com o que conflitar.
--
-- O indice PARCIAL da ao banco o que o codigo nao consegue prometer. E
-- parcial de proposito: `conversation_events` guarda VARIOS eventos do
-- mesmo tipo por conversa (TRANSFERRED, STATUS_CHANGED, VIEWED...), e um
-- unico sobre (conversation_id, type) quebraria todos eles. So o
-- ESCALATED e singular, porque so ele e uma decisao de "avisar uma vez".
--
-- Com o indice, o INSERT ... ON CONFLICT DO NOTHING vira a trava: quem
-- grava primeiro avisa, o outro descobre que perdeu e nao avisa.
CREATE UNIQUE INDEX IF NOT EXISTS "conversation_events_escalated_unico"
    ON "conversation_events" ("conversation_id")
 WHERE "type" = 'ESCALATED';
