# Informe de auditoría — MunicipalGuard AI 0.2.1

**Fecha:** 19-11-2026  
**Resultado:** PASS para pruebas locales de regresión con SQLite; NO APROBADO para datos reales o producción.

El ZIP final incluye `SHA256SUMS.txt` con 35 archivos. La verificación posterior a la compresión confirmó los 35 checksums.

## Evidencia ejecutada

- `python -m compileall -q backend frontend tests`: PASS.
- `pytest -q`: PASS (17 pruebas).
- TestClient contra FastAPI real con dos API keys/tenants: PASS para el recorrido probado.
- Consulta de finanzas tenant A no entrega registros de tenant B: PASS.
- Recuperación RAG tenant A no entrega fragmentos documentales a tenant B: PASS.
- Historial de alertas aislado por tenant: PASS.
- `alembic upgrade head` sobre base SQLite vacía: PASS.
- `alembic current`: PASS.
- `alembic downgrade base` y `upgrade head` en base temporal SQLite: PASS.
- Re-evaluación de alertas: condición que deja de cumplirse marca `is_active=false` sin borrar el registro: PASS.
- YAML de `docker-compose.yml`: parseo PASS; URL del dashboard es `http://api:8000`.
- Revisión de `.dockerignore`, `USER app`, `COPY --chown`, y publicación de puertos `127.0.0.1`: PASS por inspección.

## Pruebas no ejecutadas

- Docker build/up: Docker Engine no está disponible en el entorno.
- PostgreSQL real: no hay servidor PostgreSQL disponible.
- `pip-audit`: el ejecutable no está instalado y no se pudo ejecutar una auditoría CVE completa.
- `pip check` del entorno global falla por conflicto preexistente ajeno al proyecto: `moviepy 2.2.1` exige `pillow<12`, pero el entorno tiene `pillow 12.3.0`. No es posible atribuirlo al conjunto de dependencias del proyecto.
- Instalación limpia de todas las versiones nuevas fijadas en `requirements.txt`: pendiente en MacBook/CI. Los tests se ejecutaron con las librerías instaladas en el entorno de construcción (FastAPI 0.128.2, Starlette 0.50.0, pypdf 5.9.0, pandas 2.2.3, NumPy 2.3.5, scikit-learn 1.8.0 y SQLAlchemy 2.0.50), no con el conjunto actualizado completo. Streamlit y psycopg no están instalados en ese entorno; la interfaz no se inició y PostgreSQL no se probó.
- Pentest, revisión legal y validación con datos municipales autorizados: no realizados.

## Riesgos residuales

1. API key compartida por tenant; no identifica a usuarios individuales.
2. Aislamiento implementado en la capa de aplicación, no mediante PostgreSQL RLS.
3. El historial de alertas no es inmutable ni tiene firma criptográfica; el actor es una identidad técnica del tenant.
4. `create_all` está desactivado por defecto; aplicar migraciones con Alembic antes de iniciar la API.
5. El motor ML está desactivado; las reglas producen señales para revisión, no conclusiones de fraude.
6. RAG es TF-IDF local, no embeddings ni pgvector.
7. Cargar dos veces el mismo documento aún puede duplicar fragmentos; no hay endpoint de eliminación de documentos/datos.
8. `summary` y recuperación cargan datos en memoria; el motor de alertas aún puede requerir optimización para volúmenes grandes.
9. No existe rotación automatizada de API keys ni auditoría inmutable firmada.

## Criterio de uso

Apto para demostración local con datos ficticios. No cargar datos personales o institucionales reales, no conectar ChileCompra y no usar para decisiones administrativas o disciplinarias.
