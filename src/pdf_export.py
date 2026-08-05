"""Genera il PDF stampabile "Ordine Consegne" con il giro nell'ordine calcolato:
logo al centro, titolo, e per ogni tappa il nome della struttura con il suo indirizzo."""
import io
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, HRFlowable

LOGO_PATH = "assets/logo_dnp_pharma.png"


def _logo_flowable(max_width_mm=70):
    if not os.path.exists(LOGO_PATH):
        return None
    from PIL import Image as PILImage
    with PILImage.open(LOGO_PATH) as im:
        w, h = im.size
    width = max_width_mm * mm
    height = width * (h / w)
    return Image(LOGO_PATH, width=width, height=height)


def generate_delivery_order_pdf(sim, stops, depot_address, departure_hhmm):
    """sim: risultato di optimizer.solve() (usa sim['schedule'] e sim['order']).
    stops: lista di tappe come passate all'optimizer (per l'indirizzo completo).
    Ritorna i byte del PDF pronto per il download."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )

    title_style = ParagraphStyle(
        "TitoloOrdine", fontName="Helvetica-Bold", fontSize=22, leading=28,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "Sottotitolo", fontName="Helvetica", fontSize=10, leading=14,
        alignment=TA_CENTER, textColor=HexColor("#555555"),
    )
    cliente_style = ParagraphStyle(
        "NomeStruttura", fontName="Helvetica-Bold", fontSize=13, leading=16,
    )
    indirizzo_style = ParagraphStyle(
        "Indirizzo", fontName="Helvetica", fontSize=10.5, leading=14, textColor=HexColor("#333333"),
    )
    dettaglio_style = ParagraphStyle(
        "Dettaglio", fontName="Helvetica-Oblique", fontSize=9, leading=12, textColor=HexColor("#666666"),
    )

    elements = []

    logo = _logo_flowable()
    if logo:
        logo.hAlign = "CENTER"
        elements.append(logo)
        elements.append(Spacer(1, 10 * mm))

    elements.append(Paragraph("Ordine Consegne", title_style))
    elements.append(Spacer(1, 4 * mm))
    elements.append(Paragraph(
        f"Partenza dal deposito ({depot_address}) alle ore {departure_hhmm}",
        subtitle_style,
    ))
    elements.append(Spacer(1, 6 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.75, color=HexColor("#cccccc")))

    for row in sim["schedule"]:
        stop_i = sim["order"][row["posizione"] - 1]
        stop = stops[stop_i]
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph(f"{row['posizione']}. {row['cliente']}", cliente_style))
        elements.append(Paragraph(stop.get("indirizzo_completo", row["indirizzo"]), indirizzo_style))
        dettagli = f"Arrivo previsto: {row['arrivo']} — Scarico: {row['inizio_scarico']}–{row['fine_scarico']}"
        if row.get("vincolo") and row["vincolo"] != "Nessuno":
            dettagli += f" — Vincolo: {row['vincolo']}"
        elements.append(Paragraph(dettagli, dettaglio_style))

    elements.append(Spacer(1, 6 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.75, color=HexColor("#cccccc")))
    elements.append(Paragraph(
        f"Rientro previsto alle {sim['arrival_depot_hhmm']} — {sim['total_km']} km totali",
        subtitle_style,
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
