-- =====================================================================
--  Elo — isolamento entre tenants (Row Level Security)
--
--  Segunda camada de defesa. A primeira e a aplicacao sempre filtrar por
--  tenantId; esta existe para quando a primeira falhar — bug na consulta,
--  endpoint novo esquecido, IDOR. Com RLS ligado, o banco se RECUSA a
--  devolver linha de outro tenant mesmo que a consulta peca.
--
--  Contrato com a aplicacao: toda leitura ou escrita acontece dentro de uma
--  transacao que comeca com
--      SELECT set_config('app.tenant_id', '<uuid>', true);
--  O terceiro argumento `true` e o que importa: torna o ajuste LOCAL a
--  transacao. No COMMIT ou no ROLLBACK o Postgres o desfaz sozinho, entao a
--  conexao volta ao pool limpa. Com `SET` comum o valor sobreviveria na
--  conexao e o proximo tenant a peg -la herdaria o contexto do anterior.
--  Ver src/server/tenancy/with-tenant.ts e tests/pool-reuse.test.ts.
--
--  Sem app.tenant_id definido, app_tenant_id() devolve NULL, a comparacao
--  vira NULL, nenhuma linha satisfaz a politica e o resultado e vazio.
--  Falha fechada: esquecer de definir o tenant da tela vazia, nunca
--  vazamento.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. O tenant da transacao corrente.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app_tenant_id() RETURNS uuid
LANGUAGE plpgsql
STABLE            -- nao muda dentro da transacao; o planejador pode cachear
AS $$
DECLARE v text;
BEGIN
  -- `true` = nao explode se a variavel nunca foi definida.
  v := current_setting('app.tenant_id', true);
  IF v IS NULL OR v = '' THEN
    RETURN NULL;              -- sem tenant -> nenhuma linha
  END IF;
  RETURN v::uuid;
EXCEPTION WHEN others THEN
  RETURN NULL;                -- valor invalido tambem nao abre nada
END $$;

REVOKE ALL ON FUNCTION app_tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_tenant_id() TO elo_app;

-- ---------------------------------------------------------------------
-- 2. Aplicador de RLS, reexecutavel.
--
--    Toda migration futura que criar tabela com tenant_id deve terminar
--    com `SELECT elo_apply_rls();`. E aqui — e so aqui — que o elo_app
--    ganha acesso a uma tabela: nenhum GRANT sai sem RLS ligado junto.
--    Por isso o bootstrap NAO usa ALTER DEFAULT PRIVILEGES.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION elo_apply_rls() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE t text;
BEGIN
  FOR t IN
    SELECT c.relname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid
     WHERE n.nspname = 'public'
       AND c.relkind = 'r'
       AND a.attname = 'tenant_id'
       AND NOT a.attisdropped
       AND c.relname <> '_prisma_migrations'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    -- FORCE faz a politica valer TAMBEM para o dono da tabela. Sem isto,
    -- o elo_owner leria tudo — e e o erro mais comum em RLS.
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);

    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I'
      || ' USING (tenant_id = app_tenant_id())'
      || ' WITH CHECK (tenant_id = app_tenant_id())', t);

    EXECUTE format(
      'GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO elo_app', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 3. A tabela tenants nao tem tenant_id: ela E o tenant.
--    Isola pelo proprio id, com a mesma regra.
--
--    Criar um tenant novo funciona assim: a aplicacao gera o uuid, abre a
--    transacao com app.tenant_id ja igual a esse uuid e insere. O WITH
--    CHECK aprova porque id = app_tenant_id(). Nenhum caminho privilegiado
--    e necessario — ver provisionTenant() em src/server/tenancy.
-- ---------------------------------------------------------------------
ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_self ON public.tenants;
CREATE POLICY tenant_self ON public.tenants
  USING (id = app_tenant_id())
  WITH CHECK (id = app_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON public.tenants TO elo_app;

-- ---------------------------------------------------------------------
-- 4. Liga tudo que ja existe (users, contacts).
-- ---------------------------------------------------------------------
SELECT elo_apply_rls();

-- ---------------------------------------------------------------------
-- 5. O runtime nao enxerga o historico de migrations nem cria objeto.
-- ---------------------------------------------------------------------
-- O IF existe porque o shadow database que o `prisma migrate dev` cria para
-- validar a migration ainda nao tem a tabela de historico.
DO $$
BEGIN
  IF to_regclass('public._prisma_migrations') IS NOT NULL THEN
    REVOKE ALL ON TABLE public._prisma_migrations FROM elo_app;
  END IF;
END $$;

REVOKE CREATE ON SCHEMA public FROM elo_app;
