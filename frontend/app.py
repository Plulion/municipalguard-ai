import os
from datetime import date, time, datetime
from pathlib import Path
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="MunicipalGuard AI", page_icon="🏛️", layout="wide")
st.title("🏛️ MunicipalGuard AI")
st.caption("Public Sector Intelligence & Risk Management · v0.3.1 (Formatos Reales CL + Multimodal + Pre-Auditoría Ley 21.634)")

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
ENV_API_KEY = os.getenv("MGAI_API_KEY", "")
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"

FINANCE_TYPES = ["xml", "zip", "xlsx", "xls", "csv", "tsv", "txt", "json", "pdf", "jpg", "jpeg", "png"]
PROCUREMENT_TYPES = ["csv", "xlsx", "xls", "tsv", "txt", "json", "xml"]


@st.cache_data
def load_sample_bytes(filename: str) -> bytes | None:
    path = SAMPLE_DIR / filename
    return path.read_bytes() if path.exists() else None


with st.sidebar:
    st.header("Conexión institucional")
    st.caption(f"Endpoint configurado: `{API_URL}`")
    if ENV_API_KEY:
        api_key = ENV_API_KEY
        st.success("Credencial cargada desde el servidor.")
    else:
        api_key = st.text_input("API Key", "", type="password")

if not api_key:
    st.warning("Ingrese una API Key válida en la barra lateral para conectar.")
    st.stop()

HEADERS = {"X-API-Key": api_key}


def api_request(method: str, path: str, timeout: int = 30, **kwargs):
    try:
        return requests.request(method, f"{API_URL}{path}", headers=HEADERS, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        st.error(f"Error de comunicación con la API: {exc}")
        return None


if "flash_msg" in st.session_state:
    st.success(st.session_state.pop("flash_msg"))
if "flash_warnings" in st.session_state:
    for w in st.session_state.pop("flash_warnings"):
        st.warning(w)

inst_resp = api_request("GET", "/institution", timeout=15)
summary_resp = api_request("GET", "/summary", timeout=15)
ai_resp = api_request("GET", "/ai/status", timeout=10)
if not inst_resp or not inst_resp.ok or not summary_resp or not summary_resp.ok:
    st.error("No se pudo autenticar con la API. Verifique estado del servicio y credencial.")
    st.stop()

institution = inst_resp.json()
summary = summary_resp.json()
ai_info = ai_resp.json() if (ai_resp and ai_resp.ok) else {"provider": "desconocido", "available": False, "detail": ""}

with st.sidebar:
    st.divider()
    st.subheader("Motor IA y Privacidad")
    st.write(f"Modo activo: **`{ai_info.get('provider')}`**")
    st.write(f"Filtro PII (Ley 21.719): **{'Activo' if ai_info.get('pii_masking_enabled') else 'Inactivo'}**")
    if ai_info.get("available"):
        st.success(ai_info.get("detail", "Motor IA conectado."))
    else:
        st.info(ai_info.get("detail", "Operando con reglas deterministas y OCR local."))

st.caption(f"Institución activa: **{institution['name']}** · Tipo: {institution['institution_type']}")
cols = st.columns(6)
cols[0].metric("Movimientos", f"{summary['financial_transactions']:,}")
cols[1].metric("Licitaciones", f"{summary['procurement_processes']:,}")
cols[2].metric("Órdenes de compra", f"{summary['purchase_orders']:,}")
cols[3].metric("Proveedores", f"{summary['suppliers']:,}")
cols[4].metric("Alertas pendientes", f"{summary['pending_alerts']:,}")
cols[5].metric("Pre-Auditorías", f"{summary.get('pre_audits', 0):,}")

finance, procurement, pre_audit_tab, alerts_tab, rag_tab, roadmap = st.tabs(
    ["Finanzas", "Compras públicas", "Pre-Auditoría Bases", "Alertas", "Documentos Multimodal", "Plataforma"]
)

with finance:
    st.subheader("Finanzas municipales (Ingesta Multiformato Real)")
    with st.expander("Formatos financieros reales admitidos (Chile y Auditoría)", expanded=False):
        st.markdown(
            "- **SII DTE (`XML`) y lotes masivos (`ZIP`)**: Facturas Electrónicas (33/34), Notas de Crédito (61, con signo negativo automático), Notas de Débito (56) y Boletas.\n"
            "- **CGR SICOGEN (`XML`, `TXT`)**: Informes contables municipales con columnas `Debe`/`Haber`.\n"
            "- **SII RCV, BancoEstado y ERPs Municipales (`XLSX`, `XLS`, `CSV`, `TSV`, `TXT`)**: soporta codificación `Latin-1`, separador `;`, membretes superiores y columnas `Cargo`/`Abono`.\n"
            "- **APIs (`JSON`) y Documentos (`PDF`, `JPG`, `PNG`)**: tablas de cartolas bancarias, facturas y decretos de pago con OCR en español."
        )
    up = st.file_uploader(
        "Importar archivo financiero (XML DTE/SICOGEN, ZIP en lote, XLSX/XLS, CSV/TSV/TXT, JSON, PDF, JPG/PNG)",
        type=FINANCE_TYPES,
        key="finance",
    )
    if up and st.button("Importar movimientos", key="import_fin"):
        r = api_request("POST", "/finance/transactions/upload", timeout=90, files={"file": (up.name, up.getvalue())})
        if r and r.ok:
            res = r.json()
            st.session_state["flash_msg"] = (
                f"Registros financieros nuevos: {res['imported']} · Formato detectado: {res.get('detected_format', 'estándar')}"
            )
            if res.get("warnings"):
                st.session_state["flash_warnings"] = res["warnings"]
            st.rerun()
        elif r is not None:
            st.error(r.text)
    r = api_request("GET", "/finance/transactions", timeout=15)
    if r and r.ok:
        st.dataframe(pd.DataFrame(r.json()), use_container_width=True, hide_index=True)
    demo_fin = load_sample_bytes("financial_transactions_demo.csv")
    if demo_fin:
        st.download_button("CSV de demostración", demo_fin, file_name="financial_transactions_demo.csv")

with procurement:
    st.subheader("Compras públicas, licitaciones y proveedores")
    st.caption("Admite planillas (CSV, XLSX, XLS, TSV, TXT), exportaciones JSON de Datos Abiertos de Mercado Público y XML.")
    c1, c2 = st.columns(2)
    with c1:
        tender = st.file_uploader("Importar licitaciones (CSV/XLSX/XLS/JSON Mercado Público/XML)", type=PROCUREMENT_TYPES, key="tenders")
        if tender and st.button("Importar licitaciones"):
            r = api_request("POST", "/procurement/tenders/upload", timeout=60, files={"file": (tender.name, tender.getvalue())})
            if r and r.ok:
                data = r.json()
                st.session_state["flash_msg"] = (
                    f"Licitaciones nuevas: {data['imported']} · Actualizadas: {data['updated']} · "
                    f"Proveedores creados: {data['suppliers_created']} ({data.get('detected_format', '')})"
                )
                st.rerun()
            elif r is not None:
                st.error(r.text)
    with c2:
        orders = st.file_uploader("Importar órdenes de compra (CSV/XLSX/XLS/JSON Mercado Público/XML)", type=PROCUREMENT_TYPES, key="orders")
        if orders and st.button("Importar órdenes"):
            r = api_request("POST", "/procurement/orders/upload", timeout=60, files={"file": (orders.name, orders.getvalue())})
            if r and r.ok:
                data = r.json()
                st.session_state["flash_msg"] = (
                    f"Órdenes nuevas: {data['imported']} · Actualizadas: {data['updated']} ({data.get('detected_format', '')})"
                )
                st.rerun()
            elif r is not None:
                st.error(r.text)
    a, b = st.columns(2)
    with a:
        st.markdown("**Licitaciones**")
        r = api_request("GET", "/procurement/tenders", timeout=15)
        if r and r.ok:
            st.dataframe(pd.DataFrame(r.json()), use_container_width=True, hide_index=True)
    with b:
        st.markdown("**Órdenes de compra**")
        r = api_request("GET", "/procurement/orders", timeout=15)
        if r and r.ok:
            st.dataframe(pd.DataFrame(r.json()), use_container_width=True, hide_index=True)
    st.markdown("**Supplier 360°**")
    r = api_request("GET", "/procurement/suppliers", timeout=15)
    if r and r.ok:
        sup = r.json()
        st.dataframe(pd.DataFrame(sup), use_container_width=True, hide_index=True)
        ruts = [x["tax_id"] for x in sup if x.get("tax_id")]
        tax_id = st.selectbox("Consultar proveedor por identificador", ruts) if ruts else None
        if tax_id:
            detail = api_request("GET", f"/procurement/suppliers/{tax_id}/360", timeout=15)
            if detail and detail.ok:
                st.json(detail.json())
    demo_tenders = load_sample_bytes("procurement_tenders_demo.csv")
    if demo_tenders:
        st.download_button("CSV de licitaciones demo", demo_tenders, file_name="procurement_tenders_demo.csv")
    demo_orders = load_sample_bytes("purchase_orders_demo.csv")
    if demo_orders:
        st.download_button("CSV de órdenes demo", demo_orders, file_name="purchase_orders_demo.csv")

with pre_audit_tab:
    st.subheader("Auditoría Previa de Bases (Ley 19.886 · Ley 21.634 · D.S. 661/2024)")
    st.caption("Suba el borrador de bases en PDF (nativo o escaneado), DOCX, JPG, PNG o TXT para validar plazos, garantías, multas y cláusulas antes de publicar.")
    bases_file = st.file_uploader("Borrador de bases", type=["pdf", "docx", "jpg", "jpeg", "png", "txt"], key="bases_upload")
    p1, p2, p3 = st.columns(3)
    with p1:
        est_clp = st.number_input("Monto estimado (CLP)", min_value=0, value=120_000_000, step=1_000_000)
        utm_clp = st.number_input("Valor UTM referencial (CLP)", min_value=1, value=69500, step=100)
        weights_sum = st.number_input("Suma ponderaciones (%)", min_value=0.0, max_value=200.0, value=100.0, step=1.0)
    with p2:
        pub_date = st.date_input("Fecha estimada de publicación", value=date.today())
        close_date = st.date_input("Fecha estimada de cierre", value=date.today())
        close_time = st.time_input("Hora de cierre", value=time(15, 0))
    with p3:
        max_fine = st.number_input("Tope máximo de multas (%)", min_value=0.0, max_value=100.0, value=30.0, step=1.0)
        ser_guar = st.number_input("Garantía de seriedad (%)", min_value=0.0, max_value=50.0, value=0.0, step=0.5)
        perf_guar = st.number_input("Garantía fiel cumplimiento (%)", min_value=0.0, max_value=50.0, value=5.0, step=0.5)
        comm_members = st.number_input("Integrantes Comisión Evaluadora", min_value=0, max_value=20, value=3, step=1)
    use_ai_check = st.checkbox("Ejecutar revisión semántica adicional con motor IA configurado", value=True)

    if bases_file and st.button("Auditar borrador de bases", type="primary"):
        close_dt = datetime.combine(close_date, close_time).isoformat()
        form_data = {
            "estimated_amount_clp": str(int(est_clp)),
            "utm_value_clp": str(int(utm_clp)),
            "publication_date": pub_date.isoformat(),
            "closing_datetime": close_dt,
            "criteria_weights_sum": str(weights_sum),
            "max_fine_pct": str(max_fine),
            "seriousness_guarantee_pct": str(ser_guar),
            "performance_guarantee_pct": str(perf_guar),
            "committee_members": str(int(comm_members)),
            "use_ai": "true" if use_ai_check else "false",
        }
        r = api_request(
            "POST",
            "/pre-audit/bases",
            timeout=90,
            files={"file": (bases_file.name, bases_file.getvalue())},
            data=form_data,
        )
        if r and r.ok:
            res = r.json()
            st.markdown(
                f"**Resultado:** Tramo `{res['bracket']}` ({res['amount_utm']} UTM) · Extracción: `{res['extraction_method']}`"
                + (f" · Confianza OCR: `{res['ocr_confidence']}`" if res.get("ocr_confidence") is not None else "")
            )
            if res.get("warnings"):
                for w in res["warnings"]:
                    st.warning(w)
            if res["ready_to_publish"]:
                st.success("Sin hallazgos bloqueantes detectados en las reglas verificadas.")
            else:
                st.error(f"Se detectaron {res['blockers_count']} hallazgos BLOQUEANTES antes de publicar.")
            for f in res["findings"]:
                sev = f.get("severity", "Media")
                badge = "🔴" if sev == "Bloqueante" else ("🟠" if sev == "Alta" else "🟡")
                with st.expander(f"{badge} [{sev}] {f['title']} · {f['norm']} ({f['source']})", expanded=(sev == "Bloqueante")):
                    st.write(f["detail"])
        elif r is not None:
            st.error(r.text)

    st.divider()
    st.markdown("**Historial de pre-auditorías**")
    hist_pa = api_request("GET", "/pre-audit/bases", timeout=15)
    if hist_pa and hist_pa.ok:
        items = hist_pa.json()
        if items:
            summary_rows = [{k: v for k, v in x.items() if k != "findings"} for x in items]
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

with alerts_tab:
    st.subheader("Señales para revisión humana")
    show_inactive = st.checkbox("Incluir alertas históricas inactivas", value=False)
    if st.button("Ejecutar reglas de análisis", type="primary"):
        r = api_request("POST", "/analysis/run", timeout=60)
        if r and r.ok:
            res = r.json()
            st.session_state["flash_msg"] = (
                f"{res['message']} (Finanzas nuevas: {res['finance_alerts_created']}, "
                f"Compras nuevas: {res['procurement_alerts_created']})"
            )
            st.rerun()
        elif r is not None:
            st.error(r.text)
    r = api_request("GET", f"/alerts?active_only={'false' if show_inactive else 'true'}", timeout=15)
    if r and r.ok:
        data = r.json()
        st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
        if data:
            alert_ids = [item["id"] for item in data]
            aid = st.selectbox("Seleccionar alerta", alert_ids)
            c_upd, c_hist = st.columns(2)
            with c_upd:
                status = st.selectbox("Nuevo estado", ["Pendiente", "En revisión", "Cerrada", "Descartada"])
                reason = st.text_input("Motivo obligatorio del cambio", key="alert_reason")
                if st.button("Actualizar alerta"):
                    u = api_request("PATCH", f"/alerts/{aid}", timeout=15, json={"status": status, "reason": reason})
                    if u and u.ok:
                        st.session_state["flash_msg"] = f"Alerta #{aid} actualizada a '{status}'."
                        st.rerun()
                    elif u is not None:
                        st.error(u.text)
            with c_hist:
                st.markdown("**Historial de auditoría**")
                hist = api_request("GET", f"/alerts/{aid}/history", timeout=15)
                if hist and hist.ok:
                    events = hist.json()
                    if events:
                        st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
                    else:
                        st.caption("Sin cambios registrados aún.")

with rag_tab:
    st.subheader("Ingesta Multimodal y Buscador Documental")
    st.caption("Admite PDF nativo, PDF escaneado (OCR español), fotos JPG/PNG/WEBP, Word DOCX y TXT. Guarda custodia SHA-256 e indexa fragmentos por institución.")
    doc = st.file_uploader("Cargar documento o imagen", type=["pdf", "docx", "jpg", "jpeg", "png", "webp", "txt"])
    if doc and st.button("Procesar e indexar"):
        r = api_request("POST", "/documents/upload", timeout=90, files={"file": (doc.name, doc.getvalue())})
        if r and r.ok:
            res = r.json()
            st.success(
                f"Procesado ({res['extraction_method']}) · Fragmentos indexados: {res['chunks_indexed']} · SHA-256: `{res['sha256'][:16]}`"
            )
            if res.get("ocr_confidence") is not None:
                st.info(f"Confianza OCR: {res['ocr_confidence']:.0%}")
            for w in res.get("warnings", []):
                st.warning(w)
        elif r is not None:
            st.error(r.text)

    docs_resp = api_request("GET", "/documents", timeout=15)
    if docs_resp and docs_resp.ok and docs_resp.json():
        st.markdown("**Artefactos custodiados**")
        st.dataframe(pd.DataFrame(docs_resp.json()), use_container_width=True, hide_index=True)

    q = st.text_input("Pregunta documental")
    if st.button("Buscar evidencia") and q.strip():
        r = api_request("POST", "/rag/ask", timeout=30, json={"question": q})
        if r and r.ok:
            st.info(r.json()["answer"])
            for item in r.json()["sources"]:
                with st.expander(f"{item['source']} · página {item['page']} · score {item['score']}"):
                    st.write(item["excerpt"])
        elif r is not None:
            st.error(r.text)

with roadmap:
    st.subheader("Módulos y formatos de la plataforma")
    st.success("Activos en v0.3.1: Finanzas Multiformato (SII DTE XML, ZIP en lote, SICOGEN XML/TXT, RCV CSV, Excel XLSX/XLS, JSON, PDF/JPG), Compras Públicas 360° (Mercado Público JSON), Ingesta Multimodal OCR y Pre-Auditoría Ley 21.634 / D.S. 661.")
    st.info("Siguiente fase (v0.4.0): Módulo de Evaluación de Ofertas y Comparador de Postulaciones con generación de Borrador de Acta Fundada + Login RBAC nominal.")
    st.warning("No utilizar datos personales reales sin habilitar autenticación nominal RBAC y evaluación de impacto bajo Ley 21.719.")
