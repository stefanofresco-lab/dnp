# DNP Pharma — Pianificatore Giri di Consegna

App web locale (Streamlit) per pianificare in modo ottimale i giri di consegna
giornalieri, senza usare API a pagamento di Google Maps.

## Cosa fa

1. **Acquisizione ordini**: trascina DDT in PDF o foto — l'indirizzo di consegna
   viene estratto automaticamente via OCR (Tesseract + pdfplumber). Puoi correggere
   i dati o aggiungere tappe manualmente in una tabella modificabile.
2. **Vincoli/priorità di consegna**: ogni tappa può avere "Solo Mattina", "Solo
   Pomeriggio", un orario tassativo ("Entro le 10:30"), una finestra oraria
   ("Tra le 8:30 e le 9:00"), oppure una posizione forzata nel giro ("Ultima
   Consegna", "Penultima Consegna", "Consegna 7") — utile per liberare il
   furgone/magazzino in un ordine preciso. Guida completa nell'app (expander
   "❓ Guida: come scrivere il campo Vincolo"). Vengono sempre applicate anche le
   regole fisse: 20 min di scarico per tappa, chiusura magazzini 12:30–14:00 (si
   viaggia ma non si scarica — prevale su qualunque vincolo), ultima consegna
   mattutina entro le 12:10, rientro tassativo in deposito.
3. **Ottimizzazione**: calcola la sequenza di visita che minimizza i km, usando
   OR-Tools per una sequenza di partenza e una ricerca locale (2-opt/or-opt) che
   valuta esattamente tutti i vincoli orari e il traffico storico simulato
   (+20% nelle fasce 08:00-09:00 e 13:00-14:00). Se il giro non è fattibile entro
   il rientro richiesto, mostra un avviso rosso con il motivo esatto.
4. **Link di navigazione**: genera link Google Maps pronti per Android Auto /
   CarPlay, con split automatico ogni 8 tappe (limite pratico dei link di
   navigazione Google Maps).
5. **Anagrafica clienti**: ogni cliente riconosciuto con successo da un DDT
   viene salvato automaticamente in un'anagrafica locale (`data/clienti.json`).
   La volta successiva, se un DDT non viene riconosciuto (o per l'inserimento
   manuale), l'indirizzo viene recuperato automaticamente dal nome cliente.

Tutti i servizi usati sono gratuiti: geocodifica via **Nominatim/OpenStreetMap**,
distanze/tempi reali su strada via **OSRM** (server pubblico, con fallback a stima
Haversine se non raggiungibile), routing via **OR-Tools** (Google, open-source).

## Requisiti di sistema

- Python 3.10+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (con il language
  pack italiano) e **Poppler** (per convertire PDF scansionati in immagini)

Su macOS, con [Homebrew](https://brew.sh):
```bash
brew install tesseract tesseract-lang poppler
```

Su Windows: installa [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
e [Poppler per Windows](https://github.com/oschwartz10612/poppler-windows),
poi aggiungi entrambi al PATH di sistema.

Su Linux (Debian/Ubuntu):
```bash
sudo apt install tesseract-ocr tesseract-ocr-ita poppler-utils
```

## Installazione

```bash
cd dnp_delivery_planner
python3 -m venv .venv
source .venv/bin/activate        # su Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Avvio

```bash
source .venv/bin/activate
streamlit run app.py
```

Si aprirà automaticamente il browser su `http://localhost:8501`.

## Installare l'app su un altro PC

Copia l'intera cartella `dnp_delivery_planner` (o clonala da un repository) sul
nuovo computer, poi ripeti i passaggi "Requisiti di sistema" e "Installazione"
qui sopra. Non serve altro: non ci sono chiavi API da configurare.

## Usarla da iPhone (o da qualsiasi telefono)

Streamlit è un'app web: non serve un'app nativa iOS. Basta che il PC che la
esegue e il telefono siano sulla **stessa rete Wi-Fi**:

1. Sul PC dove giri `streamlit run app.py`, avvialo con:
   ```bash
   streamlit run app.py --server.address 0.0.0.0
   ```
2. Trova l'indirizzo IP locale del PC (es. `192.168.1.23`):
   - macOS: `ipconfig getifaddr en0`
   - Windows: `ipconfig` (cerca "Indirizzo IPv4")
3. Da Safari sull'iPhone apri `http://192.168.1.23:8501`.
4. (Opzionale) Da Safari puoi aggiungere la pagina alla schermata Home
   ("Condividi" → "Aggiungi a Home") per avere un'icona come una vera app.

I link di navigazione generati dall'app aprono nativamente **Google Maps**
sia su Android che su iPhone (se l'app Google Maps è installata) e da lì
si proiettano automaticamente su Android Auto / CarPlay una volta avviata
la navigazione mentre il telefono è collegato all'auto.

### Pubblicarla online (link pubblico, gratis) — Streamlit Community Cloud

Il progetto è già pronto per questo (file `packages.txt` incluso per le
dipendenze di sistema). Passaggi:

1. **Crea un repository GitHub** (anche privato) e caricaci questa cartella:
   ```bash
   cd dnp_delivery_planner
   git init
   git add .
   git commit -m "Pianificatore giri DNP Pharma"
   git branch -M main
   git remote add origin https://github.com/<tuo-utente>/<nome-repo>.git
   git push -u origin main
   ```
   (Crea prima il repository vuoto su github.com, poi usa l'URL che ti dà.)
2. Vai su **[share.streamlit.io](https://share.streamlit.io)** e accedi con GitHub.
3. Clicca "New app", seleziona il repository, il branch `main` e come file
   principale `app.py`.
4. Deploy. Dopo un paio di minuti ottieni un link pubblico tipo
   `https://<nome-a-caso>.streamlit.app` — nessuna password, chiunque abbia il
   link può usarlo, come richiesto.

**Limite importante da sapere**: sul piano gratuito lo spazio disco non è
garantito persistente nel tempo — ad ogni nuovo deploy (es. dopo un push di
modifiche) l'anagrafica clienti (`data/clienti.json`) e la cache indirizzi
(`data/geocode_cache.json`) ripartono vuote. Per un uso quotidiano va benissimo
lo stesso (l'anagrafica si ripopola da sola man mano che carichi DDT), ma non
aspettarti che i clienti salvati restino per sempre su questo piano gratuito.

Se in futuro preferisci un dominio tuo (es. `giri.dnppharma.it`) invece del
link `*.streamlit.app`, serve un hosting che supporti domini personalizzati
(es. Render o Railway, entrambi con piani gratuiti/economici) più un record
DNS CNAME nelle impostazioni del tuo dominio — chiedimelo quando vuoi
procedere e ti guido passo passo.

## Struttura del progetto

```
dnp_delivery_planner/
├── app.py                  # interfaccia Streamlit
├── requirements.txt
├── src/
│   ├── config.py           # costanti: deposito, orari, vincoli, traffico
│   ├── ocr_extract.py      # estrazione testo (OCR/PDF) + parsing indirizzo DDT
│   ├── geocode.py          # geocodifica indirizzi (Nominatim, con cache)
│   ├── distance_matrix.py  # matrice km/minuti reali su strada (OSRM)
│   ├── optimizer.py        # TSP con finestre temporali + traffico + ricerca locale
│   ├── maps_links.py       # generazione link Google Maps con split a 8 tappe
│   └── clients_db.py       # anagrafica clienti persistente (salvataggio/ricerca)
└── data/
    ├── geocode_cache.json  # cache indirizzi già geocodificati (creata al primo uso)
    └── clienti.json        # anagrafica clienti salvati (creata al primo uso)
```

## Note e limiti noti

- La geocodifica gratuita (Nominatim) ha un rate limit di ~1 richiesta/secondo:
  la prima elaborazione di molte tappe nuove richiede qualche secondo in più;
  gli indirizzi già geocodificati vengono riusati dalla cache locale.
- Se OSRM pubblico non è raggiungibile, l'app usa una stima di distanza/tempo
  basata sulla distanza in linea d'aria corretta per la tortuosità stradale:
  meno precisa, ma il giro resta calcolabile offline.
- L'estrazione OCR è tarata sul formato DDT tipico italiano (blocco
  "Destinatario/Destinazione", CAP + Città + Provincia). Se un fornitore usa
  un layout molto diverso, correggi semplicemente i campi nella tabella prima
  di calcolare il giro.
