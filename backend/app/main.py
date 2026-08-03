import threading
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import Base, SessionLocal, engine
from app.models import SaldoCache
from app.routers import admin_router, auth_router, dashboard_router, followize_router
from app.sync import SyncEmAndamento, _sync_news, sync_all

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Raviera Cockpit API", version="0.45")

app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(dashboard_router.router)
app.include_router(followize_router.router)


def _rodar_sync_news_agendado():
    db = SessionLocal()
    try:
        novos = _sync_news(db)
        db.commit()
        print(f"[agendador] notícias sincronizadas — {novos} itens novos")
    finally:
        db.close()


def _rodar_sync_geral_agendado():
    db = SessionLocal()
    try:
        resultado = sync_all(db)
        print(f"[agendador] sincronização geral (Meta/Google/Instagram/funil) concluída — {resultado}")
    except SyncEmAndamento:
        print("[agendador] sincronização geral pulada — já havia uma sincronização em andamento")
    except Exception as exc:
        # Nunca deixa um erro de sincronização (ex.: limite de taxa da API do Meta)
        # derrubar o servidor inteiro — só registra e segue.
        print(f"[agendador] falha na sincronização geral: {exc}")
    finally:
        db.close()


def _sync_atrasada_ao_ligar():
    # Se o servidor estava desligado às 06h (ex.: PC desligado de madrugada), roda a
    # sincronização geral assim que ele voltar, em vez de esperar o próximo dia.
    db = SessionLocal()
    try:
        ultimo = db.query(SaldoCache).order_by(SaldoCache.atualizado_em.desc()).first()
        se_esta_velho = not ultimo or not ultimo.atualizado_em or (
            datetime.utcnow() - ultimo.atualizado_em > timedelta(hours=20)
        )
    finally:
        db.close()
    if se_esta_velho:
        print("[agendador] última sincronização tem mais de 20h — sincronizando agora")
        _rodar_sync_geral_agendado()


# Sincronização geral (Meta Ads, Google Ads, Instagram, funil, notícias): roda sozinha
# às 06h todo dia, além de rodar quando alguém clica em "Atualizar dados" no dashboard.
# Notícias também rodam de novo às 15h pra pegar matérias publicadas ao longo do dia.
scheduler = BackgroundScheduler()
scheduler.add_job(_rodar_sync_geral_agendado, CronTrigger(hour=6, minute=0))
scheduler.add_job(_rodar_sync_news_agendado, CronTrigger(hour=15, minute=0))
scheduler.start()

# Roda em thread separada — uma sincronização de 20+ contas pode levar minutos e não pode
# travar o servidor de responder requisições enquanto isso (já aconteceu: o servidor ficava
# mudo até a sincronização inteira terminar).
threading.Thread(target=_sync_atrasada_ao_ligar, daemon=True).start()

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
