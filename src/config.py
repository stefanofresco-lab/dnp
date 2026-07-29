"""Costanti di configurazione del pianificatore giri DNP Pharma."""
import re
from datetime import time

DEPOT_NAME = "Deposito DNP Pharma - Dormelletto"
DEPOT_ADDRESS = "Via Matteotti, 28040 Dormelletto (NO), Italia"

DEFAULT_DEPARTURE = time(7, 30)
DEPARTURE_MIN = time(7, 30)
DEPARTURE_MAX = time(8, 0)
DEFAULT_RETURN_DEADLINE = time(16, 30)

SERVICE_TIME_MIN = 20  # minuti fissi di scarico per tappa

LUNCH_START = time(12, 30)
LUNCH_END = time(14, 0)
LAST_MORNING_ARRIVAL_DEADLINE = time(12, 10)

# Velocità media di riferimento (km/h) usata solo come fallback quando OSRM
# non è raggiungibile (distanza stimata via Haversine * fattore rete stradale).
FALLBACK_SPEED_KMH = 45.0
ROAD_NETWORK_FACTOR = 1.30  # correzione Haversine -> percorso stradale reale

# Fasce orarie con traffico storico simulato: (ora_inizio, ora_fine, moltiplicatore_tempo)
TRAFFIC_BANDS = [
    (time(8, 0), time(9, 0), 1.20),
    (time(13, 0), time(14, 0), 1.20),
]

CONSTRAINT_NONE = "Nessuno"
CONSTRAINT_MORNING = "Solo Mattina"
CONSTRAINT_AFTERNOON = "Solo Pomeriggio"
CONSTRAINT_OPTIONS = [CONSTRAINT_NONE, CONSTRAINT_MORNING, CONSTRAINT_AFTERNOON]

_VINCOLO_TIME_TOKEN_RE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?")
_VINCOLO_POSIZIONE_NUM_RE = re.compile(r"(?:consegna|posizione|tappa)\s*n?°?\.?\s*(\d+)")

_VINCOLO_EMPTY = {
    "tipo": "nessuno", "orario_min": None, "orario_max": None,
    "posizione_da_fine": None, "posizione_assoluta": None,
}


def _parse_time_tokens(v: str):
    """Estrae tutti gli orari plausibili (in minuti) da una stringa breve
    scritta dall'utente, es. "tra le 8:30 e le 9" -> [510, 540]."""
    times = []
    for m in _VINCOLO_TIME_TOKEN_RE.finditer(v):
        h = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        if 0 <= h <= 23 and 0 <= mm <= 59:
            times.append(h * 60 + mm)
    return times


def parse_vincolo(value) -> dict:
    """Interpreta il campo Priorita'/Vincolo di una tappa, scritto in formato libero.
    Riconosce, in ordine di priorita':
    - posizione forzata nel giro: "Ultima Consegna", "Penultima Consegna",
      "Terzultima Consegna", oppure "Consegna 7"/"Posizione 7" (posizione assoluta,
      utile per liberare il furgone/magazzino in un ordine preciso);
    - finestra oraria a due estremi (es. "tra le 8:30 e le 9", "dalle 8:30 alle 9:00");
    - un orario tassativo singolo (es. "Entro le 10:30");
    - "Solo Mattina", "Solo Pomeriggio";
    - nessun vincolo (automatico).
    Ritorna un dizionario con "tipo" in
    "nessuno"|"mattina"|"pomeriggio"|"deadline"|"finestra"|"posizione"."""
    if not isinstance(value, str) or not value.strip():
        return dict(_VINCOLO_EMPTY)

    v = value.strip().lower()

    m = _VINCOLO_POSIZIONE_NUM_RE.search(v)
    if m:
        return {**_VINCOLO_EMPTY, "tipo": "posizione", "posizione_assoluta": int(m.group(1))}
    if "terzultima" in v:
        return {**_VINCOLO_EMPTY, "tipo": "posizione", "posizione_da_fine": 2}
    if "penultima" in v:
        return {**_VINCOLO_EMPTY, "tipo": "posizione", "posizione_da_fine": 1}
    if "ultima" in v:
        return {**_VINCOLO_EMPTY, "tipo": "posizione", "posizione_da_fine": 0}

    times = _parse_time_tokens(v)
    if len(times) >= 2:
        lo, hi = sorted(times[:2])
        return {**_VINCOLO_EMPTY, "tipo": "finestra", "orario_min": lo, "orario_max": hi}
    if len(times) == 1:
        return {**_VINCOLO_EMPTY, "tipo": "deadline", "orario_min": times[0]}
    if "mattin" in v:
        return {**_VINCOLO_EMPTY, "tipo": "mattina"}
    if "pomerig" in v:
        return {**_VINCOLO_EMPTY, "tipo": "pomeriggio"}
    return dict(_VINCOLO_EMPTY)


def describe_vincolo(vincolo: dict) -> str:
    """Rappresentazione leggibile di un vincolo (per le tabelle mostrate all'utente)."""
    vincolo = vincolo or {}
    tipo = vincolo.get("tipo", "nessuno")
    if tipo == "mattina":
        return CONSTRAINT_MORNING
    if tipo == "pomeriggio":
        return CONSTRAINT_AFTERNOON
    if tipo == "deadline":
        m = int(vincolo.get("orario_min") or 0)
        return f"Entro le {(m // 60) % 24:02d}:{m % 60:02d}"
    if tipo == "finestra":
        lo = int(vincolo.get("orario_min") or 0)
        hi = int(vincolo.get("orario_max") or 0)
        return (f"Tra le {(lo // 60) % 24:02d}:{lo % 60:02d} e le "
                f"{(hi // 60) % 24:02d}:{hi % 60:02d}")
    if tipo == "posizione":
        if vincolo.get("posizione_assoluta") is not None:
            return f"Posizione {vincolo['posizione_assoluta']} nel giro"
        nomi = {0: "Ultima Consegna", 1: "Penultima Consegna", 2: "Terzultima Consegna"}
        return nomi.get(vincolo.get("posizione_da_fine"), "Posizione forzata")
    return CONSTRAINT_NONE

MAX_STOPS_PER_MAPS_LINK = 8  # Google Maps: origine + 8 tappe + destinazione = 10 punti

GEOCODE_CACHE_PATH = "data/geocode_cache.json"
NOMINATIM_USER_AGENT = "dnp-pharma-delivery-planner"

OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving/"
OSRM_TIMEOUT_SEC = 8
