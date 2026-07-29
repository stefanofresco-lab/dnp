"""Anagrafica clienti persistente: salva indirizzi gia' visti/inseriti cosi' che
le prossime volte vengano riconosciuti automaticamente dal nome cliente, senza
dover ridigitare o ri-estrarre l'indirizzo dal DDT."""
import json
import os

CLIENTS_DB_PATH = "data/clienti.json"


def _load() -> dict:
    if os.path.exists(CLIENTS_DB_PATH):
        with open(CLIENTS_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(db: dict):
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


def upsert_client(cliente: str, indirizzo: str, cap: str, citta: str, provincia: str, vincolo: str = ""):
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
