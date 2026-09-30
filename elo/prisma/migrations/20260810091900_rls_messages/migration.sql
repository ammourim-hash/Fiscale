-- =====================================================================
--  Elo — RLS do motor de mensagens
--
--  Duas tabelas novas, as duas com tenant_id: messages e message_views.
--  Nenhuma tabela global desta vez.
--
--  `elo_apply_rls()` liga ENABLE + FORCE, cria a politica com USING e
--  WITH CHECK e so entao concede acesso ao elo_app — nesta ordem, para que
--  nao exista instante em que a tabela esteja legivel e desprotegida.
--
--  O que isso garante: mensagem do tenant A nao pode ser lida, listada,
--  respondida nem marcada como visualizada pelo tenant B. Quem garante e o
--  banco, e nao o `where` da consulta. O mesmo vale para o realtime: o que
--  o servidor nao consegue LER do banco, ele nao tem como publicar.
-- =====================================================================

SELECT elo_apply_rls();
