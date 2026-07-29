"""Costruzione della matrice distanze/tempi tra deposito e tappe.

Usa il servizio pubblico e gratuito OSRM (Open Source Routing Machine) per ottenere
distanze/tempi reali sulla rete stradale. Se il servizio non e' raggiungibile
(rete assente, timeout) ricade su una stima Haversine corretta da un fattore di
tortuosita' della rete stradale.
"""
import math

import requests

from . import config


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


def build_matrices(points):
    """points: lista di tuple (lat, lon), indice 0 = deposito.
    Ritorna (dist_km_matrix, duration_min_matrix) — tempi in condizioni di
    traffico normale (senza correzione fasce orarie, applicata a runtime).
    """
    n = len(points)
    if n < 2:
        return [[0.0]], [[0.0]]

    coords_str = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{config.OSRM_TABLE_URL}{coords_str}?annotations=distance,duration"

    try:
        resp = requests.get(url, timeout=config.OSRM_TIMEOUT_SEC)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            raise ValueError(f"OSRM error: {data.get('code')}")
        distances_m = data["distances"]
        durations_s = data["durations"]

        dist_km = [[(d or 0) / 1000.0 for d in row] for row in distances_m]
        dur_min = [[(d or 0) / 60.0 for d in row] for row in durations_s]
        return dist_km, dur_min
    except Exception:
        return _fallback_matrix(points)
