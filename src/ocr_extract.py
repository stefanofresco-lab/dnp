"""Estrazione testo (PDF nativo o OCR) e parsing dell'indirizzo di consegna da DDT italiani."""
import io
import re

import pdfplumber
import pytesseract
from PIL import Image

MIN_NATIVE_TEXT_LEN = 40  # sotto questa soglia consideriamo il PDF "scansionato" e serve OCR

ADDRESS_RE = re.compile(
    r"((?:Via|V\.le|Viale|Corso|C\.so|Piazza|P\.zza|P\.za|Piazzale|P\.le|Strada|Loc\.|"
    r"Localit[àa]|Vicolo|Frazione|Fraz\.|Largo|Contrada|C\.da|S\.S\.|S\.P\.|SS|SP)\.?\s+"
    r"[^\n\d]{2,60}?\d+[\w/]*)",
    re.IGNORECASE,
)

CAP_CITY_PROV_RE = re.compile(
    r"(\d{5})\s+([A-Za-zÀ-ÖØ-öø-ÿ'\.\s]+?)\s*\(([A-Za-z]{2})\)"
)

# "destinatario" e' un fallback DEBOLE: se nel documento compare anche una
# sezione "Destinazione" separata (blocchi impilati, non colonne affiancate),
# va sempre preferita quella, anche se "Destinatario" compare prima nel testo.
PRIORITY_BLOCK_HEADERS = [
    "destinazione",
    "luogo di consegna",
    "consegna presso",
    "consegnare a",
]
FALLBACK_BLOCK_HEADERS = ["destinatario"]

BLOCK_STOP_MARKERS = ["codice", "descrizione", "quantit", "c.f.", "p.iva", "articolo"]


def dedupe_repeated(s: str) -> str:
    """Se una stringa e' la ripetizione di se stessa (colonne PDF affiancate estratte
    sulla stessa riga), ritorna solo la prima meta'."""
    s = s.strip()
    n = len(s)
    for split in range(1, n):
        first, second = s[:split].strip(), s[split:].strip()
        if first and first == second:
            return first
    return s


def extract_destination_column_text(data: bytes) -> str:
    """Nei DDT con layout a due colonne affiancate (Destinatario | Destinazione),
    isola geometricamente SOLO la colonna Destinazione usando la posizione X
    della parola nel PDF, cosi' non si confonde con il Destinatario quando i due
    indirizzi sono diversi (es. consegna presso una sede diversa dall'intestatario).
    Ritorna stringa vuota se il layout non e' applicabile (PDF scansionato senza
    testo nativo, oppure nessun blocco "Destinazione" individuabile)."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                words = page.extract_words()
                dest_word = next(
                    (w for w in words if w["text"].strip().lower().startswith("destinazion")),
                    None,
                )
                if dest_word is None:
                    continue
                x0 = max(0, dest_word["x0"] - 5)
                top = dest_word["top"]
                bottom = min(page.height, top + 130)
                cropped = page.within_bbox((x0, top, page.width, bottom))
                text = (cropped.extract_text() or "").strip()
                if text:
                    return text
    except Exception:
        pass
    return ""


def extract_text_from_pdf_bytes(data: bytes) -> str:
    text_parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    native_text = "\n".join(text_parts)

    if len(native_text.strip()) >= MIN_NATIVE_TEXT_LEN:
        return native_text

    # PDF scansionato: converte le pagine in immagini e applica OCR
    from pdf2image import convert_from_bytes

    ocr_parts = []
    images = convert_from_bytes(data, dpi=300)
    for img in images:
        ocr_parts.append(pytesseract.image_to_string(img, lang="ita"))
    return "\n".join(ocr_parts)


def extract_text_from_image_bytes(data: bytes) -> str:
    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img, lang="ita")


def extract_text(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_text_from_pdf_bytes(data)
    if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
        return extract_text_from_image_bytes(data)
    raise ValueError(f"Formato file non supportato: {filename}")


def _find_block(lines):
    """Individua il blocco di righe relativo all'indirizzo di DESTINAZIONE (consegna).
    Cerca prima un'intestazione "Destinazione"/equivalenti ovunque nel documento;
    solo se non esiste affatto ricade su "Destinatario" (che potrebbe non
    coincidere con il luogo di consegna effettivo)."""
    lower_lines = [l.lower() for l in lines]

    header_idx = None
    for i, l in enumerate(lower_lines):
        if any(h in l for h in PRIORITY_BLOCK_HEADERS):
            header_idx = i
            break

    if header_idx is None:
        for i, l in enumerate(lower_lines):
            if any(h in l for h in FALLBACK_BLOCK_HEADERS):
                header_idx = i
                break

    start = header_idx + 1 if header_idx is not None else 0
    block = []
    for l in lines[start:start + 8]:
        stripped = l.strip()
        if not stripped:
            if block:
                break
            continue
        if any(m in stripped.lower() for m in BLOCK_STOP_MARKERS):
            break
        block.append(stripped)
        if len(block) >= 4:
            break
    return block


def _last_match(pattern, text):
    """Ritorna l'ultimo match trovato nel testo. Quando una riga contiene due
    colonne affiancate (Destinatario poi Destinazione), l'ultimo match
    corrisponde alla colonna di destra (Destinazione)."""
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def parse_ddt(text: str, prefer_last: bool = True) -> dict:
    """Estrae Cliente / Indirizzo / CAP / Citta' / Provincia dal testo di un DDT.
    Se `prefer_last` e' True (default), quando una riga contiene due colonne
    affiancate (Destinatario | Destinazione) viene preferita l'ultima
    occorrenza (colonna di destra = Destinazione)."""
    lines = [l for l in text.splitlines()]
    block = _find_block(lines)
    search_scope = "\n".join(block) if block else text

    result = {
        "cliente": "",
        "indirizzo": "",
        "cap": "",
        "citta": "",
        "provincia": "",
        "indirizzo_completo": "",
        "trovato": False,
    }

    match_fn = (lambda p, t: _last_match(p, t)) if prefer_last else (lambda p, t: p.search(t))

    addr_match = match_fn(ADDRESS_RE, search_scope)
    cap_match = match_fn(CAP_CITY_PROV_RE, search_scope)

    if not addr_match or not cap_match:
        # fallback: cerca su tutto il documento saltando l'intestazione mittente (prime righe)
        rest_text = "\n".join(lines[3:]) if len(lines) > 3 else text
        addr_match = addr_match or match_fn(ADDRESS_RE, rest_text)
        cap_match = cap_match or match_fn(CAP_CITY_PROV_RE, rest_text)

    if block:
        result["cliente"] = dedupe_repeated(block[0])

    if addr_match:
        result["indirizzo"] = addr_match.group(1).strip().rstrip(",")
    if cap_match:
        result["cap"] = cap_match.group(1).strip()
        result["citta"] = cap_match.group(2).strip()
        result["provincia"] = cap_match.group(3).strip().upper()

    cap_citta = " ".join(p for p in [result["cap"], result["citta"]] if p)
    parts = [result["indirizzo"], cap_citta]
    result["indirizzo_completo"] = ", ".join(p for p in parts if p)
    if result["provincia"]:
        result["indirizzo_completo"] += f" ({result['provincia']})"

    result["trovato"] = bool(result["indirizzo"] and result["cap"])
    return result


def extract_delivery_info(filename: str, data: bytes) -> dict:
    """Punto di ingresso principale: estrae Cliente/Indirizzo/CAP/Citta/Provincia
    della DESTINAZIONE (non del Destinatario/intestatario, se diversi) da un DDT
    in PDF o foto. Per i PDF con testo nativo prova prima l'isolamento geometrico
    della colonna "Destinazione"; se non applicabile (PDF scansionato, layout
    diverso) ricade sul parsing testuale con preferenza per la colonna di destra."""
    lower = filename.lower()

    if lower.endswith(".pdf"):
        dest_col_text = extract_destination_column_text(data)
        if dest_col_text:
            info = parse_ddt(dest_col_text, prefer_last=False)
            # Se il ritaglio geometrico ha isolato la colonna Destinazione e ci ha
            # trovato QUALCOSA (anche solo CAP/citta o solo indirizzo), va sempre
            # preferito: e' l'unica fonte certa di non confondersi con il
            # Destinatario. Il testo unito (fallback sotto) puo' sfalsarsi quando
            # le due colonne hanno un numero diverso di righe.
            if info["cap"] or info["indirizzo"]:
                return info
        text = extract_text_from_pdf_bytes(data)
        return parse_ddt(text, prefer_last=True)

    if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
        text = extract_text_from_image_bytes(data)
        return parse_ddt(text, prefer_last=True)

    raise ValueError(f"Formato file non supportato: {filename}")
