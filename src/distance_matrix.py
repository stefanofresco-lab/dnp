"""Costruzione della matrice distanze/tempi tra deposito e tappe.

Se e' configurata una chiave TomTom (variabile d'ambiente/secret TOMTOM_API_KEY),
usa la Matrix Routing API v2 di TomTom con traffico live reale (la fonte piu'
precisa, verificata su tratte reali). Altrimenti — o se TomTom non risponde —
usa il servizio pubblico e gratuito OSRM (Open Source Routing Machine), che non
ha traffico live ma e' comunque gratuito e senza chiave. Se nemmeno OSRM e'
raggiungibile (rete assente, timeout) ricade infine su una stima Haversine
corretta da un fattore di tortuosita' della rete stradale. Tre livelli, dal piu'
preciso al piu' robusto, cosi' il giro resta sempre calcolabile.
"""
import math
import os

import requests

from . import config


def _get_tomtom_key():
    """Ritorna la chiave TomTom se configurata, altrimenti None. Cerca prima
    nei secrets di Streamlit, poi nelle variabili d'ambiente (Render e altre
    piattaforme senza il meccanismo st.secrets)."""
    try:
        import streamlit as st
        key = st.secrets.get("tomtom_api_key") or st.secrets.get("TOMTOM_API_KEY")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("TOMTOM_API_KEY") or None


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _fallback_matrix(points):
    n = len(points)
    dist_km = [[0.0] * n for _ in range(n)]
    dur_min = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            km = haversine_km(*points[i], *points[j]) * config.ROAD_NETWORK_FACTOR
            dist_km[i][j] = km
            dur_min[i][j] = km / config.FALLBACK_SPEED_KMH * 60.0
    return dist_km, dur_min


def _osrm_matrix(points):
    coords_str = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{config.OSRM_TABLE_URL}{coords_str}?annotations=distance,duration"

    resp = requests.get(url, timeout=config.OSRM_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok":
        raise ValueError(f"OSRM error: {data.get('code')}")
    distances_m = data["distances"]
    durations_s = data["durations"]

    dist_km = [[(d or 0) / 1000.0 for d in row] for row in distances_m]
    # OSRM stima i tempi solo dai limiti di velocità (nessun semaforo/ZTL/coda
    # reale): si corregge sempre con ROAD_TIME_CORRECTION_FACTOR per avvicinarsi
    # ai tempi reali; il traffico orario (fasce di punta) si applica poi a parte.
    dur_min = [
        [(d or 0) / 60.0 * config.ROAD_TIME_CORRECTION_FACTOR for d in row]
        for row in durations_s
    ]
    return dist_km, dur_min


def _tomtom_matrix(points, api_key):
    """Matrix Routing API v2 di TomTom (sincrona, un'unica chiamata per tutta
    la matrice origini x destinazioni), con traffico live reale incluso
    (departAt=now). Nessuna correzione ulteriore necessaria sui tempi: sono
    gia' tempi reali di percorrenza, a differenza della stima di OSRM."""
    url = f"https://api.tomtom.com/routing/matrix/2?key={api_key}"
    body = {
        "origins": [{"point": {"latitude": lat, "longitude": lon}} for lat, lon in points],
        "destinations": [{"point": {"latitude": lat, "longitude": lon}} for lat, lon in points],
        "options": {"departAt": "now", "routeType": "fastest", "traffic": "live"},
    }
    resp = requests.post(url, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    n = len(points)
    dist_km = [[0.0] * n for _ in range(n)]
    dur_min = [[0.0] * n for _ in range(n)]
    for cell in data.get("data", []):
        i = cell["originIndex"]
        j = cell["destinationIndex"]
        if i == j:
            continue
        summary = cell.get("routeSummary")
        if not summary:
            # TomTom non trova un percorso per questa coppia (es. punto non
            # raggiungibile su strada): si segnala con un'eccezione, cosi'
            # l'intera matrice ricade su OSRM invece di avere celle a zero
            # che falserebbero il calcolo del giro.
            raise ValueError(f"TomTom: nessun percorso per la coppia {i}->{j}")
        dist_km[i][j] = summary["lengthInMeters"] / 1000.0
        dur_min[i][j] = summary["travelTimeInSeconds"] / 60.0
    return dist_km, dur_min


def build_matrices(points):
    """points: lista di tuple (lat, lon), indice 0 = deposito.
    Ritorna (dist_km_matrix, duration_min_matrix) — tempi in condizioni di
    traffico normale (senza correzione fasce orarie, applicata a runtime).
    Prova TomTom (se configurato) -> OSRM -> stima Haversine, in quest'ordine.
    """
    n = len(points)
    if n < 2:
        return [[0.0]], [[0.0]]

    tomtom_key = _get_tomtom_key()
    if tomtom_key:
        try:
            return _tomtom_matrix(points, tomtom_key)
        except Exception:
            pass  # si prosegue con OSRM

    try:
        return _osrm_matrix(points)
    except Exception:
        return _fallback_matrix(points)
