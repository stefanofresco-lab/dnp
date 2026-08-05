"""Anagrafica clienti persistente: salva indirizzi gia' visti/inseriti cosi' che
le prossime volte vengano riconosciuti automaticamente dal nome cliente, senza
dover ridigitare o ri-estrarre l'indirizzo dal DDT.

Su Streamlit Community Cloud lo spazio disco del container NON e' permanente:
ad ogni riavvio dell'app un file salvato solo localmente andrebbe perso. Per
questo, se sono configurati i secrets "github_token" e "github_repo", questo
modulo legge/scrive l'anagrafica direttamente nel file data/clienti.json del
repository GitHub (tramite le API di GitHub), cosi' i dati sopravvivono a
qualunque riavvio. In locale (senza quei secrets) usa semplicemente il file
sul disco, com'era prima."""
import base64
import json
import os

CLIENTS_DB_PATH = "data/clienti.json"


def _get_github_config():
    """Ritorna (token, repo) se configurati nei secrets di Streamlit, altrimenti
    (None, None) — nel qual caso si usa il file locale."""
    try:
        import streamlit as st
        token = st.secrets.get("github_token")
        repo = st.secrets.get("github_repo")
        if token and repo:
            return token, repo
    except Exception:
        pass
    return None, None


def _github_headers(token: str) -> dict:
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}


def _github_load(token: str, repo: str):
    """Ritorna (dizionario_clienti, sha_file_corrente_o_None)."""
    import requests
    url = f"https://api.github.com/repos/{repo}/contents/{CLIENTS_DB_PATH}"
    resp = requests.get(url, headers=_github_headers(token), timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return (json.loads(content) if content.strip() else {}), data["sha"]
    return {}, None


def _github_save(token: str, repo: str, db: dict):
    import requests
    # Rilegge la sha piu' recente subito prima di scrivere, per evitare conflitti
    # se il file e' stato aggiornato nel frattempo.
    _, sha = _github_load(token, repo)
    url = f"https://api.github.com/repos/{repo}/contents/{CLIENTS_DB_PATH}"
    content_b64 = base64.b64encode(
        json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")
    payload = {"message": "Aggiorna anagrafica clienti", "content": content_b64, "branch": "main"}
    if sha:
        payload["sha"] = sha
    resp = requests.put(url, headers=_github_headers(token), json=payload, timeout=10)
    resp.raise_for_status()


def _load() -> dict:
    token, repo = _get_github_config()
    if token and repo:
        db, _ = _github_load(token, repo)
        return db
    if os.path.exists(CLIENTS_DB_PATH):
        with open(CLIENTS_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(db: dict):
    token, repo = _get_github_config()
    if token and repo:
        _github_save(token, repo, db)
        return
    os.makedirs(os.path.dirname(CLIENTS_DB_PATH), exist_ok=True)
    with open(CLIENTS_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def _key(nome: str) -> str:
    return nome.strip().lower()


def list_clients() -> list:
    db = _load()
    return sorted(db.values(), key=lambda c: c["cliente"].lower())


def find_client(nome: str):
    """Cerca un cliente per nome: prima match esatto, poi match parziale
    (utile quando l'OCR estrae un nome leggermente diverso, es. abbreviato)."""
    if not nome or not nome.strip():
        return None
    db = _load()
    key = _key(nome)

    if key in db:
        return db[key]

    for k, record in db.items():
        if k in key or key in k:
            return record
    return None


def upsert_client(cliente: str, indirizzo: str, cap: str, citta: str, provincia: str,
                   vincolo: str = "", coordinate: str = ""):
    """Salva o aggiorna un cliente in anagrafica (upsert per nome)."""
    if not cliente or not cliente.strip():
        return
    db = _load()
    db[_key(cliente)] = {
        "cliente": cliente.strip(),
        "indirizzo": indirizzo or "",
        "cap": cap or "",
        "citta": citta or "",
        "provincia": provincia or "",
        "vincolo": vincolo or "",
        "coordinate": coordinate or "",
    }
    _save(db)


def delete_client(nome: str):
    db = _load()
    key = _key(nome)
    if key in db:
        del db[key]
        _save(db)
        return True
    return False
