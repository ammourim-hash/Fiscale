-- CreateEnum
CREATE TYPE "phone_status" AS ENUM ('OK', 'NO_AREA_CODE', 'INVALID');

-- CreateTable
CREATE TABLE "external_customer_references" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "source" TEXT NOT NULL DEFAULT 'FISCALE',
    "external_id" TEXT NOT NULL,
    "display_name" TEXT NOT NULL,
    "document_digits" TEXT,
    "document_kind" TEXT,
    "email" TEXT,
    "active" BOOLEAN NOT NULL DEFAULT true,
    "deactivated_at" TIMESTAMPTZ(6),
    "source_version" TEXT NOT NULL,
    "content_hash" TEXT NOT NULL,
    "source_updated_at" TIMESTAMPTZ(6),
    "synced_at" TIMESTAMPTZ(6) NOT NULL,
    "missing_since" TIMESTAMPTZ(6),
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "external_customer_references_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "external_customer_phones" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "customer_id" UUID NOT NULL,
    "raw" TEXT NOT NULL,
    "e164" TEXT,
    "status" "phone_status" NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "external_customer_phones_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "sync_runs" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "source" TEXT NOT NULL DEFAULT 'FISCALE',
    "mode" TEXT NOT NULL,
    "received" INTEGER NOT NULL DEFAULT 0,
    "created" INTEGER NOT NULL DEFAULT 0,
    "updated" INTEGER NOT NULL DEFAULT 0,
    "unchanged" INTEGER NOT NULL DEFAULT 0,
    "reactivated" INTEGER NOT NULL DEFAULT 0,
    "deactivated" INTEGER NOT NULL DEFAULT 0,
    "marked_missing" INTEGER NOT NULL DEFAULT 0,
    "rejected" INTEGER NOT NULL DEFAULT 0,
    "started_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finished_at" TIMESTAMPTZ(6),
    "status" TEXT NOT NULL DEFAULT 'OK',

    CONSTRAINT "sync_runs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "consumed_integration_nonces" (
    "nonce" TEXT NOT NULL,
    "expires_at" TIMESTAMPTZ(6) NOT NULL,
    "consumed_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "consumed_integration_nonces_pkey" PRIMARY KEY ("nonce")
);

-- CreateIndex
CREATE INDEX "external_customer_references_tenant_id_document_digits_idx" ON "external_customer_references"("tenant_id", "document_digits");

-- CreateIndex
CREATE INDEX "external_customer_references_tenant_id_display_name_idx" ON "external_customer_references"("tenant_id", "display_name");

-- CreateIndex
CREATE UNIQUE INDEX "external_customer_references_tenant_id_source_external_id_key" ON "external_customer_references"("tenant_id", "source", "external_id");

-- CreateIndex
CREATE INDEX "external_customer_phones_tenant_id_e164_idx" ON "external_customer_phones"("tenant_id", "e164");

-- CreateIndex
CREATE UNIQUE INDEX "external_customer_phones_customer_id_raw_key" ON "external_customer_phones"("customer_id", "raw");

-- CreateIndex
CREATE INDEX "sync_runs_tenant_id_started_at_idx" ON "sync_runs"("tenant_id", "started_at");

-- CreateIndex
CREATE INDEX "consumed_integration_nonces_expires_at_idx" ON "consumed_integration_nonces"("expires_at");

-- AddForeignKey
ALTER TABLE "external_customer_phones" ADD CONSTRAINT "external_customer_phones_customer_id_fkey" FOREIGN KEY ("customer_id") REFERENCES "external_customer_references"("id") ON DELETE CASCADE ON UPDATE CASCADE;
