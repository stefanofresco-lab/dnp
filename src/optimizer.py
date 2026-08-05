"""Ottimizzazione del giro di consegne: TSP con finestre temporali (TSP-TW),
traffico storico simulato, chiusura pranzo dei magazzini e vincolo tassativo
di rientro. Usa OR-Tools per una sequenza di partenza (TSP puro sulla distanza)
poi rifinisce con ricerca locale (2-opt / or-opt) valutando ESATTAMENTE tutti i
vincoli orari tramite simulazione temporale — necessario perche' il tempo di
percorrenza dipende dall'ora del giorno (traffico) e OR-Tools non modella bene
finestre temporali time-dependent insieme a soste di chiusura pranzo.

L'orario di rientro e' SEMPRE trattato come un limite massimo (vincolo di
fattibilita'), mai come un obiettivo: l'algoritmo cerca sempre il percorso
migliore secondo la modalita' scelta (piu' breve in km o piu' veloce in tempo)
indipendentemente da quanto margine lascia il rientro scelto — non "spreca"
mai km o minuti per riempire il tempo disponibile.
"""
from datetime import time

from . import config


def _t2m(t: time) -> float:
    return t.hour * 60 + t.minute


LUNCH_START_MIN = _t2m(config.LUNCH_START)
LUNCH_END_MIN = _t2m(config.LUNCH_END)
LAST_MORNING_SCARICO_DEADLINE_MIN = _t2m(config.LAST_MORNING_SCARICO_DEADLINE)
TRAFFIC_BANDS_MIN = [(_t2m(a), _t2m(b), f) for a, b, f in config.TRAFFIC_BANDS]


def min_to_hhmm(m: float) -> str:
    m = int(round(m))
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def traffic_multiplier(clock_min: float) -> float:
    for start, end, factor in TRAFFIC_BANDS_MIN:
        if start <= clock_min < end:
            return factor
    return 1.0


def simulate_route(order, stops, dist_km, dur_min, start_min, return_deadline_min,
                    service_time_min=None):
    """order: lista di indici (0-based) in `stops` nell'ordine di visita.
    service_time_min: minuti di scarico di DEFAULT per le tappe che non hanno
    un proprio "tempo_scarico" impostato (una tappa puo' sovrascrivere il
    default globale mettendo stop["tempo_scarico"] = minuti). Ritorna un
    dizionario con schedule dettagliato, km totali, tempo totale, fattibilita'
    e violazioni.
    """
    default_service_time = (
        config.DEFAULT_SERVICE_TIME_MIN if service_time_min is None else service_time_min
    )

    current_time = start_min
    current_idx = 0  # 0 = deposito nella matrice
    total_km = 0.0
    schedule = []
    violations = []  # ciascuna: {"message": str, "overage_min": float}
    morning_stops = []  # (position, service_start_min, stop_index) per le tappe servite prima di pranzo

    for pos, stop_i in enumerate(order):
        target_idx = stop_i + 1
        stop = stops[stop_i]
        service_time_min = stop.get("tempo_scarico") or default_service_time

        leg_km = dist_km[current_idx][target_idx]
        leg_dur = dur_min[current_idx][target_idx] * traffic_multiplier(current_time)
        # Arrotonda l'arrivo al minuto SUBITO, prima di qualunque confronto: cosi'
        # l'orario mostrato (es. "12:10") e' sempre esattamente quello usato nei
        # controlli di chiusura pranzo/vincoli, senza scarti di pochi secondi
        # nascosti dall'arrotondamento del solo display.
        arrival = round(current_time + leg_dur)
        total_km += leg_km

        vincolo = stop.get("vincolo") or {"tipo": "nessuno", "orario_min": None, "orario_max": None}
        tipo = vincolo.get("tipo", "nessuno")

        if arrival <= LAST_MORNING_SCARICO_DEADLINE_MIN:
            # Se si arriva entro le 12:15 lo scarico puo' iniziare subito, anche
            # se la sua durata lo fa finire oltre le 12:30: il limite e' sempre
            # e solo sull'ORARIO DI INIZIO, indipendentemente da quanto dura lo
            # scarico di quella tappa.
            service_start = arrival
        elif arrival >= LUNCH_END_MIN:
            service_start = arrival
        else:
            service_start = LUNCH_END_MIN  # attesa: magazzino chiuso per pranzo

        if tipo == "pomeriggio" and service_start < LUNCH_END_MIN:
            # Vincolo "Solo Pomeriggio": se si arriva in anticipo il furgone attende
            # in loco fino alle 14:00 (nessuna violazione, solo tempo perso).
            service_start = LUNCH_END_MIN

        if tipo == "finestra" and vincolo.get("orario_min") is not None and service_start < vincolo["orario_min"]:
            # Finestra oraria (es. "tra le 8:30 e le 9"): se si arriva prima
            # dell'inizio finestra il furgone attende in loco, lo scarico non
            # parte comunque prima dell'orario minimo richiesto.
            service_start = vincolo["orario_min"]

        if LUNCH_START_MIN <= service_start < LUNCH_END_MIN:
            # La chiusura pranzo del magazzino prevale SEMPRE su qualunque
            # vincolo: anche se un orario richiesto dal cliente cade nella
            # fascia 12:30-14:00, lo scarico non puo' comunque iniziare prima
            # delle 14:00 (l'eventuale vincolo orario verra' segnalato come
            # violato piu' sotto, se non piu' rispettabile).
            service_start = LUNCH_END_MIN

        departure = service_start + service_time_min

        if tipo == "mattina" and service_start >= LUNCH_START_MIN:
            # Arrivo troppo tardi per rispettare "Solo Mattina": non recuperabile attendendo.
            violations.append({
                "message": f"{stop['cliente']}: vincolo 'Solo Mattina' violato, "
                           f"consegna prevista alle {min_to_hhmm(service_start)}",
                "overage_min": service_start - LUNCH_START_MIN + 30,
            })

        if tipo == "deadline" and service_start > vincolo["orario_min"]:
            # Priorita' con orario tassativo (es. "Entro le 10:30"): non recuperabile attendendo.
            violations.append({
                "message": f"{stop['cliente']}: consegna richiesta entro le "
                           f"{min_to_hhmm(vincolo['orario_min'])}, prevista invece alle "
                           f"{min_to_hhmm(service_start)}",
                "overage_min": service_start - vincolo["orario_min"],
            })

        if tipo == "finestra" and vincolo.get("orario_max") is not None and service_start > vincolo["orario_max"]:
            # Arrivo oltre la fine della finestra: non recuperabile attendendo.
            violations.append({
                "message": f"{stop['cliente']}: consegna richiesta tra le "
                           f"{min_to_hhmm(vincolo['orario_min'])} e le {min_to_hhmm(vincolo['orario_max'])}, "
                           f"prevista invece alle {min_to_hhmm(service_start)}",
                "overage_min": service_start - vincolo["orario_max"],
            })

        if service_start < LUNCH_START_MIN:
            morning_stops.append((pos, service_start, stop_i))

        schedule.append({
            "posizione": pos + 1,
            "cliente": stop["cliente"],
            "indirizzo": stop.get("indirizzo_completo", ""),
            "vincolo": config.describe_vincolo(vincolo),
            "arrivo": min_to_hhmm(arrival),
            "inizio_scarico": min_to_hhmm(service_start),
            "fine_scarico": min_to_hhmm(departure),
            "km_tappa": round(leg_km, 1),
            "_arrival_min": arrival,
        })

        current_time = departure
        current_idx = target_idx

    leg_km = dist_km[current_idx][0]
    leg_dur = dur_min[current_idx][0] * traffic_multiplier(current_time)
    arrival_depot = round(current_time + leg_dur)
    total_km += leg_km

    if arrival_depot > return_deadline_min:
        violations.append({
            "message": f"Rientro previsto alle {min_to_hhmm(arrival_depot)}, oltre il limite "
                       f"tassativo delle {min_to_hhmm(return_deadline_min)}",
            "overage_min": arrival_depot - return_deadline_min,
        })

    if morning_stops:
        # L'ultimo scarico mattutino (per ordine di visita) deve iniziare entro
        # LAST_MORNING_SCARICO_DEADLINE_MIN, non solo l'arrivo: e' l'inizio
        # scarico che deve avvenire in tempo per chiudere prima delle 12:30.
        _, last_service_start, last_stop_i = morning_stops[-1]
        if last_service_start > LAST_MORNING_SCARICO_DEADLINE_MIN:
            violations.append({
                "message": f"Ultima consegna mattutina ({stops[last_stop_i]['cliente']}): scarico "
                           f"previsto alle {min_to_hhmm(last_service_start)}, oltre le "
                           f"{min_to_hhmm(LAST_MORNING_SCARICO_DEADLINE_MIN)} richieste per chiudere "
                           f"prima della pausa pranzo",
                "overage_min": last_service_start - LAST_MORNING_SCARICO_DEADLINE_MIN,
            })

    return {
        "order": order,
        "schedule": schedule,
        "total_km": round(total_km, 1),
        "total_time_min": round(arrival_depot - start_min, 1),
        "arrival_depot": arrival_depot,
        "arrival_depot_hhmm": min_to_hhmm(arrival_depot),
        "feasible": len(violations) == 0,
        "violations": violations,
    }


def _score(sim, route_mode=None):
    overage = sum(v["overage_min"] for v in sim["violations"])
    metric = sim["total_time_min"] if route_mode == config.ROUTE_MODE_FASTEST else sim["total_km"]
    return (0 if sim["feasible"] else 1, overage, metric)


def _nearest_neighbor_order(n, dist_km):
    unvisited = set(range(n))
    order = []
    current = 0  # deposito
    while unvisited:
        nxt = min(unvisited, key=lambda j: dist_km[current][j + 1])
        order.append(nxt)
        unvisited.remove(nxt)
        current = nxt + 1
    return order


def _nearest_neighbor_order_time(n, dur_min):
    """Come _nearest_neighbor_order ma sceglie il prossimo per tempo di
    percorrenza (usato come seed quando la modalita' e' 'Percorso Più Veloce')."""
    unvisited = set(range(n))
    order = []
    current = 0
    while unvisited:
        nxt = min(unvisited, key=lambda j: dur_min[current][j + 1])
        order.append(nxt)
        unvisited.remove(nxt)
        current = nxt + 1
    return order


def _ortools_worker(n, matrix, result_queue):
    """Eseguito in un processo separato: il risolutore nativo di OR-Tools puo'
    incorrere in crash a basso livello (segfault) su alcune combinazioni di
    piattaforma/versione. Isolandolo in un sotto-processo, un eventuale crash
    termina solo questo processo senza mai coinvolgere il server Streamlit.
    `matrix` puo' essere la matrice km o la matrice minuti, a seconda della
    modalita' di ottimizzazione richiesta."""
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError:
        result_queue.put(None)
        return

    size = n + 1
    manager = pywrapcp.RoutingIndexManager(size, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def cost_callback(from_index, to_index):
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return int(matrix[i][j] * 1000)

    transit_idx = routing.RegisterTransitCallback(cost_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(3)

    solution = routing.SolveWithParameters(search_params)
    if solution is None:
        result_queue.put(None)
        return

    order = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != 0:
            order.append(node - 1)
        index = solution.Value(routing.NextVar(index))
    result_queue.put(order)


def _ortools_tsp_order(n, matrix, timeout_sec=6):
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(target=_ortools_worker, args=(n, matrix, result_queue))
    process.start()
    process.join(timeout_sec)

    if process.is_alive():
        process.terminate()
        process.join()
        return None

    if process.exitcode != 0:
        # Il sotto-processo e' crashato (es. segfault nativo di OR-Tools):
        # si prosegue con gli altri ordini candidati (nearest-neighbor, euristico).
        return None

    try:
        return result_queue.get_nowait()
    except Exception:
        return None


def resolve_position_pins(stops):
    """Risolve i vincoli di posizione forzata (Ultima/Penultima/Consegna N/
    ordinali italiani) in posizioni assolute 1-based, rispetto al numero
    totale di tappe. In caso di conflitto (due tappe che rivendicano la
    stessa posizione) vince la prima trovata; le altre restano libere e
    vengono segnalate."""
    n = len(stops)
    claimed = {}
    pins = {}
    conflicts = []
    for i, stop in enumerate(stops):
        v = stop.get("vincolo") or {}
        if v.get("tipo") != "posizione":
            continue
        if v.get("posizione_assoluta") is not None:
            target = max(1, min(n, v["posizione_assoluta"]))
        elif v.get("posizione_da_fine") is not None:
            target = max(1, n - v["posizione_da_fine"])
        else:
            continue
        if target in claimed:
            conflicts.append(
                f"{stop['cliente']}: posizione richiesta ({config.describe_vincolo(v)}) "
                f"gia' occupata da {stops[claimed[target]]['cliente']}, vincolo ignorato per questa tappa"
            )
            continue
        claimed[target] = i
        pins[i] = target
    return pins, conflicts


def _apply_pins_to_order(order, pins):
    """Reinserisce le tappe con posizione fissata nei loro slot, mantenendo
    l'ordine relativo delle tappe libere cosi' come proposto in `order`."""
    if not pins:
        return order
    n = len(order)
    free_seq = [i for i in order if i not in pins]
    result = [None] * n
    for stop_i, pos in pins.items():
        result[pos - 1] = stop_i
    it = iter(free_seq)
    for p in range(n):
        if result[p] is None:
            result[p] = next(it)
    return result


def _respects_pins(order, pins):
    return all(order[pos - 1] == stop_i for stop_i, pos in pins.items())


def _local_search(order, stops, dist_km, dur_min, start_min, return_deadline_min,
                   pins=None, service_time_min=None, route_mode=None, max_iters=2000):
    pins = pins or {}
    best_order = _apply_pins_to_order(order[:], pins)
    best_sim = simulate_route(
        best_order, stops, dist_km, dur_min, start_min, return_deadline_min, service_time_min
    )
    best_score = _score(best_sim, route_mode)

    n = len(order)
    improved = True
    iters = 0
    while improved and iters < max_iters:
        improved = False
        # 2-opt
        for i in range(n - 1):
            for j in range(i + 1, n):
                iters += 1
                if iters >= max_iters:
                    break
                candidate = best_order[:i] + best_order[i:j + 1][::-1] + best_order[j + 1:]
                if not _respects_pins(candidate, pins):
                    continue
                sim = simulate_route(
                    candidate, stops, dist_km, dur_min, start_min, return_deadline_min, service_time_min
                )
                score = _score(sim, route_mode)
                if score < best_score:
                    best_order, best_sim, best_score = candidate, sim, score
                    improved = True
        # or-opt: relocate singolo stop in un'altra posizione
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                iters += 1
                if iters >= max_iters:
                    break
                candidate = best_order[:]
                stop_val = candidate.pop(i)
                candidate.insert(j, stop_val)
                if not _respects_pins(candidate, pins):
                    continue
                sim = simulate_route(
                    candidate, stops, dist_km, dur_min, start_min, return_deadline_min, service_time_min
                )
                score = _score(sim, route_mode)
                if score < best_score:
                    best_order, best_sim, best_score = candidate, sim, score
                    improved = True

    return best_order, best_sim


def solve(stops, dist_km, dur_min, start_min, return_deadline_min,
          service_time_min=None, route_mode=None):
    """stops: lista di dict con almeno 'cliente', 'indirizzo_completo', 'vincolo', 'lat', 'lon'.
    dist_km / dur_min: matrici (n+1)x(n+1), indice 0 = deposito.
    service_time_min: minuti di scarico fissi per tappa (default da config).
    route_mode: config.ROUTE_MODE_SHORTEST (minimizza km, default) oppure
    config.ROUTE_MODE_FASTEST (minimizza il tempo totale di giro). L'orario di
    rientro resta SEMPRE solo un limite massimo di fattibilita', mai un
    obiettivo: a parita' di vincoli il risultato non cambia in base a quanto
    margine lascia il rientro scelto.
    Ritorna il miglior sim trovato (vedi simulate_route).
    """
    n = len(stops)
    if n == 0:
        return None

    optimize_matrix = dur_min if route_mode == config.ROUTE_MODE_FASTEST else dist_km

    candidate_orders = []

    ortools_order = _ortools_tsp_order(n, optimize_matrix)
    if ortools_order:
        candidate_orders.append(ortools_order)

    if route_mode == config.ROUTE_MODE_FASTEST:
        candidate_orders.append(_nearest_neighbor_order_time(n, dur_min))
    else:
        candidate_orders.append(_nearest_neighbor_order(n, dist_km))

    # seed euristico: deadline/mattina prima (le deadline in ordine di orario),
    # nessun vincolo in mezzo (per distanza/tempo dal deposito), pomeriggio dopo.
    def sort_key(i):
        v = stops[i].get("vincolo") or {"tipo": "nessuno", "orario_min": None, "orario_max": None}
        tipo = v.get("tipo", "nessuno")
        base = dur_min[0][i + 1] if route_mode == config.ROUTE_MODE_FASTEST else dist_km[0][i + 1]
        if tipo in ("deadline", "finestra"):
            return (0, v.get("orario_min") or 0)
        if tipo == "mattina":
            return (0, LUNCH_START_MIN)
        if tipo == "nessuno":
            return (1, base)
        return (2, base)  # pomeriggio

    candidate_orders.append(sorted(range(n), key=sort_key))

    pins, conflicts = resolve_position_pins(stops)

    best_sim = None
    for init_order in candidate_orders:
        order, sim = _local_search(
            init_order, stops, dist_km, dur_min, start_min, return_deadline_min,
            pins=pins, service_time_min=service_time_min, route_mode=route_mode,
        )
        if best_sim is None or _score(sim, route_mode) < _score(best_sim, route_mode):
            best_sim = sim

    if conflicts:
        best_sim["violations"] = best_sim["violations"] + [
            {"message": c, "overage_min": 0} for c in conflicts
        ]
        best_sim["feasible"] = False

    return best_sim
