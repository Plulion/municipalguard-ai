from datetime import date
from pathlib import Path
import pandas as pd
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.app.db import Base
from backend.app.models import (
    Tenant,
    FinancialTransaction,
    ProcurementProcess,
    PurchaseOrder,
    Alert,
    DocumentChunk,
)
from backend.app.analytics import (
    clean_text,
    normalize_headers,
    normalize_rut,
    parse_chilean_date,
    parse_clp,
    read_table,
    import_financial,
    import_procurement,
    import_purchase_orders,
    run_finance_rules,
    run_procurement_rules,
)
from backend.app.rag import retrieve


def make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class __err:
    def __init__(self, typ):
        self.typ = typ

    def __enter__(self):
        return None

    def __exit__(self, typ, value, tb):
        assert typ is self.typ
        return True


def test_parse_chilean_clp_formats_and_edge_cases():
    assert parse_clp("1.250.000") == 1250000
    assert parse_clp("1250000") == 1250000
    for bad in ("1.250,50", True, False, float("inf"), float("-inf")):
        with __err(ValueError):
            parse_clp(bad)


def test_finance_import_rules_and_idempotency():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    frame = pd.DataFrame([
        {
            "transaction_date": "2026-01-01",
            "transaction_type": "Gasto",
            "amount_clp": "1250000",
            "supplier_name": "A",
            "supplier_tax_id": "76.111.111-6",
            "invoice_number": "F1",
            "budget_amount_clp": "1000000",
        },
        {
            "transaction_date": "2026-01-01",
            "transaction_type": "Gasto",
            "amount_clp": "1250000",
            "supplier_name": "A",
            "supplier_tax_id": "76.111.111-6",
            "invoice_number": "F1",
            "budget_amount_clp": "1000000",
        },
    ])
    assert import_financial(db, "tenant-a", frame, "test.csv") == 2
    assert import_financial(db, "tenant-a", frame, "test.csv") == 0
    assert run_finance_rules(db, "tenant-a") == 4
    categories = {a.category for a in db.query(Alert).filter_by(tenant_id="tenant-a").all()}
    assert categories == {"budget_variance", "possible_duplicate_invoice"}


def test_duplicate_invoice_detected_across_different_files_and_credit_note_kept():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    jan = pd.DataFrame([
        {
            "transaction_date": "2026-01-10",
            "transaction_type": "Gasto",
            "amount_clp": "500000",
            "supplier_tax_id": "76.111.111-6",
            "invoice_number": "FAC-900",
        }
    ])
    feb = pd.DataFrame([
        {
            "transaction_date": "2026-02-12",
            "transaction_type": "Gasto",
            "amount_clp": "500000",
            "supplier_tax_id": "76.111.111-6",
            "invoice_number": "FAC-900",
        },
        {
            "transaction_date": "2026-02-15",
            "transaction_type": "Nota de crédito",
            "amount_clp": "-500000",
            "supplier_tax_id": "76.111.111-6",
            "invoice_number": "FAC-900",
        },
    ])
    assert import_financial(db, "tenant-a", jan, "enero.csv") == 1
    assert import_financial(db, "tenant-a", feb, "febrero.csv") == 2
    assert run_finance_rules(db, "tenant-a") == 2
    alerts = db.query(Alert).filter_by(tenant_id="tenant-a", category="possible_duplicate_invoice").all()
    assert len(alerts) == 2


def test_procurement_import_and_alert_rules():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    frame = pd.DataFrame([
        {
            "external_id": "123-1",
            "title": "Servicio de prueba",
            "offers_count": "1",
            "estimated_amount_clp": "1000000",
            "awarded_amount_clp": "1500000",
            "awarded_supplier_tax_id": "76.111.111-6",
            "supplier_name": "Proveedor A",
        },
        {
            "external_id": "123-2",
            "title": "Adjudicación sin ofertas",
            "offers_count": "0",
            "estimated_amount_clp": "1000000",
            "awarded_amount_clp": "1000000",
            "awarded_supplier_tax_id": "76.111.111-6",
            "supplier_name": "Proveedor A",
        },
    ])
    result = import_procurement(db, "tenant-a", frame, "tenders.csv")
    assert result["imported"] == 2
    assert result["suppliers_created"] == 1
    po_frame = pd.DataFrame([
        {"external_id": "OC-10", "amount_clp": "1000000", "procurement_external_id": "123-1"},
        {"external_id": "OC-11", "amount_clp": "1000000", "procurement_external_id": "123-1"},
    ])
    assert import_purchase_orders(db, "tenant-a", po_frame, "orders.csv")["imported"] == 2
    assert run_procurement_rules(db, "tenant-a") == 4
    categories = {a.category for a in db.query(Alert).filter_by(tenant_id="tenant-a", domain="procurement").all()}
    assert categories == {"single_offer", "award_over_estimate", "award_without_offers", "po_exceeds_award"}


def test_tenant_queries_do_not_mix_records():
    db = make_db()
    db.add_all([Tenant(id="a", name="A"), Tenant(id="b", name="B")])
    db.commit()
    db.add_all([
        FinancialTransaction(tenant_id="a", transaction_date=date(2026, 1, 1), transaction_type="Gasto", amount_clp=100),
        FinancialTransaction(tenant_id="b", transaction_date=date(2026, 1, 1), transaction_type="Gasto", amount_clp=200),
    ])
    db.commit()
    assert sum(x.amount_clp for x in db.query(FinancialTransaction).filter_by(tenant_id="a").all()) == 100
    assert sum(x.amount_clp for x in db.query(FinancialTransaction).filter_by(tenant_id="b").all()) == 200


def test_rag_retrieval_is_tenant_scoped():
    db = make_db()
    db.add_all([Tenant(id="a", name="A"), Tenant(id="b", name="B")])
    db.commit()
    db.add_all([
        DocumentChunk(tenant_id="a", filename="a.txt", page_number=None, chunk_index=0, content="Orden de compra y factura para pago municipal."),
        DocumentChunk(tenant_id="b", filename="b.txt", page_number=None, chunk_index=0, content="Política de recursos humanos y permisos."),
    ])
    db.commit()
    results = retrieve(db, "a", "orden de compra factura pago")
    assert results and all(x["source"] == "a.txt" for x in results)


def test_procurement_deduplicates_repeated_supplier_in_batch():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    frame = pd.DataFrame([
        {"external_id": "L-1", "title": "Compra uno", "awarded_supplier_tax_id": "76.111.111-6", "supplier_name": "Proveedor A"},
        {"external_id": "L-2", "title": "Compra dos", "awarded_supplier_tax_id": "76.111.111-6", "supplier_name": "Proveedor A"},
    ])
    result = import_procurement(db, "tenant-a", frame, "tenders.csv")
    assert result == {"imported": 2, "updated": 0, "suppliers_created": 1}


def test_chilean_dates_and_scientific_clp_are_correct():
    assert str(parse_chilean_date("05/01/2026")) == "2026-01-05"
    assert str(parse_chilean_date("13/01/2026")) == "2026-01-13"
    assert parse_clp("1.5E+07") == 15_000_000
    assert parse_clp("1e6") == 1_000_000
    with __err(ValueError):
        parse_clp("1,250")
    assert parse_clp("1,250,000") == 1_250_000


def test_nan_empty_values_are_not_persisted_as_text_nan():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    frame = pd.DataFrame([{"external_id": float("nan"), "title": "Sin ID", "status": float("nan")}])
    try:
        import_procurement(db, "tenant-a", frame, "bad.csv")
        assert False, "Debe rechazar identificador vacío"
    except ValueError as exc:
        assert "Fila 2" in str(exc)
    assert clean_text(float("nan")) == ""


def test_procurement_import_updates_existing_record():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    first = pd.DataFrame([{"external_id": "LIC-77", "title": "Servicio", "status": "En evaluación"}])
    second = pd.DataFrame([{"external_id": "LIC-77", "title": "Servicio", "status": "Adjudicada", "awarded_amount_clp": "1500000"}])
    assert import_procurement(db, "tenant-a", first, "one.csv")["imported"] == 1
    result = import_procurement(db, "tenant-a", second, "two.csv")
    assert result["updated"] == 1
    row = db.query(ProcurementProcess).filter_by(tenant_id="tenant-a", external_id="LIC-77").one()
    assert row.status == "Adjudicada" and row.awarded_amount_clp == 1_500_000


def test_header_collision_rejected_and_procurement_alias_preserved():
    with __err(ValueError):
        normalize_headers(pd.DataFrame(columns=["orden_compra", "id_orden_compra"]))
    result = normalize_headers(pd.DataFrame(columns=["licitacion", "orden_compra"]))
    assert "procurement_external_id" in result.columns
    assert "external_id" in result.columns


def test_rut_normalization_and_validation():
    assert normalize_rut("76.111.111-6") == "76111111-6"
    assert normalize_rut("76111111-6") == "76111111-6"
    with __err(ValueError):
        normalize_rut("76.111.111-1")


def test_purchase_order_upsert_and_nan_handling():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    frame = pd.DataFrame([{"orden_compra": "OC-1", "monto_total": "125000", "estado": float("nan"), "licitacion": "LIC-1"}])
    result = import_purchase_orders(db, "tenant-a", frame, "orders.csv")
    assert result["imported"] == 1
    row = db.query(PurchaseOrder).filter_by(external_id="OC-1").one()
    assert row.status == "unknown" and row.procurement_external_id == "LIC-1"
    frame2 = pd.DataFrame([{"orden_compra": "OC-1", "monto_total": "200000", "estado": "Recibida", "licitacion": "LIC-1"}])
    assert import_purchase_orders(db, "tenant-a", frame2, "orders2.csv")["updated"] == 1
    assert row.amount_clp == 200000


def test_financial_identical_rows_without_invoice_are_not_deduplicated_across_files():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    frame = pd.DataFrame([{"fecha": "2026-01-05", "tipo": "Ingreso", "monto": "100000"}])
    assert import_financial(db, "tenant-a", frame, "a.csv") == 1
    assert import_financial(db, "tenant-a", frame, "b.csv") == 1
    assert db.query(FinancialTransaction).filter_by(tenant_id="tenant-a").count() == 2


def test_real_api_cross_tenant_and_alert_audit():
    from backend.app.main import app
    from backend.app.security import require_tenant

    a = {"X-API-Key": "tenant-a-test-key-000000000000"}
    b = {"X-API-Key": "tenant-b-test-key-000000000000"}

    with TestClient(app) as client:
        assert client.get("/institution", headers=a).status_code == 200
        assert client.get("/institution", headers=b).status_code == 200

        document_text = (
            "La municipalidad registra el contrato de mantención de luminarias públicas. "
            "El proveedor debe entregar informes mensuales y respaldos de recepción conforme. " * 3
        )
        uploaded1 = client.post(
            "/documents/upload",
            headers=a,
            files={"file": ("contrato_demo.txt", document_text.encode("utf-8"), "text/plain")},
        )
        assert uploaded1.status_code == 200
        uploaded2 = client.post(
            "/documents/upload",
            headers=a,
            files={"file": ("contrato_demo.txt", document_text.encode("utf-8"), "text/plain")},
        )
        assert uploaded2.status_code == 200
        assert client.get("/summary", headers=a).json()["documents"] == uploaded1.json()["chunks_indexed"]

        bad_doc = client.post("/documents/upload", headers=a, files={"file": ("virus.exe", b"hello" * 20, "application/octet-stream")})
        assert bad_doc.status_code == 415

        rag_a = client.post("/rag/ask", headers=a, json={"question": "contrato mantención luminarias informes mensuales"})
        rag_b = client.post("/rag/ask", headers=b, json={"question": "contrato mantención luminarias informes mensuales"})
        assert rag_a.status_code == 200 and len(rag_a.json()["sources"]) > 0
        assert rag_b.status_code == 200 and rag_b.json()["sources"] == []

        payload = {
            "transaction_date": "2026-01-05",
            "transaction_type": "Gasto",
            "amount_clp": 100000,
            "budget_amount_clp": 50000,
            "supplier_tax_id": "76.111.111-6",
            "description": "Prueba auditoría",
        }
        assert client.post("/finance/transactions", headers=a, json=payload).status_code == 200
        assert client.get("/finance/transactions", headers=b).json() == []

        credit = {
            "transaction_date": "2026-01-06",
            "transaction_type": "Nota de crédito",
            "amount_clp": -25000,
            "supplier_tax_id": "76.111.111-6",
            "invoice_number": "NC-1",
        }
        assert client.post("/finance/transactions", headers=a, json=credit).status_code == 200

        invalid_rut = {
            "transaction_date": "2026-01-06",
            "transaction_type": "Gasto",
            "amount_clp": 100,
            "supplier_tax_id": "76.111.111-1",
        }
        assert client.post("/finance/transactions", headers=a, json=invalid_rut).status_code == 400

        assert client.get("/procurement/suppliers/%20/360", headers=a).status_code == 400
        s360 = client.get("/procurement/suppliers/76.111.111-6/360", headers=a)
        assert s360.status_code == 200
        assert s360.json()["financial_transactions_count"] >= 2

        client.post("/analysis/run", headers=a)
        alerts = client.get("/alerts", headers=a).json()
        assert alerts
        alert_id = alerts[0]["id"]

        bad_reason = client.patch(f"/alerts/{alert_id}", headers=a, json={"status": "En revisión", "reason": "     "})
        assert bad_reason.status_code == 422

        response = client.patch(
            f"/alerts/{alert_id}",
            headers=a,
            json={"status": "En revisión", "reason": "Revisión documental iniciada"},
        )
        assert response.status_code == 200
        history = client.get(f"/alerts/{alert_id}/history", headers=a)
        assert history.status_code == 200 and history.json()[0]["new_status"] == "En revisión"
        assert client.get(f"/alerts/{alert_id}/history", headers=b).status_code == 404

    try:
        require_tenant("clavé-no-válida")
        assert False, "La clave no ASCII debe rechazarse de forma controlada"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_all_demo_csv_files_import_with_valid_ruts_and_chilean_dates():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    sample = Path(__file__).resolve().parents[1] / "data" / "sample"
    finance = read_table("financial_transactions_demo.csv", (sample / "financial_transactions_demo.csv").read_bytes())
    tenders = read_table("procurement_tenders_demo.csv", (sample / "procurement_tenders_demo.csv").read_bytes())
    orders = read_table("purchase_orders_demo.csv", (sample / "purchase_orders_demo.csv").read_bytes())
    assert import_financial(db, "tenant-a", finance, "finance.csv") == len(finance)
    assert import_procurement(db, "tenant-a", tenders, "tenders.csv")["imported"] == len(tenders)
    assert import_purchase_orders(db, "tenant-a", orders, "orders.csv")["imported"] == len(orders)


def test_procurement_alert_is_deactivated_when_updated_source_no_longer_triggers_rule():
    db = make_db()
    db.add(Tenant(id="tenant-a", name="A"))
    db.commit()
    first = pd.DataFrame([
        {
            "external_id": "LIC-88",
            "title": "Servicio",
            "status": "Adjudicada",
            "estimated_amount_clp": "1000000",
            "awarded_amount_clp": "1500000",
            "offers_count": "1",
        }
    ])
    import_procurement(db, "tenant-a", first, "first.csv")
    run_procurement_rules(db, "tenant-a")
    alert = db.query(Alert).filter_by(tenant_id="tenant-a", category="single_offer").one()
    assert alert.is_active is True
    second = pd.DataFrame([
        {
            "external_id": "LIC-88",
            "title": "Servicio",
            "status": "Adjudicada",
            "estimated_amount_clp": "1000000",
            "awarded_amount_clp": "900000",
            "offers_count": "3",
        }
    ])
    import_procurement(db, "tenant-a", second, "second.csv")
    run_procurement_rules(db, "tenant-a")
    db.refresh(alert)
    assert alert.is_active is False


def test_pii_masking_hides_personal_data():
    from backend.app.pii import mask_pii
    sample = "Contacto: juan.perez@municipio.cl, fono +56 9 8765 4321, RUT 12.345.678-5, Empresa 76.111.111-6."
    masked, counts = mask_pii(sample)
    assert "[CORREO_OCULTO]" in masked
    assert "[TELEFONO_OCULTO]" in masked
    assert "[RUT_PERSONA_NATURAL]" in masked
    assert "76.111.111-6" in masked
    assert counts["emails"] == 1 and counts["phones"] == 1 and counts["personal_ruts"] == 1


def test_chile_pre_audit_detects_ds661_and_ley21634_violations():
    from datetime import datetime
    from backend.app.chile_procurement_rules import run_pre_audit_bases
    bad_text = (
        "Las presentes bases se rigen por el Decreto Supremo 250. "
        "El equipo deberá ser exclusivamente marca ACME Pro X1 sin excepciones."
    )
    res = run_pre_audit_bases(
        full_text=bad_text,
        estimated_amount_clp=150_000_000,
        utm_value_clp=69500,
        publication_date=date(2026, 11, 10),
        closing_datetime=datetime(2026, 11, 16, 11, 0),
        criteria_weights_sum=95.0,
        max_fine_pct=40.0,
        seriousness_guarantee_pct=5.0,
        performance_guarantee_pct=0.0,
        committee_members=2,
        use_ai=False,
    )
    codes = {f["code"] for f in res["findings"]}
    assert res["bracket"] == "LP"
    assert res["ready_to_publish"] is False
    assert {
        "insufficient_publication_window",
        "closing_before_1500_after_holiday",
        "weights_not_100",
        "fine_cap_exceeds_30",
        "seriousness_guarantee_over_cap",
        "committee_under_three",
        "outdated_ds250_reference",
        "possible_brand_restriction",
    }.issubset(codes)


def test_multimodal_docx_jpg_and_spoofed_pdf_via_api():
    import io
    from docx import Document as DocxDocument
    from PIL import Image
    from backend.app.main import app

    a = {"X-API-Key": "tenant-a-test-key-000000000000"}
    docx_buf = io.BytesIO()
    d = DocxDocument()
    d.add_paragraph("Bases administrativas licitación pública de mantención de áreas verdes bajo D.S. 250.")
    d.save(docx_buf)

    img_buf = io.BytesIO()
    Image.new("RGB", (200, 80), color=(255, 255, 255)).save(img_buf, format="JPEG")

    with TestClient(app) as client:
        assert client.get("/ai/status", headers=a).status_code == 200

        spoof = client.post("/documents/upload", headers=a, files={"file": ("falso.pdf", b"NO_ES_UN_PDF_REAL", "application/pdf")})
        assert spoof.status_code == 400

        up_docx = client.post(
            "/documents/upload",
            headers=a,
            files={"file": ("bases_demo.docx", docx_buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert up_docx.status_code == 200
        assert up_docx.json()["extraction_method"] == "docx_native"
        assert up_docx.json()["chunks_indexed"] >= 1

        up_jpg = client.post(
            "/documents/upload",
            headers=a,
            files={"file": ("boleta_demo.jpg", img_buf.getvalue(), "image/jpeg")},
        )
        assert up_jpg.status_code == 200
        assert up_jpg.json()["mime_type"] == "image/jpeg"

        pa = client.post(
            "/pre-audit/bases",
            headers=a,
            files={"file": ("bases_demo.docx", docx_buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={
                "estimated_amount_clp": "120000000",
                "criteria_weights_sum": "90",
                "max_fine_pct": "35",
                "use_ai": "false",
            },
        )
        assert pa.status_code == 200
        body = pa.json()
        assert body["ready_to_publish"] is False
        assert body["blockers_count"] >= 2
        assert len(client.get("/pre-audit/bases", headers=a).json()) >= 1


def test_real_chilean_financial_formats_dte_sicogen_zip_bank_pdf_and_mp_json():
    import io
    import json
    import zipfile
    import fitz
    from backend.app.main import app

    a = {"X-API-Key": "tenant-a-test-key-000000000000"}

    sii_dte_xml = """<?xml version="1.0" encoding="ISO-8859-1"?>
    <EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0">
      <SetDTE ID="SetDoc">
        <DTE version="1.0">
          <Documento ID="F100">
            <Encabezado>
              <IdDoc><TipoDTE>33</TipoDTE><Folio>1001</Folio><FchEmis>2026-10-15</FchEmis></IdDoc>
              <Emisor><RUTEmisor>76111111-6</RUTEmisor><RznSoc>Servicios Municipales SpA</RznSoc></Emisor>
              <Totales><MntTotal>1500000</MntTotal></Totales>
            </Encabezado>
            <Detalle><NmbItem>Mantención alumbrado</NmbItem></Detalle>
          </Documento>
        </DTE>
        <DTE version="1.0">
          <Documento ID="NC100">
            <Encabezado>
              <IdDoc><TipoDTE>61</TipoDTE><Folio>1001</Folio><FchEmis>2026-10-20</FchEmis></IdDoc>
              <Emisor><RUTEmisor>76111111-6</RUTEmisor><RznSoc>Servicios Municipales SpA</RznSoc></Emisor>
              <Totales><MntTotal>300000</MntTotal></Totales>
            </Encabezado>
            <Detalle><NmbItem>Descuento nota de crédito</NmbItem></Detalle>
          </Documento>
        </DTE>
      </SetDTE>
    </EnvioDTE>""".encode("latin-1")

    xxe_xml = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><DTE>&xxe;</DTE>'

    sicogen_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <InformeContableSICOGEN>
      <Movimiento>
        <Fecha>2026-10-22</Fecha>
        <CodigoCuenta>2152208001</CodigoCuenta>
        <Glosa>Servicio de aseo comunal</Glosa>
        <Debe>2450000</Debe>
        <Haber>0</Haber>
      </Movimiento>
    </InformeContableSICOGEN>""".encode("utf-8")

    bank_csv = "Fecha;Glosa;Cargo;Abono\n18/10/2026;Pago proveedor mantención;850.000;0\n19/10/2026;Reintegro tesorería;0;120.000\n".encode("latin-1")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("dte_lote.xml", sii_dte_xml)
        zf.writestr("sicogen_oct.xml", sicogen_xml)

    mp_tenders_json = json.dumps({
        "Listado": [
            {
                "CodigoExterno": "2345-10-LP26",
                "Nombre": "Servicio de mantención de áreas verdes",
                "Estado": "Adjudicada",
                "Fechas": {"FechaPublicacion": "2026-10-01T10:00:00", "FechaCierre": "2026-10-25T15:30:00"},
                "MontoEstimado": 80000000,
                "Adjudicacion": {
                    "NumeroOferentes": 2,
                    "MontoTotal": 78000000,
                    "RutProveedor": "76.111.111-6",
                    "NombreProveedor": "Servicios Municipales SpA",
                },
            }
        ]
    }).encode("utf-8")

    pdf_doc = fitz.open()
    page = pdf_doc.new_page()
    page.insert_text((72, 72), "DECRETO DE PAGO N° 4421\nFecha: 25/10/2026\nRUT: 76.111.111-6\nMonto Total: $1.250.000\n")
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    with TestClient(app) as client:
        assert client.get("/formats").status_code == 200

        assert client.post("/finance/transactions/upload", headers=a, files={"file": ("xxe.xml", xxe_xml, "application/xml")}).status_code == 400

        r_dte = client.post("/finance/transactions/upload", headers=a, files={"file": ("envio_sii.xml", sii_dte_xml, "application/xml")})
        assert r_dte.status_code == 200
        assert r_dte.json()["detected_format"] == "xml_sii_dte"
        assert r_dte.json()["imported"] == 2

        r_bank = client.post("/finance/transactions/upload", headers=a, files={"file": ("cartola_banco.csv", bank_csv, "text/csv")})
        assert r_bank.status_code == 200
        assert r_bank.json()["imported"] == 2

        r_zip = client.post("/finance/transactions/upload", headers=a, files={"file": ("lote_octubre.zip", zip_buf.getvalue(), "application/zip")})
        assert r_zip.status_code == 200
        assert r_zip.json()["imported"] == 3
        assert "xml_sii_dte" in r_zip.json()["detected_format"]

        r_pdf = client.post("/finance/transactions/upload", headers=a, files={"file": ("decreto_4421.pdf", pdf_bytes, "application/pdf")})
        assert r_pdf.status_code == 200
        assert r_pdf.json()["imported"] == 1

        r_mp = client.post("/procurement/tenders/upload", headers=a, files={"file": ("mp_licitaciones.json", mp_tenders_json, "application/json")})
        assert r_mp.status_code == 200
        assert r_mp.json()["detected_format"] == "json_mercadopublico"
        assert r_mp.json()["imported"] == 1
