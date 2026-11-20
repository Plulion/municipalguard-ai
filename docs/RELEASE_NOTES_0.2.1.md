# Release notes — MunicipalGuard AI 0.2.1

## Correcciones principales

1. Fechas: parseo explícito de `DD/MM/AAAA` y `AAAA-MM-DD`; no se usa la heurística regional de pandas.
2. CLP: `Decimal`, notación científica soportada como valor numérico; formatos ambiguos y montos fraccionarios rechazados; separador chileno de miles con punto.
3. Celdas: `clean_text()` centraliza nulos de pandas/Excel y no persiste `nan` como texto.
4. Encabezados: alias de licitación y OC separados; colisiones detectadas y rechazadas.
5. RUT: se eliminan puntos y se valida módulo 11; formato canónico `XXXXXXXX-DV`.
6. Licitaciones/OC: upsert por tenant e identificador externo, con contadores `imported` y `updated`.
7. Finanzas: deduplicación solo cuando existen RUT y folio; duplicados dentro del mismo archivo se conservan para detección; sin identidad de factura no se eliminan movimientos idénticos.
8. Notas de crédito: montos negativos permitidos; reglas de duplicidad no alertan sobre importes negativos.
9. Límites de columnas: validación previa para evitar fallos de longitud en PostgreSQL.
10. Isolation Forest: desactivado hasta disponer de evaluación con datos municipales representativos y separación de signos/tipos.
11. Alertas: motivo obligatorio y tabla `alert_audit_events`; endpoint GET de historial.
11.1. Reglas: las alertas se recalculan; si la condición desaparece, quedan históricas con `is_active=false`, y si reaparece se reactivan.
12. Seguridad: API keys sin valor por defecto, comparación de bytes, imagen Docker no-root, `.dockerignore`, puertos de Compose en loopback, clave de demo configurada por el usuario.
13. Base de datos: migración inicial Alembic y auto-creación de esquema desactivada por defecto.
14. Dependencias: FastAPI 0.128.2, Streamlit 1.67.0, Requests 2.34.2 y pypdf 6.4.0; Starlette queda como dependencia transitiva resuelta por FastAPI, publicados antes del 19-11-2026. La auditoría CVE automatizada queda pendiente de ejecutar en un entorno con acceso a OSV/PyPI. Las herramientas de test/auditoría están separadas en `requirements-dev.txt`; se incluyen `httpx` y `httpx2` para compatibilidad del cliente de pruebas según la versión resuelta de Starlette.

## Compatibilidad y limitaciones

- La migración inicial se generó y probó en SQLite.
- PostgreSQL y Docker no pudieron ejecutarse en este entorno.
- El código fue probado con las versiones de librerías instaladas en el entorno de construcción; las nuevas versiones fijadas en `requirements.txt` requieren instalación limpia y prueba de aceptación en el Mac.
- El historial de alertas identifica la clave técnica/tenant, no una persona individual.
- No hay RLS, RBAC, MFA ni autenticación individual.
- No hay integración ChileCompra ni módulos de personal, obras, beneficios o flota.
