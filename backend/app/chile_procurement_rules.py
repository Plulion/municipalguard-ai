"""Pre-auditoría preliminar de bases bajo Ley 19.886, Ley 21.634 y D.S. 661/2024."""
from datetime import date, datetime, timedelta
import re
from .ai_provider import analyze_json

DEFAULT_UTM_CLP = 69500

CHILE_FIXED_HOLIDAYS = {
    (1, 1), (5, 1), (5, 21), (6, 29), (7, 16), (8, 15),
    (9, 18), (9, 19), (10, 12), (10, 31), (11, 1), (12, 8), (12, 25),
}


def is_chile_non_business_day(d: date) -> bool:
    return d.weekday() >= 5 or (d.month, d.day) in CHILE_FIXED_HOLIDAYS


def classify_utm_bracket(amount_utm: float) -> tuple[str, int, int | None]:
    if amount_utm < 100:
        return "L1", 5, None
    if amount_utm < 1000:
        return "LE", 10, 5
    if amount_utm < 5000:
        return "LP", 20, 10
    return "LR", 30, None


def run_pre_audit_bases(
    full_text: str,
    estimated_amount_clp: int | None = None,
    utm_value_clp: int = DEFAULT_UTM_CLP,
    publication_date: date | None = None,
    closing_datetime: datetime | None = None,
    criteria_weights_sum: float | None = None,
    max_fine_pct: float | None = None,
    seriousness_guarantee_pct: float | None = None,
    performance_guarantee_pct: float | None = None,
    committee_members: int | None = None,
    use_ai: bool = True,
) -> dict:
    findings: list[dict] = []
    amount_utm = round(estimated_amount_clp / utm_value_clp, 2) if (estimated_amount_clp and utm_value_clp > 0) else None
    bracket = None

    if amount_utm is not None:
        bracket, min_days, reduced_days = classify_utm_bracket(amount_utm)
        if publication_date and closing_datetime:
            days = (closing_datetime.date() - publication_date).days
            if days < min_days:
                if reduced_days and days >= reduced_days:
                    findings.append({
                        "severity": "Media",
                        "source": "determinista",
                        "norm": "D.S. 661/2024 Art. 36",
                        "code": "reduced_publication_window",
                        "title": f"Plazo de publicación reducido ({days} días) para tramo {bracket}",
                        "detail": f"El mínimo general para {bracket} ({amount_utm} UTM) es {min_days} días corridos. Solo procede reducción a {reduced_days} días por bienes/servicios de simple y objetiva especificación debidamente fundada.",
                    })
                else:
                    findings.append({
                        "severity": "Bloqueante",
                        "source": "determinista",
                        "norm": "D.S. 661/2024 Art. 36",
                        "code": "insufficient_publication_window",
                        "title": f"Plazo de publicación inferior al mínimo legal ({days} días vs {min_days} días)",
                        "detail": f"Para una licitación tramo {bracket} ({amount_utm} UTM) se exigen al menos {min_days} días corridos entre publicación y cierre.",
                    })

        if amount_utm >= 1000:
            if committee_members is not None and committee_members < 3:
                findings.append({
                    "severity": "Bloqueante",
                    "source": "determinista",
                    "norm": "D.S. 661/2024 / Ley 19.886",
                    "code": "committee_under_three",
                    "title": "Comisión Evaluadora con menos de 3 integrantes",
                    "detail": "Las licitaciones iguales o superiores a 1.000 UTM (tramos LP y LR) consideran Comisión Evaluadora integrada por al menos 3 funcionarios.",
                })
            if performance_guarantee_pct is not None and performance_guarantee_pct == 0:
                findings.append({
                    "severity": "Alta",
                    "source": "determinista",
                    "norm": "D.S. 661/2024 Art. 121",
                    "code": "missing_performance_guarantee",
                    "title": "Falta garantía de fiel cumplimiento en monto ≥ 1.000 UTM",
                    "detail": "Desde 1.000 UTM revise la exigencia de garantía de fiel cumplimiento (regla general 5% del precio neto).",
                })

        if seriousness_guarantee_pct is not None:
            if seriousness_guarantee_pct > 3:
                findings.append({
                    "severity": "Bloqueante",
                    "source": "determinista",
                    "norm": "D.S. 661/2024 Art. 52",
                    "code": "seriousness_guarantee_over_cap",
                    "title": f"Garantía de seriedad excede el tope legal ({seriousness_guarantee_pct}%)",
                    "detail": "La garantía de seriedad de la oferta no debe superar el 3% del monto estimado.",
                })
            elif seriousness_guarantee_pct > 0 and amount_utm <= 5000:
                findings.append({
                    "severity": "Media",
                    "source": "determinista",
                    "norm": "D.S. 661/2024 Art. 52",
                    "code": "seriousness_guarantee_under_5000_utm",
                    "title": "Garantía de seriedad exigida en licitación ≤ 5.000 UTM",
                    "detail": "En contrataciones iguales o inferiores a 5.000 UTM la exigencia de garantía de seriedad requiere ponderación de riesgo y justificación expresa.",
                })

    if closing_datetime:
        close_date = closing_datetime.date()
        if is_chile_non_business_day(close_date):
            findings.append({
                "severity": "Bloqueante",
                "source": "determinista",
                "norm": "D.S. 661/2024 Art. 36",
                "code": "closing_on_non_business_day",
                "title": "Fecha de cierre en día inhábil o feriado",
                "detail": f"El cierre ({close_date}) no debe fijarse en sábado, domingo ni feriado fijo configurado.",
            })
        else:
            prev_day = close_date - timedelta(days=1)
            if (close_date.weekday() == 0 or is_chile_non_business_day(prev_day)) and closing_datetime.hour < 15:
                findings.append({
                    "severity": "Bloqueante",
                    "source": "determinista",
                    "norm": "D.S. 661/2024 Art. 36",
                    "code": "closing_before_1500_after_holiday",
                    "title": "Cierre en lunes o día siguiente a inhábil antes de las 15:00 hrs",
                    "detail": f"El cierre está fijado a las {closing_datetime.strftime('%H:%M')}. El Art. 36 establece cierre desde las 15:00 hrs en lunes o día hábil inmediatamente siguiente a un inhábil.",
                })

    if criteria_weights_sum is not None and abs(criteria_weights_sum - 100.0) > 0.01:
        findings.append({
            "severity": "Bloqueante",
            "source": "determinista",
            "norm": "Principio de estricta sujeción a las bases / D.S. 661",
            "code": "weights_not_100",
            "title": f"Ponderación de criterios suma {criteria_weights_sum}% (debe ser 100%)",
            "detail": "Las ponderaciones técnicas y económicas deben sumar 100% para evitar inconsistencias en la evaluación.",
        })

    if max_fine_pct is not None and max_fine_pct > 30:
        findings.append({
            "severity": "Bloqueante",
            "source": "determinista",
            "norm": "Ley 21.634 / Ley 19.886 Art. 13",
            "code": "fine_cap_exceeds_30",
            "title": f"Tope de multas supera el máximo legal de 30% ({max_fine_pct}%)",
            "detail": "El tope máximo de multas no puede exceder el 30% del precio total del contrato.",
        })

    lower = full_text.lower()
    if re.search(r"\b(?:d\.?s\.?\s*n?°?\s*250|decreto\s+(?:supremo\s+)?(?:n°\s*)?250)\b", lower):
        findings.append({
            "severity": "Alta",
            "source": "determinista",
            "norm": "D.S. 661/2024 (derogó D.S. 250)",
            "code": "outdated_ds250_reference",
            "title": "Referencia normativa obsoleta al Decreto Supremo N° 250",
            "detail": "El texto cita el D.S. 250, reemplazado por el Reglamento D.S. N° 661/2024.",
        })

    for m in re.finditer(r"\b(marca\s+[a-z0-9áéíóúñ-]{2,}|exclusivamente\s+marca|modelo\s+espec[ií]fico)\b", lower):
        window = lower[max(0, m.start() - 80): min(len(lower), m.end() + 120)]
        if "equivalente" not in window and "similar" not in window:
            findings.append({
                "severity": "Alta",
                "source": "determinista",
                "norm": "Ley 19.886 / Ley 21.634 (Libre concurrencia)",
                "code": "possible_brand_restriction",
                "title": "Posible direccionamiento a marca o modelo sin cláusula 'o equivalente'",
                "detail": f"Fragmento detectado: '...{full_text[max(0, m.start()-40):min(len(full_text), m.end()+60)].strip()}...'. Salvo justificación técnica excepcional, revise permitir equivalentes.",
            })
            break

    if amount_utm is not None and amount_utm >= 1000:
        if "comisión evaluadora" not in lower and "comision evaluadora" not in lower:
            findings.append({
                "severity": "Media",
                "source": "determinista",
                "norm": "D.S. 661/2024",
                "code": "no_committee_mention",
                "title": "No se identifica cláusula de Comisión Evaluadora en texto",
                "detail": "Para licitaciones ≥ 1.000 UTM revise incorporar la conformación de la Comisión Evaluadora y declaración de conflictos de interés.",
            })

    ai_meta = {"status": "skipped", "provider": "none", "pii_redactions": {}}
    if use_ai and len(full_text.strip()) >= 80:
        sys_prompt = (
            "Eres un asistente de revisión preliminar sobre Compras Públicas de Chile (Ley 19.886, Ley 21.634 y D.S. 661/2024). "
            "Identifica posibles riesgos o cláusulas a revisar y responde SOLO en JSON válido con: "
            '{"findings": [{"severity": "Bloqueante|Alta|Media", "norm": "artículo o ley", "title": "título breve", "detail": "explicación con cita textual"}]}. '
            "No inventes hallazgos sin evidencia en el texto."
        )
        excerpt = full_text[:12000]
        ai_res = analyze_json(sys_prompt, f"Revisa posibles puntos de atención en estas bases:\n\n{excerpt}")
        ai_meta = {k: v for k, v in ai_res.items() if k != "data"}
        if ai_res.get("status") == "ok" and isinstance(ai_res.get("data"), dict):
            for item in ai_res["data"].get("findings", [])[:8]:
                if isinstance(item, dict) and item.get("title"):
                    findings.append({
                        "severity": item.get("severity", "Media"),
                        "source": f"ia:{ai_res.get('provider')}",
                        "norm": item.get("norm", "Ley 19.886 / D.S. 661"),
                        "code": "ai_semantic_finding",
                        "title": str(item.get("title"))[:200],
                        "detail": str(item.get("detail", ""))[:1000],
                    })

    order = {"Bloqueante": 0, "Alta": 1, "Media": 2}
    findings.sort(key=lambda x: order.get(x.get("severity", "Media"), 3))
    blockers = sum(1 for f in findings if f.get("severity") == "Bloqueante")

    return {
        "amount_utm": amount_utm,
        "utm_value_clp": utm_value_clp,
        "bracket": bracket,
        "ready_to_publish": blockers == 0,
        "disclaimer": "Filtro preliminar automatizado; no constituye certificación jurídica ni reemplaza la revisión legal e institucional.",
        "blockers_count": blockers,
        "total_findings": len(findings),
        "findings": findings,
        "ai_review": ai_meta,
    }
