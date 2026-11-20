#!/usr/bin/env bash
set -euo pipefail
: "${MGAI_API_KEY:?Cargue .env y configure MGAI_API_KEY antes de ejecutar}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
AUTH=(-H "X-API-Key: ${MGAI_API_KEY}")
request() { curl --fail --silent --show-error "$@"; }
echo "[1/7] health"
request "${BASE_URL}/health"
echo
echo "[2/7] institution"
request "${AUTH[@]}" "${BASE_URL}/institution"
echo
echo "[3/7] summary"
request "${AUTH[@]}" "${BASE_URL}/summary"
echo
echo "[4/7] import finance demo"
request -X POST "${AUTH[@]}" -F "file=@data/sample/financial_transactions_demo.csv" "${BASE_URL}/finance/transactions/upload"
echo
echo "[5/7] import tenders demo"
request -X POST "${AUTH[@]}" -F "file=@data/sample/procurement_tenders_demo.csv" "${BASE_URL}/procurement/tenders/upload"
echo
echo "[6/7] import purchase orders demo"
request -X POST "${AUTH[@]}" -F "file=@data/sample/purchase_orders_demo.csv" "${BASE_URL}/procurement/orders/upload"
echo
echo "[7/7] run analysis + alerts"
request -X POST "${AUTH[@]}" "${BASE_URL}/analysis/run"
echo
request "${AUTH[@]}" "${BASE_URL}/alerts"
echo
echo "Smoke test completado. Revise respuestas y alertas; son datos ficticios."
