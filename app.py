"""DNP Pharma - Pianificatore Giri di Consegna
Streamlit app locale: acquisizione ordini via OCR/manuale, ottimizzazione del giro
(TSP con finestre temporali), generazione link di navigazione Google Maps.
"""
import datetime as dt
import re

import pandas as pd
import streamlit as st

from src import clients_db, config, distance_matrix, geocode, maps_links, ocr_extract, optimizer, pdf_export

st.set_page_config(page_title="DNP Pharma - Pianificatore Giri", layout="wide")
st.logo("assets/logo_dnp_pharma.png")

STOPS_COLUMNS = [
    "Cliente", "Indirizzo", "CAP", "Citta", "Provincia", "Vincolo",
    "Coordinate GPS", "Tempo Scarico (min)",
]

if "stops_df" not in st.session_state:
    st.session_state.stops_df = pd.DataFrame(columns=STOPS_COLUMNS)

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0


def _parse_coordinate_field(value):
    """Interpreta un campo 'lat, lon' incollato da Google Maps (tasto destro sul
    punto -> copia le coordinate). Ritorna (lat, lon) oppure None se non valido."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [p for p in re.split(r"[,;\s]+", value.strip()) if p]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None

if "depot_coord" not in st.session_state:
    st.session_state.depot_coord = None


def _geocode_depot():
    if st.session_state.depot_coord is None:
        lat, lon, _ = geocode.geocode_address(config.DEPOT_ADDRESS)
        st.session_state.depot_coord = (lat, lon)
    return st.session_state.depot_coord


col_logo, col_title = st.columns([1, 4])
with col_logo:
    st.image("assets/logo_dnp_pharma.png")
with col_title:
    st.title("🚚 Pianificatore Giri di Consegna")
    st.caption(f"Deposito: {config.DEPOT_ADDRESS}")

def _time_range_15min(t_min, t_max):
    """Lista di orari (datetime.time) a passi di 15 minuti tra t_min e t_max inclusi."""
    start = t_min.hour * 60 + t_min.minute
    end = t_max.hour * 60 + t_max.minute
    out = []
    m = start
    while m <= end:
        out.append(dt.time(m // 60, m % 60))
        m += 15
    return out


with st.sidebar:
    st.header("Parametri del giro")
    departure = st.slider(
        "Orario di partenza",
        min_value=config.DEPARTURE_MIN,
        max_value=config.DEPARTURE_MAX,
        value=config.DEFAULT_DEPARTURE,
        step=dt.timedelta(minutes=5),
        format="HH:mm",
    )

    _return_options = _time_range_15min(config.RETURN_DEADLINE_MIN, config.RETURN_DEADLINE_MAX)
    return_deadline = st.selectbox(
        "Rientro tassativo entro le (limite massimo)",
        _return_options,
        index=_return_options.index(config.DEFAULT_RETURN_DEADLINE)
        if config.DEFAULT_RETURN_DEADLINE in _return_options else 0,
        format_func=lambda t: t.strftime("%H:%M"),
        help="È sempre solo un limite massimo: se il giro può rientrare prima, l'app non "
             "sceglie mai un percorso più lungo o più lento solo per 'riempire' il tempo.",
    )

    route_mode = st.selectbox(
        "Ottimizza per",
        config.ROUTE_MODE_OPTIONS,
        help=f"'{config.ROUTE_MODE_SHORTEST}' minimizza i km totali. "
             f"'{config.ROUTE_MODE_FASTEST}' minimizza il tempo totale di giro (traffico incluso).",
    )

    service_time_min = st.number_input(
        "Tempo di scarico per tappa (minuti)",
        min_value=config.SERVICE_TIME_OPTIONS_MIN,
        max_value=config.SERVICE_TIME_OPTIONS_MAX,
        value=config.DEFAULT_SERVICE_TIME_MIN,
        step=5,
    )

    st.markdown("---")
    st.caption(
        f"Regole applicate: magazzini chiusi 12:30-14:00 (si può viaggiare ma non scaricare), "
        f"ultimo inizio scarico mattutino entro le "
        f"{config.LAST_MORNING_SCARICO_DEADLINE.strftime('%H:%M')}, "
        f"traffico storico +20% nella fascia 08:00-09:00."
    )

st.subheader("1. Acquisizione ordini (DDT)")
col_upload, col_pulisci1 = st.columns([5, 1])
with col_upload:
    uploaded_files = st.file_uploader(
        "Trascina qui i DDT (PDF o foto, anche più di uno insieme) per estrarre "
        "automaticamente l'indirizzo di consegna",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=f"file_uploader_{st.session_state.uploader_key}",
    )
with col_pulisci1:
    st.write("")
    st.write("")
    if st.button("🦋 FARFALLINA", key="pulisci_sezione1", help="Svuota i file caricati"):
        st.session_state.uploader_key += 1
        st.rerun()

if uploaded_files:
    if st.button("📄 Estrai indirizzi dai file caricati"):
        new_rows = []
        errors = []
        for f in uploaded_files:
            try:
                data = f.read()
                info = ocr_extract.extract_delivery_info(f.name, data)

                if info["trovato"]:
                    # OCR riuscito: impara/aggiorna l'anagrafica per le prossime volte.
                    clients_db.upsert_client(
                        info["cliente"], info["indirizzo"], info["cap"],
                        info["citta"], info["provincia"],
                    )
                else:
                    # OCR incompleto: prova a recuperare l'indirizzo dall'anagrafica clienti.
                    match = clients_db.find_client(info["cliente"] or f.name)
                    if match:
                        info["cliente"] = match["cliente"]
                        info["indirizzo"] = match["indirizzo"]
                        info["cap"] = match["cap"]
                        info["citta"] = match["citta"]
                        info["provincia"] = match["provincia"]
                        errors.append(f"{f.name}: indirizzo non riconosciuto dal DDT, recuperato "
                                       f"dall'anagrafica clienti ({match['cliente']}). Verifica comunque.")
                    else:
                        errors.append(f"{f.name}: indirizzo non riconosciuto automaticamente, "
                                       f"compilalo manualmente nella tabella.")

                new_rows.append({
                    "Cliente": info["cliente"] or f.name,
                    "Indirizzo": info["indirizzo"],
                    "CAP": info["cap"],
                    "Citta": info["citta"],
                    "Provincia": info["provincia"],
                    "Vincolo": config.CONSTRAINT_NONE,
                    "Coordinate GPS": "",
                    "Tempo Scarico (min)": "",
                })
            except Exception as e:
                errors.append(f"{f.name}: errore di estrazione ({e})")

        if new_rows:
            st.session_state.stops_df = pd.concat(
                [st.session_state.stops_df, pd.DataFrame(new_rows)], ignore_index=True
            )
        for e in errors:
            st.warning(e)

_MANUAL_FIELD_KEYS = [
    "manual_cliente", "manual_indirizzo", "manual_cap",
    "manual_citta", "manual_provincia", "manual_vincolo", "manual_coordinate",
]
_NUOVO_CLIENTE_LABEL = "➕ Nuovo cliente..."

# Streamlit vieta di riassegnare st.session_state[key] per un widget DOPO che
# quel widget e' gia' stato istanziato nello stesso run. Per svuotare i campi
# dopo l'invio, usiamo un flag controllato PRIMA che i widget vengano creati.
if st.session_state.get("_reset_manual_form"):
    for _k in _MANUAL_FIELD_KEYS:
        st.session_state[_k] = ""
    st.session_state["manual_client_select"] = _NUOVO_CLIENTE_LABEL
    st.session_state["_reset_manual_form"] = False

for _k in _MANUAL_FIELD_KEYS:
    st.session_state.setdefault(_k, "")


def _autofill_from_saved_client():
    """Callback della tendina 'Cliente salvato': eseguito PRIMA del rerun,
    quindi puo' impostare in sicurezza i campi degli altri widget."""
    selected = st.session_state.get("manual_client_select")
    if not selected or selected == _NUOVO_CLIENTE_LABEL:
        return
    match = clients_db.find_client(selected)
    if match:
        st.session_state.manual_cliente = match["cliente"]
        st.session_state.manual_indirizzo = match["indirizzo"]
        st.session_state.manual_cap = match["cap"]
        st.session_state.manual_citta = match["citta"]
        st.session_state.manual_provincia = match["provincia"]
        st.session_state.manual_vincolo = match.get("vincolo", "")
        st.session_state.manual_coordinate = match.get("coordinate", "")


st.session_state.setdefault("search_results", [])


def _usa_candidato(cand):
    """Applica un candidato di ricerca confermato ai campi del modulo manuale
    (eseguito PRIMA che i widget del modulo vengano istanziati in questo run,
    quindi puo' impostarne il valore in sicurezza)."""
    st.session_state.manual_coordinate = f"{cand['lat']}, {cand['lon']}"
    if cand.get("indirizzo"):
        st.session_state.manual_indirizzo = cand["indirizzo"]
    if cand.get("cap"):
        st.session_state.manual_cap = cand["cap"]
    if cand.get("citta"):
        st.session_state.manual_citta = cand["citta"]
    st.session_state.search_results = []


with st.expander("🔍 Cerca e conferma un indirizzo (consigliato per ospedali/RSA/strutture)"):
    st.caption(
        "Cerca per **nome della struttura** (es. \"Ospedale Gavazzeni Bergamo\", \"RSA Villa Serena\") "
        "oppure per indirizzo. Utile quando il magazzino/punto di consegna non è alla stessa sede "
        "legale della struttura: vedi fino a 5 risultati con l'indirizzo completo e scegli tu quello "
        "giusto, invece di far indovinare all'app — come un navigatore che chiede conferma."
    )
    search_query = st.text_input(
        "Nome struttura o indirizzo da cercare", key="search_query",
        placeholder="Ospedale Gavazzeni Bergamo",
    )
    if st.button("🔍 Cerca"):
        with st.spinner("Ricerca in corso (max qualche secondo)..."):
            st.session_state.search_results = geocode.search_candidates(search_query)
        if not st.session_state.search_results:
            st.warning(
                "Nessun risultato trovato per questa ricerca. Prova con un nome/indirizzo diverso, "
                "oppure inserisci le coordinate manualmente nel modulo qui sotto."
            )

    for i, cand in enumerate(st.session_state.search_results):
        col_a, col_b = st.columns([5, 1])
        with col_a:
            st.write(f"📍 {cand['display_name']}")
        with col_b:
            if st.button("✅ Usa questo", key=f"usa_candidato_{i}"):
                _usa_candidato(cand)
                st.success("Confermato! Completa Cliente e gli altri campi qui sotto, poi 'Aggiungi tappa'.")
                st.rerun()

with st.expander("➕ Aggiungi una tappa manualmente (senza DDT)"):
    saved_names = [c["cliente"] for c in clients_db.list_clients()]
    st.selectbox(
        "Cliente salvato in anagrafica (opzionale)",
        [_NUOVO_CLIENTE_LABEL] + saved_names,
        key="manual_client_select",
        on_change=_autofill_from_saved_client,
        help="Scegli un cliente gia' salvato per compilare da solo indirizzo e priorita', "
             "oppure lascia 'Nuovo cliente' e scrivi i dati a mano.",
    )

    # Racchiuso in un st.form: senza, ogni text_input si aggiorna al volo con un
    # proprio giro di rete, e cliccare "Aggiungi tappa" subito dopo aver scritto
    # nell'ultimo campo (tipicamente Vincolo) puo' partire prima che quel valore
    # sia stato registrato — costringendo a scriverlo due volte. Con st.form
    # tutti i campi vengono letti insieme, in un solo colpo, solo alla conferma.
    with st.form("manual_stop_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            st.text_input("Cliente", key="manual_cliente")
            st.text_input("Indirizzo", key="manual_indirizzo")
            st.text_input("CAP", key="manual_cap")
            st.text_input("Città", key="manual_citta")
        with c2:
            st.text_input("Provincia (sigla)", key="manual_provincia")
            st.text_input(
                "Vincolo (opzionale)",
                key="manual_vincolo",
                placeholder=(
                    f"{config.CONSTRAINT_MORNING} / {config.CONSTRAINT_AFTERNOON} / "
                    "Entro le 10:30 / Tra le 8:30 e le 9:00 / Ultima Consegna / Prima Consegna"
                ),
            )
            st.text_input(
                "Coordinate GPS (opzionale, da Google Maps)",
                key="manual_coordinate",
                placeholder="45.1234, 9.5678",
                help="Su Google Maps, tasto destro sul punto esatto -> clicca sulle coordinate "
                     "per copiarle -> incollale qui. Se compilato, l'app usa questo punto esatto "
                     "invece di geocodificare l'indirizzo, garantendo che combaci con Google Maps.",
            )

        salva_in_anagrafica = st.checkbox("💾 Salva/aggiorna questo cliente in anagrafica", value=True)
        submitted = st.form_submit_button("Aggiungi tappa", type="primary")

    if submitted:
        if not st.session_state.manual_cliente or not st.session_state.manual_indirizzo:
            st.warning("Inserisci almeno Cliente e Indirizzo.")
        elif st.session_state.manual_coordinate and not _parse_coordinate_field(st.session_state.manual_coordinate):
            st.warning("Formato coordinate non valido: usa 'latitudine, longitudine' (es. 45.1234, 9.5678).")
        else:
            new_row = pd.DataFrame([{
                "Cliente": st.session_state.manual_cliente,
                "Indirizzo": st.session_state.manual_indirizzo,
                "CAP": st.session_state.manual_cap,
                "Citta": st.session_state.manual_citta,
                "Provincia": st.session_state.manual_provincia,
                "Vincolo": st.session_state.manual_vincolo,
                "Coordinate GPS": st.session_state.manual_coordinate,
            }])
            st.session_state.stops_df = pd.concat(
                [st.session_state.stops_df, new_row], ignore_index=True
            )
            if salva_in_anagrafica:
                clients_db.upsert_client(
                    st.session_state.manual_cliente, st.session_state.manual_indirizzo,
                    st.session_state.manual_cap, st.session_state.manual_citta,
                    st.session_state.manual_provincia, st.session_state.manual_vincolo,
                    st.session_state.manual_coordinate,
                )
            st.success(f"Tappa '{st.session_state.manual_cliente}' aggiunta alla tabella qui sotto.")
            st.session_state["_reset_manual_form"] = True
            st.rerun()

with st.expander("🗂️ Anagrafica clienti salvati"):
    saved_clients = clients_db.list_clients()
    if saved_clients:
        st.dataframe(pd.DataFrame(saved_clients), use_container_width=True, hide_index=True)
        col_del1, col_del2 = st.columns([3, 1])
        with col_del1:
            nome_da_eliminare = st.text_input("Nome cliente da eliminare dall'anagrafica")
        with col_del2:
            st.write("")
            st.write("")
            if st.button("🗑️ Elimina"):
                if clients_db.delete_client(nome_da_eliminare):
                    st.success(f"Cliente '{nome_da_eliminare}' eliminato dall'anagrafica.")
                    st.rerun()
                else:
                    st.warning("Nessun cliente trovato con questo nome.")
    else:
        st.caption(
            "Nessun cliente salvato ancora. Vengono salvati automaticamente quelli riconosciuti "
            "da un DDT, oppure puoi aggiungerli tu dal modulo qui sopra."
        )

col_sec2_title, col_pulisci2 = st.columns([5, 1])
with col_sec2_title:
    st.subheader("2. Tappe del giro e priorità di consegna (modificabile)")
with col_pulisci2:
    st.write("")
    if st.button("🦋 FARFALLINA", key="pulisci_sezione2", help="Svuota la tabella delle tappe"):
        st.session_state.stops_df = pd.DataFrame(columns=STOPS_COLUMNS)
        st.rerun()

st.caption(
    "Modifica gli indirizzi se necessario e imposta qui le priorità delle tappe, se ce ne sono — "
    "altrimenti lascia il campo Vincolo vuoto e il calcolo del giro sarà completamente automatico. "
    "Il campo **Coordinate GPS** è opzionale: se lo compili (incollando 'lat, lon' copiato da Google "
    "Maps, o confermato con la ricerca qui sotto), l'app usa quel punto esatto invece di "
    "geocodificare l'indirizzo. Il campo **Tempo Scarico (min)** è opzionale: se vuoto usa il valore "
    "impostato in sidebar, altrimenti quello scritto qui vale solo per quella tappa."
)

with st.expander("❓ Guida: come scrivere il campo Vincolo"):
    st.markdown(f"""
| Cosa scrivere | Effetto |
|---|---|
| *(vuoto)* / `{config.CONSTRAINT_NONE}` | Nessun vincolo, l'algoritmo decide tutto in automatico |
| `{config.CONSTRAINT_MORNING}` | Consegna tassativamente prima delle 12:30 |
| `{config.CONSTRAINT_AFTERNOON}` | Consegna dopo le 14:00 (se il furgone arriva prima, attende) |
| `Entro le 10:30` | Orario limite tassativo: la consegna deve avvenire entro quell'ora |
| `Tra le 8:30 e le 9:00` | Finestra oraria: se il furgone arriva prima, attende in loco fino alle 8:30 |
| `Ultima Consegna` | Forza questa tappa come **ultima** del giro (es. per liberare per ultimo un magazzino) |
| `Penultima Consegna` / `Terzultima Consegna` | Forza la posizione a partire dal fondo del giro |
| `Prima Consegna`, `Seconda Consegna`, ... `Ottava Consegna` | Forza la posizione assoluta corrispondente (1ª, 2ª, ... 8ª) nel giro |
| `Consegna 7` / `Posizione 7` | Forza questa tappa esattamente alla **posizione assoluta 7** nel giro |

Le regole fisse di magazzino (chiusura 12:30-14:00, scarico configurabile in sidebar, rientro tassativo)
si applicano sempre, anche sopra a questi vincoli: se un orario richiesto cade nella chiusura pranzo,
l'app lo segnala come non rispettabile invece di ignorarlo.
""")

edited_df = st.data_editor(
    st.session_state.stops_df,
    num_rows="dynamic",
    use_container_width=True,
    key="stops_editor",
)
st.session_state.stops_df = edited_df

st.markdown("---")


def calcola_giro():
    df = st.session_state.stops_df.copy()
    df = df.dropna(subset=["Cliente"]).reset_index(drop=True)

    if df.empty:
        st.error("Aggiungi almeno una tappa prima di calcolare il giro.")
        st.stop()

    with st.spinner("Geocodifica indirizzi..."):
        depot_lat, depot_lon = _geocode_depot()
        if depot_lat is None:
            st.error("Impossibile geocodificare l'indirizzo del deposito. Verifica la connessione internet.")
            st.stop()

        stops = []
        geocode_errors = []
        precisione_bassa = []  # clienti dove l'app NON ha trovato la via esatta
        for _, row in df.iterrows():
            indirizzo = row.get("Indirizzo", "")
            cap = row.get("CAP", "")
            citta = row.get("Citta", "")
            provincia = row.get("Provincia", "")
            full_addr = f"{indirizzo}, {cap} {citta} ({provincia})"

            coord_override = _parse_coordinate_field(row.get("Coordinate GPS", ""))
            if coord_override:
                # Coordinate GPS incollate manualmente (es. da Google Maps): usate cosi'
                # come sono, senza passare dalla geocodifica — garantisce lo stesso punto
                # esatto che si vede su Google Maps.
                lat, lon = coord_override
                display_name = f"{full_addr} [coordinate GPS manuali]"
                precisione = "manuale"
            else:
                lat, lon, display_name, precisione = geocode.geocode_stop(indirizzo, cap, citta, provincia)

            if lat is None:
                geocode_errors.append(row.get("Cliente", "?"))
                continue

            if precisione == "bassa":
                # L'app NON ha trovato la via/il civico: si e' dovuta accontentare del
                # centro citta' (o del solo CAP). Non e' un indirizzo inventato a caso,
                # ma NON e' il punto preciso — va segnalato chiaramente, non nascosto,
                # esattamente come fanno i siti dei corrieri quando non riconoscono
                # un indirizzo.
                precisione_bassa.append(row.get("Cliente", "?"))

            tempo_scarico_raw = row.get("Tempo Scarico (min)", "")
            try:
                tempo_scarico = int(float(tempo_scarico_raw)) if str(tempo_scarico_raw).strip() else None
            except (TypeError, ValueError):
                tempo_scarico = None

            stops.append({
                "cliente": row.get("Cliente", ""),
                "indirizzo_completo": display_name or full_addr,
                "vincolo": config.parse_vincolo(row.get("Vincolo")),
                "lat": lat,
                "lon": lon,
                "tempo_scarico": tempo_scarico,
                "precisione": precisione,
            })

        if geocode_errors:
            st.warning(
                "Indirizzo non geocodificabile per: " + ", ".join(geocode_errors) +
                ". Verifica/correggi l'indirizzo nella tabella."
            )
        if precisione_bassa:
            st.error(
                "⚠️ Via/civico NON trovati con precisione per: " + ", ".join(precisione_bassa) +
                ". L'app ha posizionato questi punti solo indicativamente (centro città/CAP), "
                "NON sull'indirizzo esatto — usa '🔍 Cerca e conferma un indirizzo' qui sopra o "
                "incolla le coordinate GPS da Google Maps prima di fidarti del percorso."
            )
        if not stops:
            st.error("Nessun indirizzo valido da pianificare.")
            st.stop()

    with st.spinner("Calcolo matrice distanze/tempi (rete stradale)..."):
        points = [(depot_lat, depot_lon)] + [(s["lat"], s["lon"]) for s in stops]
        dist_km, dur_min = distance_matrix.build_matrices(points)

    with st.spinner("Ottimizzazione del giro (TSP con finestre temporali)..."):
        start_min = departure.hour * 60 + departure.minute
        deadline_min = return_deadline.hour * 60 + return_deadline.minute
        sim = optimizer.solve(
            stops, dist_km, dur_min, start_min, deadline_min,
            service_time_min=service_time_min, route_mode=route_mode,
        )

    st.session_state.last_sim = sim
    st.session_state.last_stops = stops
    st.session_state.last_depot = (depot_lat, depot_lon)


if st.button("🧭 Calcola giro ottimale", type="primary"):
    calcola_giro()

if "last_sim" in st.session_state and st.session_state.last_sim:
    sim = st.session_state.last_sim
    stops = st.session_state.last_stops
    depot_coord = st.session_state.last_depot

    st.subheader("3. Risultato pianificazione")

    ore_totali = int(sim["total_time_min"] // 60)
    min_totali = int(sim["total_time_min"] % 60)
    if sim["feasible"]:
        st.success(
            f"✅ Giro fattibile ({route_mode}) — rientro previsto alle {sim['arrival_depot_hhmm']} "
            f"({sim['total_km']} km totali, {ore_totali}h{min_totali:02d}m di giro)"
        )
    else:
        st.error("❌ Giro NON fattibile con i vincoli attuali:")
        for v in sim["violations"]:
            st.error(f"• {v['message']}")

    schedule_df = pd.DataFrame(sim["schedule"]).drop(columns=["_arrival_min"])
    st.dataframe(schedule_df, use_container_width=True, hide_index=True)

    st.info(
        "Se un indirizzo geocodificato non è corretto, correggilo nella tabella "
        "della sezione 2 qui sopra, poi premi il pulsante qui sotto per ricalcolare "
        "il percorso con le modifiche (senza dover ricliccare 'Calcola giro ottimale')."
    )
    if st.button("🔁 Ricalcola con le modifiche"):
        calcola_giro()
        st.rerun()

    map_points = pd.DataFrame(
        [{"lat": depot_coord[0], "lon": depot_coord[1]}] +
        [{"lat": stops[i]["lat"], "lon": stops[i]["lon"]} for i in sim["order"]]
    )
    st.map(map_points, size=20)

    st.subheader("4. Link navigazione (Android Auto / CarPlay)")
    ordered_coords = [(stops[i]["lat"], stops[i]["lon"]) for i in sim["order"]]
    links = maps_links.build_navigation_links(depot_coord, ordered_coords)

    if len(links) > 1:
        st.info(
            f"Il giro ha {len(ordered_coords)} tappe: Google Maps supporta al massimo "
            f"{config.MAX_STOPS_PER_MAPS_LINK} tappe per link di navigazione, quindi il "
            f"percorso è stato diviso in {len(links)} link consecutivi."
        )

    for link in links:
        st.markdown(f"**{link['label']}**")
        st.link_button(f"📍 Apri in Google Maps — {link['label']}", link["url"])
        st.code(link["url"], language=None)

    st.subheader("5. Stampa ordine di consegna")
    pdf_bytes = pdf_export.generate_delivery_order_pdf(
        sim, stops, config.DEPOT_ADDRESS, departure.strftime("%H:%M")
    )
    st.download_button(
        "📄 Scarica PDF Ordine Consegne",
        data=pdf_bytes,
        file_name="ordine_consegne.pdf",
        mime="application/pdf",
        type="primary",
    )
