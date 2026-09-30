-- =====================================================================
--  Elo — RLS da midia
--
--  Uma tabela nova com tenant_id: message_attachments. Mesmo tratamento
--  de sempre — ENABLE + FORCE + USING + WITH CHECK, e o GRANT so depois
--  da politica existir.
--
--  Aqui o isolamento tem uma consequencia a mais que nas outras tabelas:
--  o download le a linha para descobrir a `storage_key`. Sem passar pelo
--  banco nao ha como saber a chave, e a linha de outro tenant nao existe
--  para quem pergunta. O caminho no storage tambem carrega o tenant no
--  prefixo, entao as duas camadas teriam de falhar juntas.
-- =====================================================================

SELECT elo_apply_rls();
