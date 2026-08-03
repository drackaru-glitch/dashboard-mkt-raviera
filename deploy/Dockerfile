# Alternativa em container, caso o TI prefira Docker em vez de systemd direto no servidor.
# Build (rodar a partir da raiz do projeto, onde ficam as pastas backend/ e frontend/):
#   docker build -f deploy/Dockerfile -t dashboard-raviera .
# Rodar (lembre de montar um volume pro banco e passar o .env):
#   docker run -d --name dashboard-raviera -p 8000:8000 \
#     --env-file backend/.env \
#     -v dashboard_raviera_db:/app/backend \
#     --restart always \
#     dashboard-raviera

FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

WORKDIR /app/backend

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
