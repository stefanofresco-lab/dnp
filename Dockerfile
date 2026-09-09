# Immagine per Render.com (Web Service Docker, piano gratuito).
# Diamo pieno controllo su Python/Tesseract/Poppler, evitando le sorprese
# incontrate su Streamlit Community Cloud (versione Python imposta, bug di
# compatibilita' tra le loro librerie interne).
FROM python:3.11-slim

# Dipendenze di sistema per OCR (Tesseract) e PDF (Poppler).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-ita \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render fornisce la porta da usare tramite la variabile d'ambiente $PORT.
EXPOSE 8501
CMD streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true
