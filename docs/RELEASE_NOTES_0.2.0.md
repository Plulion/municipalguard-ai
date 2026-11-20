# Release notes — 0.2.0

## Delivered
- Tenant entity and API-key-to-tenant resolution.
- Tenant-scoped financial transactions, procurement tenders, purchase orders, suppliers, alerts and RAG chunks.
- Financial CSV/XLSX import and duplicate re-import suppression for records with supplier ID + invoice folio.
- Procurement tender and purchase-order CSV/XLSX import with batch idempotency by external ID.
- Supplier 360 aggregation across awarded tenders and purchase orders.
- Deterministic review rules for possible duplicate invoices, budget reference variance, single-offer tenders and award-over-estimate.
- Experimental Isolation Forest for financial datasets with at least 30 records. This is not a validated fraud model.
- Local TF-IDF document retrieval with tenant filtering and PDF page references.
- Docker Compose profile using PostgreSQL 16.

## Verification performed in build environment
- Python compilation: passed.
- Unit tests: 6 passed.
- FastAPI TestClient smoke test: health, authenticated institution and summary, finance import, tender import, purchase order import, analysis, supplier listing, Supplier 360, alerts, document indexing, RAG retrieval and missing-key rejection.
- Docker runtime was not executed in this build environment. Docker Compose YAML syntax was parsed.

## Known limitations / blockers before institutional pilot
- API key is a shared technical credential, not individual user identity. No OIDC/SSO, MFA, RBAC or ABAC.
- Tenant isolation is implemented in application queries but not yet reinforced by PostgreSQL RLS. Independent penetration/security testing is required.
- No Alembic migration history; schema bootstrap currently uses SQLAlchemy `create_all`. Do not upgrade an existing production database with this approach.
- No transactional outbox, background worker, append-only audit log, immutable evidence store or case workflow.
- RAG is TF-IDF lexical retrieval, not vector embeddings/pgvector, and has no LLM answer generation.
- ChileCompra connector is not implemented. Imports use local CSV/XLSX files and no external calls.
- Contracts, amendments, receipts and payment matching are not yet implemented as full procurement workflow.
- Personal, Works, Benefits, Fleet and Inventory domains are not implemented.
- ML is exploratory and not validated on real, authorized, representative institutional data.
