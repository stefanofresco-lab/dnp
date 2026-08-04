"""Geocodifica indirizzi in coordinate lat/lon usando Nominatim (OpenStreetMap, gratuito).

Nominatim e' meno completo di Google Maps per molti indirizzi civici precisi,
quindi qui si tenta una CASCATA di query via via piu' generiche (query
strutturata, testo libero completo, senza numero civico, solo CAP/citta',
solo citta') prima di arrendersi — spesso basta un formato leggermente
diverso perche' lo stesso indirizzo, che esiste davvero, venga trovato."""
import json
import os
import re

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

from . import config

_geolocator = Nominatim(user_agent=config.NOMINATIM_USER_AGENT, timeout=10)
_geocode_raw = RateLimiter(_geolocator.geocode, min_delay_seconds=1.1, max_retries=2)

_CIVICO_RE = re.compile(r"\s*,?\s*\d+\s*\w{0,3}\s*$")


def _load_cache():
    if os.path.exists(config.GEOCODE_CACHE_PATH):
        with open(config.GEOCODE_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(config.GEOCODE_CACHE_PATH), exist_ok=True)
    with open(config.GEOCODE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


_CACHE = _load_cache()


def _try_geocode(query):
    try:
        return _geocode_raw(query, country_codes="it", exactly_one=True)
    except Exception:
        return None


def geocode_address(address: str):
    """Geocodifica una singola stringa indirizzo gia' formattata (usato per il
    deposito). Ritorna (lat, lon, display_name) oppure (None, None, None)."""
    key = address.strip().lower()
    if key in _CACHE:
        entry = _CACHE[key]
        return entry["lat"], entry["lon"], entry["display_name"]

    location = _try_geocode(address)
    if location is None:
        simplified = ",".join(address.split(",")[-2:]) if "," in address else address
        location = _try_geocode(simplified)

    if location is None:
        return None, None, None

    _CACHE[key] = {
        "lat": location.latitude,
        "lon": location.longitude,
        "display_name": location.address,
    }
    _save_cache(_CACHE)
    return location.latitude, location.longitude, location.address


def geocode_stop(indirizzo: str, cap: str, citta: str, provincia: str = ""):
    """Geocodifica una tappa a partire dai campi separati, tentando piu'
    varianti via via piu' generiche se le prime falliscono. Ritorna
    (lat, lon, display_name) oppure (None, None, None) se nessuna variante
    trova un risultato."""
    indirizzo = (indirizzo or "").strip()
    cap = (cap or "").strip()
    citta = (citta or "").strip()
    provincia = (provincia or "").strip()

    cache_key = f"{indirizzo}|{cap}|{citta}|{provincia}".strip().lower()
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        return entry["lat"], entry["lon"], entry["display_name"]

    cap_citta = " ".join(p for p in [cap, citta] if p)
    indirizzo_senza_civico = _CIVICO_RE.sub("", indirizzo).strip() if indirizzo else ""

    candidates = []

    # 1) query strutturata (spesso la piu' affidabile quando i campi sono puliti)
    if indirizzo or citta:
        struct = {"country": "Italy"}
        if indirizzo:
            struct["street"] = indirizzo
        if citta:
            struct["city"] = citta
        if cap:
            struct["postalcode"] = cap
        candidates.append(struct)

    # 2) testo libero completo
    full_text = ", ".join(p for p in [indirizzo, cap_citta] if p)
    if full_text:
        candidates.append(full_text)

    # 3) via senza numero civico + citta' (nel caso Nominatim non abbia quel civico esatto)
    if indirizzo_senza_civico and citta and indirizzo_senza_civico.lower() != indirizzo.lower():
        candidates.append(f"{indirizzo_senza_civico}, {citta}")

    # 4) solo CAP + citta'
    if cap_citta:
        candidates.append(cap_citta)

    # 5) solo citta' (ultima spiaggia: posiziona almeno vicino al centro citta')
    if citta:
        candidates.append(citta)

    location = None
    for candidate in candidates:
        location = _try_geocode(candidate)
        if location is not None:
            break

    if location is None:
        return None, None, None

    _CACHE[cache_key] = {
        "lat": location.latitude,
        "lon": location.longitude,
        "display_name": location.address,
    }
    _save_cache(_CACHE)
    return location.latitude, location.longitude, location.address
