import threading
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from sqlalchemy import inspect, text

from app.database import Base, SessionLocal, engine
from app.models import Notificacao, SaldoCache, SistemaEstado
from app.notas_versao import NOTAS_VERSAO
from app.routers import admin_router, auth_router, dashboard_router, followize_router, letalk_router, notificacoes_router
from app.sync import DIAS_JANELA_SYNC_PADRAO, SyncEmAndamento, _sync_news, sync_all

VERSAO_ATUAL = "0.58"

Base.metadata.create_all(bind=engine)


def _migrar_colunas_novas():
    """`create_all` só cria TABELA nova — uma coluna nova em tabela já existente precisa de
    ALTER TABLE manual. Seguro rodar toda vez: só altera o que ainda não existe."""
    colunas_users = {c["name"] for c in inspect(engine).get_columns("users")}
    if "ordem_blocos" not in colunas_users:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN ordem_blocos VARCHAR"))
    if "notificacoes_lidas_ate" not in colunas_users:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN notificacoes_lidas_ate DATETIME"))

    colunas_brands = {c["name"] for c in inspect(engine).get_columns("brands")}
    if "orcamento_diario_real" not in colunas_brands:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE brands ADD COLUMN orcamento_diario_real FLOAT"))
    if "orcamento_atualizado_em" not in colunas_brands:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE brands ADD COLUMN orcamento_atualizado_em DATETIME"))
    if "ultimo_status_saldo" not in colunas_brands:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE brands ADD COLUMN ultimo_status_saldo VARCHAR"))
    if "ultimo_orcamento_real_zerado" not in colunas_brands:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE brands ADD COLUMN ultimo_orcamento_real_zerado BOOLEAN"))

    colunas_token_saude = {c["name"] for c in inspect(engine).get_columns("token_saude")}
    if "alertado" not in colunas_token_saude:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE token_saude ADD COLUMN alertado BOOLEAN"))


def _checar_nova_versao():
    """Compara a versão rodando agora com a última que o sino já avisou. Diferente = alguém
    atualizou o servidor — gera uma notificação geral (brand_id nulo, todo mundo vê) com o
    resumo dessa versão. Primeira vez que essa checagem roda no banco (sem linha em
    SistemaEstado ainda) não notifica nada — só grava a versão atual como "já avisada", senão
    todo mundo seria avisado da versão que já está instalada desde sempre."""
    db = SessionLocal()
    try:
        estado = db.query(SistemaEstado).first()
        if not estado:
            db.add(SistemaEstado(ultima_versao_notificada=VERSAO_ATUAL))
            db.commit()
            return
        if estado.ultima_versao_notificada != VERSAO_ATUAL:
            texto = NOTAS_VERSAO.get(VERSAO_ATUAL, "Nova versão do FEFO no ar — sem detalhes registrados desta vez.")
            db.add(Notificacao(tipo="versao", titulo=f"Nova versão: {VERSAO_ATUAL}", mensagem=texto))
            estado.ultima_versao_notificada = VERSAO_ATUAL
            db.commit()
    finally:
        db.close()


_migrar_colunas_novas()
_checar_nova_versao()

app = FastAPI(title="FEFO API", version=VERSAO_ATUAL)

app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(dashboard_router.router)
app.include_router(followize_router.router)
app.include_router(letalk_router.router)
app.include_router(notificacoes_router.router)


def _rodar_sync_news_agendado():
    db = SessionLocal()
    try:
        novos = _sync_news(db)
        db.commit()
        print(f"[agendador] notícias sincronizadas — {novos} itens novos")
    finally:
        db.close()


def _rodar_sync_geral_agendado(days: int | None = None):
    db = SessionLocal()
    try:
        resultado = sync_all(db, **({"days": days} if days else {}))
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
        agora = datetime.utcnow()
        dias_parado = (agora - ultimo.atualizado_em).days if (ultimo and ultimo.atualizado_em) else None
        se_esta_velho = dias_parado is None or (agora - ultimo.atualizado_em) > timedelta(hours=20)
    finally:
        db.close()
    if se_esta_velho:
        # A sincronização normal só pede DIAS_JANELA_SYNC_PADRAO dias ao Meta (ver sync.py) —
        # o suficiente quando ela roda em dia. Depois de um servidor desligado por mais tempo
        # que isso, uma janela maior evita deixar dias sem dado nenhum no meio do buraco.
        janela = max(DIAS_JANELA_SYNC_PADRAO, (dias_parado or 0) + 2) if dias_parado else None
        print(f"[agendador] última sincronização tem mais de 20h — sincronizando agora (janela: {janela or 'padrão'} dias)")
        _rodar_sync_geral_agendado(days=janela)


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
        # Sem isto, o navegador pode decidir sozinho (por heurística, sem perguntar ao
        # servidor) que uma cópia antiga do index.html ainda está "fresca" e nem tenta
        # recarregar — quem mexe no frontend recarrega a página, vê o mesmo JS de antes, e
        # acha que a mudança não entrou. "no-cache" força revalidar (ETag) a cada carga; como
        # o navegador já manda o ETag de volta, isso não baixa o arquivo de novo à toa.
        return FileResponse(str(FRONTEND_DIR / "index.html"), headers={"Cache-Control": "no-cache"})
