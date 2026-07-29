"""Generazione link di navigazione Google Maps, con split automatico oltre 8 tappe
(limite pratico: origine + 8 destinazioni + ritorno = 10 punti per link, gestibile
nativamente dall'app Google Maps su Android Auto / da Safari + Google Maps su iPhone/CarPlay)."""
from . import config


def _coord_str(lat, lon):
    return f"{lat},{lon}"


def _build_link(origin, destination, waypoints):
    url = "https://www.google.com/maps/dir/?api=1"
    url += f"&origin={_coord_str(*origin)}"
    url += f"&destination={_coord_str(*destination)}"
    if waypoints:
        wp_str = "|".join(_coord_str(*w) for w in waypoints)
        url += f"&waypoints={wp_str}"
    url += "&travelmode=driving&dir_action=navigate"
    return url


def build_navigation_links(depot_coord, ordered_stop_coords):
    """depot_coord: (lat, lon). ordered_stop_coords: lista di (lat, lon) nell'ordine
    ottimizzato di visita. Ritorna una lista di dict {"label": str, "url": str,
    "tappe": [indici 1-based delle tappe coperte]}.
    """
    n = len(ordered_stop_coords)
    max_per_link = config.MAX_STOPS_PER_MAPS_LINK
    links = []

    if n == 0:
        return links

    if n <= max_per_link:
        links.append({
            "label": "Giro completo",
            "url": _build_link(depot_coord, depot_coord, ordered_stop_coords),
            "tappe": list(range(1, n + 1)),
        })
        return links

    # split in gruppi: ogni link copre al massimo max_per_link tappe.
    # Il link N parte dall'ultima tappa del link N-1 (o dal deposito per il primo)
    # e termina sulla ultima tappa del gruppo; l'ultimo link rientra al deposito.
    idx = 0
    origin = depot_coord
    link_num = 1
    while idx < n:
        group = ordered_stop_coords[idx: idx + max_per_link]
        is_last_group = (idx + max_per_link) >= n

        if is_last_group:
            destination = depot_coord
            waypoints = group
        else:
            destination = group[-1]
            waypoints = group[:-1]

        tappe_range = list(range(idx + 1, idx + len(group) + 1))
        label = f"Link {link_num} (tappe {tappe_range[0]}-{tappe_range[-1]}" + (
            " + rientro deposito)" if is_last_group else ")"
        )

        links.append({
            "label": label,
            "url": _build_link(origin, destination, waypoints),
            "tappe": tappe_range,
        })

        origin = destination
        idx += max_per_link
        link_num += 1

    return links
