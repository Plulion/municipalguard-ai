# Informe de auditoría técnica — MunicipalGuard AI 0.2.0

**Resultado:** MVP local de portafolio ejecutable a nivel API y base de datos SQLite; no apto para producción ni para cargar datos institucionales reales.

## 1. Hallazgos relevantes de 0.1.0

- No había autenticación en rutas de datos, alertas ni documentos.
- No existía `tenant_id` en entidades; no había aislamiento entre instituciones.
- Persistencia SQLite local y sin migraciones versionadas.
- Solo existía el dominio de transacciones financieras.
- Los documentos RAG se consultaban sin ámbito institucional.
- No existía una ficha agregada de proveedores ni ciclo de compras.

## 2. Cambios incorporados en 0.2.0

- Tabla `Tenant` y perfil institucional básico.
- Mapeo de API key a un único tenant; el tenant se deriva de la credencial y no de un parámetro enviado por el cliente.
- Filtro por tenant en movimientos, licitaciones, órdenes, proveedores, alertas y fragmentos RAG.
- Importación CSV/XLSX de finanzas, licitaciones y órdenes de compra.
- Deduplicación de reimportaciones por fingerprint financiero, ID externo de licitación y número de orden. Los duplicados contenidos dentro del primer archivo se conservan para que puedan generar una señal de revisión.
- Supplier 360 con adjudicaciones y órdenes asociadas por identificador tributario.
- Reglas financieras y de compras públicas con explicaciones y prioridad de revisión.
- Isolation Forest experimental solo desde 30 movimientos; no está validado como detector de fraude.
- RAG TF-IDF con recuperación filtrada por tenant y página para PDF.
- Límite de tamaño de carga y manejo de errores de importación sin exponer trazas internas.
- Compose con PostgreSQL 16 para pruebas locales mediante Docker.

## 3. Pruebas ejecutadas

- `python -m compileall -q backend frontend tests`: PASS.
- `pytest -q`: 6 PASS.
- FastAPI TestClient: health, institución autenticada, summary, importación de CSV de finanzas, licitaciones y órdenes, reimportación idempotente, análisis, proveedores, Supplier 360, alertas, indexación y consulta RAG: PASS.
- Prueba con dos API keys y dos tenants: los movimientos, licitaciones y fragmentos RAG del tenant A no aparecen al tenant B: PASS.
- Solicitud sin API key: HTTP 401: PASS.
- Docker Compose YAML parse: PASS.

## 4. Pruebas no ejecutadas

- Docker runtime: no ejecutado en el entorno de construcción porque el binario Docker no estaba disponible.
- Dashboard Streamlit: compilación sintáctica verificada, pero no se inició en el entorno de construcción.
- PostgreSQL real: no se levantó servidor PostgreSQL; el smoke test funcional se realizó con SQLite.
- Pruebas de carga, penetración, restauración de backups y revisión legal: no ejecutadas.

## 5. Bloqueadores para un piloto real

1. Sustituir API keys compartidas por OIDC/SSO, MFA, usuarios, roles y autorización ABAC.
2. Implementar PostgreSQL RLS y pruebas de seguridad cross-tenant; la aplicación no debe depender solo de filtros ORM.
3. Introducir Alembic y migraciones revisadas; actualmente se usa `Base.metadata.create_all` para bootstrap.
4. Crear auditoría append-only, evidencia con hashes, case management y segregación de funciones.
5. Implementar backups cifrados, recuperación probada, TLS, gestión de secretos y observabilidad.
6. Implementar conectores ChileCompra únicamente después de verificar documentación, ticket, límites, términos y campos disponibles. Esta versión solo importa CSV/XLSX.
7. Ampliar compras a contratos, modificaciones, recepciones, garantías, facturas y pagos.
8. Implementar los módulos Personal, Obras, Beneficios, Flota e Inventarios con controles sectoriales.
9. Evaluar RAG híbrido con pgvector, embeddings locales, permisos por documento/unidad y defensa contra prompt injection.
10. Validar ML con datos autorizados, representativos y etiquetas gobernadas; medir falsos positivos y capacidad real de revisión.
11. Revisión de privacidad, seguridad y normativa aplicable por profesionales competentes antes del piloto.

## 6. Dictamen

**Aprobado como prototipo técnico local para portafolio y demostración con datos sintéticos. No aprobado para producción, decisiones administrativas ni tratamiento de información institucional real.**
