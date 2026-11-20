# Arquitectura técnica — MunicipalGuard AI 0.2.1

## Estado de esta entrega

Esta versión es una base de desarrollo local. Tiene aislamiento lógico por `tenant_id` aplicado en las rutas y consultas principales, autenticación técnica por API key asignada a un tenant, Finanzas, Compras Públicas (licitaciones, proveedores, órdenes de compra), reglas determinísticas, Isolation Forest desactivado hasta evaluación representativa, gestión básica de alertas y recuperación documental TF-IDF con filtro de tenant.

Incluye historial de cambios de alertas y vigencia recalculada (`is_active`), pero no tiene todavía autenticación individual, RBAC/ABAC, PostgreSQL RLS, pgvector, OCR, integración viva con ChileCompra, ML productivo, expediente probatorio completo ni los cuatro módulos restantes. No cargar datos institucionales reales.

## Componentes

- `Control Plane` (futuro): tenants, instituciones, usuarios, planes, módulos y políticas globales.
- `Data Plane`: registros y documentos aislados por tenant.
- FastAPI: API versionada conceptualmente; endpoints agrupados por dominio.
- SQLAlchemy: modelo relacional compatible con SQLite y PostgreSQL.
- Streamlit: interfaz de demostración.
- RAG: TF-IDF léxico por institución; no es búsqueda vectorial semántica.

## Aislamiento de tenant en 0.2.1

La API key se mapea a un único `tenant_id` desde `MGAI_API_KEYS_JSON`. El tenant no se acepta desde un parámetro arbitrario del usuario. Las consultas de dominio filtran por el tenant autenticado. La base compartida usa columnas `tenant_id` e índices. Esta defensa es útil para pruebas de concepto, pero no equivale a un control de seguridad institucional completo: faltan usuarios individuales, roles, PostgreSQL Row-Level Security, aislamiento de workers/cachés/exportaciones y pruebas de penetración.

Para agregar una institución local, configure una clave diferente por tenant:

```json
{"clave-larga-institucion-a":"institution-a","clave-larga-institucion-b":"institution-b"}
```

Use claves aleatorias largas, secret manager y TLS en cualquier despliegue real. No almacene credenciales en Git.

## Dominios implementados

### Finanzas

Importación CSV/XLSX de movimientos; parseo explícito de fechas chilenas y CLP; validación de RUT; deduplicación conservadora de reimportación por RUT + folio; los duplicados del mismo archivo se conservan; reglas para factura posiblemente duplicada y monto por sobre presupuesto de referencia.

### Compras públicas

Importación CSV/XLSX de licitaciones y órdenes de compra; registro básico de proveedores; ficha Supplier 360 local que agrega adjudicaciones y órdenes de compra; reglas de revisión para procesos con una oferta y adjudicación por sobre estimación.

Una oferta o una diferencia sobre estimación no es irregularidad por sí sola. Debe considerarse mercado, bases, impuestos, alcance, modificaciones y antecedentes.

### RAG

PDF/TXT -> extracción de texto -> chunks -> TF-IDF -> recuperación dentro del tenant. Para PDF se conserva página. No hay LLM ni embeddings vectoriales en esta versión.

## Próximos incrementos técnicos

1. Endurecer migraciones Alembic y validar upgrades desde bases anteriores (0.2.0 no se migra automáticamente).
2. OIDC/SSO, usuarios, RBAC/ABAC, MFA y gestión de sesiones.
3. PostgreSQL RLS, pruebas negativas de aislamiento y tenant context transaccional.
4. pgvector, embeddings locales, búsqueda híbrida BM25/vector, reranking y ACL por documento/unidad.
5. Conector ChileCompra basado en documentación y autorización vigente; fixtures reproducibles antes de llamadas reales.
6. Case management con evidencia hash, historial append-only y aprobación de supervisor.
7. Los módulos Personal, Obras, Beneficios y Flota/Inventarios.
8. Evaluación ML temporal, model registry, drift y revisión humana.
9. Observabilidad, backup/restore, retención, threat modeling y pentest independiente.
