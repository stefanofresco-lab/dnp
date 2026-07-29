"""DNP Pharma - Pianificatore Giri di Consegna
Streamlit app locale: acquisizione ordini via OCR/manuale, ottimizzazione del giro
(TSP con finestre temporali), generazione link di navigazione Google Maps.
"""
import datetime as dt

import pandas as pd
import streamlit as st

from src import clients_db, config, distance_matrix, geocode, maps_links, ocr_extract, optimizer

st.set_page_config(page_title="DNP Pharma - Pianificatore Giri", layout="wide")

if "stops_df" not in st.session_state:
    st.session_state.stops_df = pd.DataFrame(
        columns=["Cliente", "Indirizzo", "CAP", "Citta", "Provincia", "Vincolo"]
    )

if "depot_coord" not in st.session_state:
    st.session_state.depot_coord = None


def _geocode_depot():
    if st.session_state.depot_coord is None:
        lat, lon, _ = geocode.geocode_address(config.DEPOT_ADDRESS)
        st.session_state.depot_coord = (lat, lon)
    return st.session_state.depot_coord


st.title("🚚 DNP Pharma — Pianificatore Giri di Consegna")
st.caption(f"Deposito: {config.DEPOT_ADDRESS}")

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
    return_deadline = st.time_input(
        "Rientro tassativo entro le", value=config.DEFAULT_RETURN_DEADLINE
    )
    st.markdown("---")
    st.caption(
        "Regole applicate: scarico 20 min/tappa, magazzini chiusi 12:30-14:00 "
        "(si può viaggiare ma non scaricare), ultima consegna mattutina entro le 12:10, "
        "traffico storico +20% nelle fasce 08:00-09:00 e 13:00-14:00."
    )

st.subheader("1. Acquisizione ordini (DDT)")
uploaded_files = st.file_uploader(
    "Trascina qui i DDT (PDF o foto) per estrarre automaticamente l'indirizzo di consegna",
    type=["pdf", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

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
    "manual_citta", "manual_provincia", "manual_vincolo",
]
for _k in _MANUAL_FIELD_KEYS:
    st.session_state.setdefault(_k, "")

with st.expander("➕ Aggiungi una tappa manualmente (senza DDT)"):
    c1, c2 = st.columns(2)
    with c1:
        st.text_input("Cliente", key="manual_cliente")
        if st.button("🔍 Cerca in anagrafica clienti"):
            match = clients_db.find_client(st.session_state.manual_cliente)
            if match:
                st.session_state.manual_indirizzo = match["indirizzo"]
                st.session_state.manual_cap = match["cap"]
                st.session_state.manual_citta = match["citta"]
                st.session_state.manual_provincia = match["provincia"]
                st.session_state.manual_vincolo = match.get("vincolo", "")
                st.success(f"Trovato in anagrafica: {match['cliente']}")
            else:
                st.warning("Nessun cliente salvato con questo nome.")
        st.text_input("Indirizzo", key="manual_indirizzo")
        st.text_input("CAP", key="manual_cap")
    with c2:
        st.text_input("Città", key="manual_citta")
        st.text_input("Provincia (sigla)", key="manual_provincia")
        st.text_input(
            "Vincolo (opzionale)",
            key="manual_vincolo",
            placeholder=(
                f"{config.CONSTRAINT_MORNING} / {config.CONSTRAINT_AFTERNOON} / "
                "Entro le 10:30 / Tra le 8:30 e le 9:00 / Ultima Consegna"
            ),
        )

    salva_in_anagrafica = st.checkbox("💾 Salva/aggiorna questo cliente in anagrafica", value=True)

    if st.button("Aggiungi tappa", type="primary"):
        if not st.session_state.manual_cliente or not st.session_state.manual_indirizzo:
            st.warning("Inserisci almeno Cliente e Indirizzo.")
        else:
            new_row = pd.DataFrame([{
                "Cliente": st.session_state.manual_cliente,
                "Indirizzo": st.session_state.manual_indirizzo,
                "CAP": st.session_state.manual_cap,
                "Citta": st.session_state.manual_citta,
                "Provincia": st.session_state.manual_provincia,
                "Vincolo": st.session_state.manual_vincolo,
            }])
            st.session_state.stops_df = pd.concat(
                [st.session_state.stops_df, new_row], ignore_index=True
            )
            if salva_in_anagrafica:
                clients_db.upsert_client(
                    st.session_state.manual_cliente, st.session_state.manual_indirizzo,
                    st.session_state.manual_cap, st.session_state.manual_citta,
                    st.session_state.manual_provincia, st.session_state.manual_vincolo,
                )
            st.success(f"Tappa '{st.session_state.manual_cliente}' aggiunta alla tabella qui sotto.")
            for _k in _MANUAL_FIELD_KEYS:
                st.session_state[_k] = ""
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

st.subheader("2. Tappe del giro e priorità di consegna (modificabile)")
st.caption(
    "Modifica gli indirizzi se necessario e imposta qui le priorità delle tappe, se ce ne sono — "
    "altrimenti lascia il campo Vincolo vuoto e il calcolo del giro sarà completamente automatico."
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
| `Consegna 7` / `Posizione 7` | Forza questa tappa esattamente alla **posizione assoluta 7** nel giro |

Le regole fisse di magazzino (chiusura 12:30-14:00, scarico 20 min/tappa, rientro tassativo) si applicano
sempre, anche sopra a questi vincoli: se un orario richiesto cade nella chiusura pranzo, l'app lo segnala
come non rispettabile invece di ignorarlo.
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
        for _, row in df.iterrows():
            full_addr = f"{row.get('Indirizzo', '')}, {row.get('CAP', '')} {row.get('Citta', '')} ({row.get('Provincia', '')})"
            lat, lon, display_name = geocode.geocode_address(full_addr)
            if lat is None:
                geocode_errors.append(row.get("Cliente", "?"))
                continue
            stops.append({
                "cliente": row.get("Cliente", ""),
                "indirizzo_completo": display_name or full_addr,
                "vincolo": config.parse_vincolo(row.get("Vincolo")),
                "lat": lat,
                "lon": lon,
            })

        if geocode_errors:
            st.warning(
                "Indirizzo non geocodificabile per: " + ", ".join(geocode_errors) +
                ". Verifica/correggi l'indirizzo nella tabella."
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
        sim = optimizer.solve(stops, dist_km, dur_min, start_min, deadline_min)

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

    if sim["feasible"]:
        st.success(
            f"✅ Giro fattibile — rientro previsto alle {sim['arrival_depot_hhmm']} "
            f"({sim['total_km']} km totali)"
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
