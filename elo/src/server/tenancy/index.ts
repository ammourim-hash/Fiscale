/**
 * Fachada da camada de tenancy. O resto da aplicacao importa daqui — e so
 * daqui — para chegar a dado de tenant.
 *
 * `provisionTenant` NAO sai por aqui de proposito. Criar tenant e ato
 * administrativo e vive em src/server/admin, atras de uma trava de
 * ambiente. Ver a nota naquele arquivo.
 */
export {
  assertTenantId,
  newTenantId,
  withTenant,
  withTenantOn,
  type TenantCapableClient,
  type TenantClient,
  type WithTenantOptions,
} from "./with-tenant";
