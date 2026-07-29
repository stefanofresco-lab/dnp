"""Geocodifica indirizzi in coordinate lat/lon usando Nominatim (OpenStreetMap, gratuito)."""
import json
import os
import time as time_mod

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

from . import config

_geolocator = Nominatim(user_agent=config.NOMINATIM_USER_AGENT, timeout=10)
_geocode_raw = RateLimiter(_geolocator.geocode, min_delay_seconds=1.1, max_retries=2)


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


def geocode_address(address: str):
    """Ritorna (lat, lon, display_name) oppure (None, None, None) se non trovato."""
    key = address.strip().lower()
    if key in _CACHE:
        entry = _CACHE[key]
        return entry["lat"], entry["lon"], entry["display_name"]

    location = None
    try:
        location = _geocode_raw(address, country_codes="it", exactly_one=True)
    except Exception:
        location = None

    if location is None:
        # Riprova rimuovendo dettagli minori (es. numero civico) per aumentare il match rate
        simplified = ",".join(address.split(",")[-2:]) if "," in address else address
        try:
            location = _geocode_raw(simplified, country_codes="it", exactly_one=True)
        except Exception:
            location = None

    if location is None:
        return None, None, None

    _CACHE[key] = {
        "lat": location.latitude,
        "lon": location.longitude,
        "display_name": location.address,
    }
    _save_cache(_CACHE)
    return location.latitude, location.longitude, location.address
