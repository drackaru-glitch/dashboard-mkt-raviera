"""Sincroniza saldo, gasto/leads diários e melhor criativo com o Meta (ou gera dados de
demonstração quando META_DEMO_MODE está ativo). Chamado pelo endpoint /api/sync.
"""

import hashlib
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import DATABASE_URL, GOOGLE_ADS_DEMO_MODE, META_DEMO_MODE, NBS_DEMO_MODE
from app.database import SessionLocal
from app.models import (
    AdAccount,
    AdDailyMetric,
    Brand,
    BrandNewsItem,
    DailyMetric,
    FunilDailyMetric,
    GoogleAccountCache,
    GoogleAdsAccount,
    GoogleDailyMetric,
    GoogleKeywordCache,
    GoogleLocationCache,
    GoogleTopAdCache,
    InstagramAccount,
    InstagramStatsCache,
    InstagramTopPostCache,
    SaldoCache,
)

if not META_DEMO_MODE:
    from app import meta_client

if not GOOGLE_ADS_DEMO_MODE:
    from app import google_ads_client

from app import geocoding, instagram_client, media_cache, news_client

_sync_lock = threading.Lock()
_sync_started_at: float | None = None

# Cada conta sincroniza numa thread separada (com sua própria sessão do banco) em vez de
# uma de cada vez — é isso que deixa a sincronização muito mais rápida. Mas o SQLite só
# aceita um escritor por vez: sincronizar várias contas ao mesmo tempo nele causa erros de
# "database is locked" e de arquivo de mídia (duas threads escrevendo o mesmo caminho). Por
# isso o paralelismo só é ligado de verdade quando o banco é Postgres (que aguenta escrita
# concorrente); no SQLite, cai pra 1 (sequencial, como era antes) — sem regressão.
MAX_SYNC_WORKERS = 5 if not DATABASE_URL.startswith("sqlite") else 1

# Buscar os dados na API do Meta é a parte lenta (~12s por conta), e não toca no banco —
# então pode rodar em paralelo mesmo no SQLite. Só a GRAVAÇÃO precisa ser uma de cada vez.
# Antes as duas coisas andavam juntas na mesma thread, então no SQLite (MAX_SYNC_WORKERS=1)
# cada conta esperava a anterior inteira, tempo de rede incluído.
MAX_FETCH_WORKERS = 5

# Se uma sincronização ficar "presa" por mais que isso (algo travou sem lançar exceção),
# a próxima tentativa destrava e segue em vez de bloquear pra sempre com a mensagem de
# "já tem uma sincronização em andamento".
SYNC_TIMEOUT_SECONDS = 15 * 60


class SyncEmAndamento(Exception):
    """Já existe uma sincronização em andamento neste processo."""


def _brand_matches_campaign(brand: Brand, campaign_name: str) -> bool:
    if not brand.campaign_filter:
        return True  # conta não compartilhada: tudo pertence à única marca
    if brand.campaign_filter.startswith("!"):
        # Exclusão: a conta é "dedicada" a essa marca, mas tem uma campanha de outra
        # cidade/marca misturada por engano (aconteceu com Jeep Ji-Paraná/Porto Velho) — em
        # vez de exigir um prefixo exato (os nomes reais nem sempre são consistentes), só
        # exclui quem contém o texto informado e aceita todo o resto da conta.
        return brand.campaign_filter[1:].lower() not in campaign_name.lower()
    return campaign_name.lower().startswith(brand.campaign_filter.lower())


def _demo_row_for_brand(brand: Brand, day: date, rng: random.Random) -> dict:
    dow = day.weekday()
    weekend_factor = 0.6 if dow >= 5 else 1.0
    base_gasto = 100 + (brand.id * 37) % 400
    base_leads = 3 + (brand.id * 5) % 12
    gasto = round(base_gasto * weekend_factor * (0.75 + rng.random() * 0.5), 2)
    leads_formulario = max(0, round(base_leads * weekend_factor * (0.6 + rng.random() * 0.7)))
    leads_whatsapp = max(0, round(leads_formulario * 0.4 * rng.random()))
    return {"gasto": gasto, "leads_formulario": leads_formulario, "leads_whatsapp": leads_whatsapp}


def _buscar_meta_account(meta_account_id: str, days: int, since_criativos: str, until_criativos: str) -> dict:
    """Faz as três chamadas à API do Meta de uma conta. Só rede, sem tocar no banco — é isso
    que permite buscar várias contas ao mesmo tempo mesmo usando SQLite."""
    return {
        "saldo": meta_client.get_ad_account_balance(meta_account_id),
        "insights": meta_client.get_daily_insights(meta_account_id, days=days),
        "ads_daily": meta_client.get_ads_daily(meta_account_id, since_criativos, until_criativos),
    }


def _sync_meta_account(
    db: Session,
    account: AdAccount,
    account_brands: list[Brand],
    days: int,
    since_criativos: str,
    until_criativos: str,
    dados: dict | None = None,
) -> int:
    """Grava no banco os dados de uma conta. `dados` vem da fase de busca (_buscar_meta_account);
    se não vier, busca na hora — o que faz esta função voltar a ser rede + banco junto."""
    dias_sincronizados = 0
    if dados is None and not META_DEMO_MODE:
        dados = _buscar_meta_account(account.meta_account_id, days, since_criativos, until_criativos)

    # --- saldo ---
    if META_DEMO_MODE:
        saldo = round(500 + (account.id * 733) % 5000, 2)
        moeda = "BRL"
    else:
        result = dados["saldo"]
        saldo = result["saldo"]
        moeda = result["moeda"]
    cache = db.query(SaldoCache).filter(SaldoCache.ad_account_id == account.id).first()
    if not cache:
        cache = SaldoCache(ad_account_id=account.id)
        db.add(cache)
    cache.saldo = saldo
    cache.moeda = moeda
    cache.atualizado_em = datetime.utcnow()

    # --- gasto/leads diários ---
    if META_DEMO_MODE:
        rng = random.Random(account.id)
        for brand in account_brands:
            for i in range(days):
                day = date.today() - timedelta(days=i)
                row = _demo_row_for_brand(brand, day, rng)
                _upsert_daily_metric(db, brand.id, day, row, fonte="demo")
                dias_sincronizados += 1
    else:
        insights = dados["insights"]
        for brand in account_brands:
            brand_rows = [r for r in insights if _brand_matches_campaign(brand, r["campaign_name"])]
            by_date: dict[str, dict] = {}
            for r in brand_rows:
                d = by_date.setdefault(r["data"], {"gasto": 0.0, "leads_formulario": 0, "leads_whatsapp": 0})
                d["gasto"] += r["gasto"]
                d["leads_formulario"] += r["leads_formulario"]
                d["leads_whatsapp"] += r["leads_whatsapp"]
            for data_str, row in by_date.items():
                day = datetime.strptime(data_str, "%Y-%m-%d").date()
                _upsert_daily_metric(db, brand.id, day, row, fonte="meta")
                dias_sincronizados += 1

    # --- histórico diário de criativos (pra recalcular o top 3 por período) ---
    # Uma chamada só por conta (não por marca) — contas com várias marcas (ex.: BMW
    # Auto Campo Grande = 3 marcas) chamavam a mesma listagem de anúncios à toa,
    # o que desperdiçava tempo e contribuía pro limite de chamadas do Meta.
    if META_DEMO_MODE:
        for brand in account_brands:
            _gerar_criativos_demo(db, brand, since_criativos, until_criativos)
    else:
        ads_daily = dados["ads_daily"]
        for brand in account_brands:
            brand_rows = [r for r in ads_daily if _brand_matches_campaign(brand, r["campaign_name"])]
            _upsert_ad_daily_metrics(db, brand.id, brand_rows, since_criativos)

    return dias_sincronizados


def sync_all(db: Session, days: int = 30) -> dict:
    global _sync_started_at
    if not _sync_lock.acquire(blocking=False):
        presa_ha_muito_tempo = _sync_started_at is not None and (time.monotonic() - _sync_started_at) > SYNC_TIMEOUT_SECONDS
        if not presa_ha_muito_tempo:
            raise SyncEmAndamento()
        # A sincronização anterior está rodando há mais de 15 minutos sem terminar — trata
        # como travada/abandonada em vez de bloquear os próximos cliques em "Atualizar" pra
        # sempre. Destrava à força e tenta de novo.
        print("[sync] sincronização anterior parece travada (>15min) — destravando e seguindo mesmo assim")
        try:
            _sync_lock.release()
        except RuntimeError:
            pass
        if not _sync_lock.acquire(blocking=False):
            raise SyncEmAndamento()
    _sync_started_at = time.monotonic()
    try:
        return _sync_all(db, days)
    finally:
        _sync_started_at = None
        _sync_lock.release()


def _sync_all(db: Session, days: int = 30) -> dict:
    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all()
    accounts = db.query(AdAccount).all()
    accounts_by_id = {a.id: a for a in accounts}
    brands_by_account: dict[int, list[Brand]] = {}
    for b in brands:
        brands_by_account.setdefault(b.ad_account_id, []).append(b)

    synced_accounts = 0
    synced_days = 0
    falhas: list[str] = []

    # Janela usada pro histórico de criativos: do dia 1 do mês ANTERIOR até hoje — cobre
    # "Mês Atual" e "Mês Anterior" no dashboard, que recalcula o top 3 na hora a partir
    # desses dados brutos (em vez de um "top 3" fixo pré-calculado pelo backend).
    hoje = date.today()
    ano_mes_anterior = hoje.year if hoje.month > 1 else hoje.year - 1
    mes_anterior = hoje.month - 1 if hoje.month > 1 else 12
    since_criativos = date(ano_mes_anterior, mes_anterior, 1).isoformat()
    until_criativos = hoje.isoformat()

    contas_a_sincronizar = [
        (account_id, account_brands)
        for account_id, account_brands in brands_by_account.items()
        if account_id in accounts_by_id
    ]

    # Fase 1: buscar na API do Meta, várias contas ao mesmo tempo. Nada disso toca no banco,
    # então é seguro em paralelo mesmo no SQLite — e é aqui que estava quase todo o tempo.
    # Uma conta que falhar (token vencido, rate limit) não atrapalha as outras.
    dados_por_conta: dict[int, dict] = {}
    if not META_DEMO_MODE:
        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
            futures = {
                executor.submit(
                    _buscar_meta_account, accounts_by_id[account_id].meta_account_id, days, since_criativos, until_criativos
                ): account_id
                for account_id, _ in contas_a_sincronizar
            }
            for fut in as_completed(futures):
                account = accounts_by_id[futures[fut]]
                try:
                    dados_por_conta[futures[fut]] = fut.result()
                except Exception as exc:
                    falhas.append(f"{account.nome}: {exc}")
                    print(f"[sync] falha na conta Meta '{account.nome}' ({account.meta_account_id}): {exc}")

    # Fase 2: gravar, uma conta de cada vez — o SQLite só aceita um escritor, e escrever é
    # rápido perto do tempo de rede que já passou.
    for account_id, account_brands in contas_a_sincronizar:
        if not META_DEMO_MODE and account_id not in dados_por_conta:
            continue  # a busca falhou e já foi registrada acima
        account = accounts_by_id[account_id]
        try:
            synced_days += _sync_meta_account(
                db, account, account_brands, days, since_criativos, until_criativos, dados_por_conta.get(account_id)
            )
            db.commit()
            synced_accounts += 1
        except Exception as exc:
            db.rollback()
            falhas.append(f"{account.nome}: {exc}")
            print(f"[sync] falha ao gravar conta Meta '{account.nome}' ({account.meta_account_id}): {exc}")

    try:
        contas_instagram = _sync_instagram(db, days)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Instagram: {exc}")
        print(f"[sync] falha ao sincronizar Instagram: {exc}")
        contas_instagram = 0

    try:
        contas_google = _sync_google_ads(db, days)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Google Ads: {exc}")
        print(f"[sync] falha ao sincronizar Google Ads: {exc}")
        contas_google = 0

    try:
        marcas_funil = _sync_funil(db, days)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Funil: {exc}")
        print(f"[sync] falha ao sincronizar funil: {exc}")
        marcas_funil = 0

    try:
        noticias = _sync_news(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Notícias: {exc}")
        print(f"[sync] falha ao sincronizar notícias: {exc}")
        noticias = 0

    return {
        "contas_sincronizadas": synced_accounts,
        "registros_diarios": synced_days,
        "contas_instagram_sincronizadas": contas_instagram,
        "contas_google_sincronizadas": contas_google,
        "marcas_funil_sincronizadas": marcas_funil,
        "noticias_coletadas": noticias,
        "modo_demo": META_DEMO_MODE,
        "falhas": falhas,
    }


def _demo_google_row_for_brand(brand: Brand, day: date, rng: random.Random) -> dict:
    dow = day.weekday()
    weekend_factor = 0.7 if dow >= 5 else 1.0
    base_gasto = 80 + (brand.id * 41) % 300
    base_conv = 2 + (brand.id * 3) % 8
    gasto = round(base_gasto * weekend_factor * (0.75 + rng.random() * 0.5), 2)
    cliques = max(1, round(gasto / (1.5 + rng.random() * 3)))
    conversoes = round(base_conv * weekend_factor * (0.6 + rng.random() * 0.7), 1)
    return {"gasto": gasto, "cliques": cliques, "conversoes": conversoes}


def _sync_google_ads(db: Session, days: int) -> int:
    brands = db.query(Brand).filter(Brand.google_ads_account_id.isnot(None)).all()
    accounts = db.query(GoogleAdsAccount).all()
    accounts_by_id = {a.id: a for a in accounts}
    brands_by_account: dict[int, list[Brand]] = {}
    for b in brands:
        brands_by_account.setdefault(b.google_ads_account_id, []).append(b)

    synced = 0
    # Mesma lógica do Meta: cada conta sincroniza numa thread própria, com sua própria
    # sessão e commit isolado — uma conta com token vencido ou API travada não derruba nem
    # atrasa as outras.
    with ThreadPoolExecutor(max_workers=MAX_SYNC_WORKERS) as executor:
        futures = {
            executor.submit(_sync_google_account_thread, account_id, [b.id for b in account_brands], days): account_id
            for account_id, account_brands in brands_by_account.items()
            if account_id in accounts_by_id
        }
        for fut in as_completed(futures):
            ok, erro = fut.result()
            account = accounts_by_id[futures[fut]]
            if ok:
                synced += 1
            else:
                print(f"[sync] falha na conta Google Ads '{account.nome}' ({account.customer_id}): {erro}")

    return synced


def _sync_google_account_thread(account_id: int, brand_ids: list[int], days: int) -> tuple[bool, str]:
    db = SessionLocal()
    try:
        account = db.query(GoogleAdsAccount).filter(GoogleAdsAccount.id == account_id).first()
        account_brands = db.query(Brand).filter(Brand.id.in_(brand_ids)).all()
        _sync_google_account(db, account, account_brands, days)
        db.commit()
        return True, ""
    except Exception as exc:
        db.rollback()
        return False, str(exc)
    finally:
        db.close()


def _sync_google_account(db: Session, account: GoogleAdsAccount, account_brands: list[Brand], days: int) -> None:
    # --- orçamento diário ---
    if GOOGLE_ADS_DEMO_MODE:
        orcamento = round(50 + (account.id * 733) % 300, 2)
        status = "ENABLED"
    else:
        result = google_ads_client.get_account_budget_and_status(account.customer_id)
        orcamento = result["orcamento_diario"]
        status = result["status"]
    cache = db.query(GoogleAccountCache).filter(GoogleAccountCache.google_ads_account_id == account.id).first()
    if not cache:
        cache = GoogleAccountCache(google_ads_account_id=account.id)
        db.add(cache)
    cache.orcamento_diario = orcamento
    cache.status = status
    cache.atualizado_em = datetime.utcnow()

    # --- gasto/cliques/conversões diários ---
    # Cada marca aqui tem sua própria conta do Google Ads (1:1), então os dados
    # da conta pertencem inteiros à marca — sem filtro de campanha necessário.
    brand = account_brands[0]
    if GOOGLE_ADS_DEMO_MODE:
        rng = random.Random(account.id)
        for i in range(days):
            day = date.today() - timedelta(days=i)
            row = _demo_google_row_for_brand(brand, day, rng)
            _upsert_google_daily_metric(db, brand.id, day, row)
    else:
        metrics = google_ads_client.get_daily_metrics(account.customer_id, days=days)
        for row in metrics:
            day = datetime.strptime(row["data"], "%Y-%m-%d").date()
            _upsert_google_daily_metric(db, brand.id, day, row)

    # --- top palavras-chave (impacto por conversão) ---
    if GOOGLE_ADS_DEMO_MODE:
        rng_kw = random.Random(brand.id * 71)
        palavras_demo = ["preço", "promoção", "concessionária", "avaliação usado", "financiamento", "revisão", "test drive", "peças originais"]
        top_keywords = []
        for i, palavra in enumerate(palavras_demo[:8]):
            conv = max(0.5, round((4 + brand.id % 12) * (1 - i * 0.1) * (0.6 + rng_kw.random() * 0.7), 1))
            cpa_demo = 15 + rng_kw.random() * 25
            top_keywords.append(
                {
                    "texto": f"{brand.nome.lower()} {palavra}",
                    "cliques": round(conv * (3 + rng_kw.random() * 4)),
                    "conversoes": conv,
                    "gasto": round(conv * cpa_demo, 2),
                }
            )
    else:
        top_keywords = google_ads_client.get_top_keywords(account.customer_id, days=days, limit=10)
    top_keywords.sort(key=lambda k: k["conversoes"], reverse=True)
    _replace_google_keywords(db, brand.id, top_keywords[:10])

    # --- localidades atingidas (mapa de calor) ---
    if GOOGLE_ADS_DEMO_MODE:
        rng_loc = random.Random(brand.id * 83)
        cidades_demo = [("Campo Grande", "Mato Grosso do Sul"), ("Dourados", "Mato Grosso do Sul"), ("Rio Branco", "Acre"), ("Vitória", "Espírito Santo"), ("Belém", "Pará")]
        locations = []
        for cidade, estado in cidades_demo:
            conv = max(0, round((3 + brand.id % 10) * (0.4 + rng_loc.random() * 0.8), 1))
            locations.append(
                {
                    "cidade": cidade,
                    "estado": estado,
                    "cliques": round(conv * (4 + rng_loc.random() * 5)),
                    "conversoes": conv,
                    "gasto": round(conv * (15 + rng_loc.random() * 25), 2),
                }
            )
    else:
        locations = google_ads_client.get_geographic_performance(account.customer_id, days=days, limit=15)
    _replace_google_locations(db, brand.id, locations)


def _sync_news(db: Session) -> int:
    # Roda 2x/dia (agendador em app.main) + toda vez que alguém clica em "Atualizar dados".
    # v1 sem IA: só coleta manchetes reais dos portais e filtra por marca (ver news_client).
    itens = news_client.coletar_noticias()
    novos = 0
    for item in itens:
        existe = (
            db.query(BrandNewsItem)
            .filter(BrandNewsItem.marca_chave == item["marca_chave"], BrandNewsItem.link == item["link"])
            .first()
        )
        if existe:
            continue
        thumbnail_original = item.pop("thumbnail_url", None)
        chave = hashlib.md5(item["link"].encode("utf-8")).hexdigest()
        item["thumbnail_url"] = media_cache.baixar_imagem(thumbnail_original, "noticias", chave)
        db.add(BrandNewsItem(**item))
        novos += 1
    # mantém só os últimos 14 dias, pra não crescer sem limite
    limite = datetime.utcnow() - timedelta(days=14)
    db.query(BrandNewsItem).filter(BrandNewsItem.coletado_em < limite).delete()
    return novos


def _sync_funil(db: Session, days: int) -> int:
    # Lead vem do Meta (DailyMetric, já sincronizado acima); Proposta/Venda viriam do Followize
    # (webhook, ver app.routers.followize_router). Sem eventos reais chegando ainda, tudo aqui é
    # demo — cada marca tem uma taxa de conversão própria e estável (seed = brand.id) pra ficar
    # plausível entre uma sincronização e outra. Linhas com fonte="manual" (corrigidas à mão)
    # nunca são sobrescritas por aqui.
    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all()
    for brand in brands:
        rng = random.Random(brand.id * 131)
        taxa_lead_proposta = 0.35 + (brand.id % 5) * 0.05  # ~35% a 55%
        taxa_proposta_venda = 0.15 + (brand.id % 4) * 0.04  # ~15% a 27%
        for i in range(days):
            day = date.today() - timedelta(days=i)
            existing = db.query(FunilDailyMetric).filter(FunilDailyMetric.brand_id == brand.id, FunilDailyMetric.data == day).first()
            if existing and existing.fonte == "manual":
                continue
            metric = db.query(DailyMetric).filter(DailyMetric.brand_id == brand.id, DailyMetric.data == day).first()
            leads = (metric.leads_formulario + metric.leads_whatsapp) if metric else 0
            if NBS_DEMO_MODE:
                propostas = round(leads * taxa_lead_proposta * (0.7 + rng.random() * 0.6))
                vendas = round(propostas * taxa_proposta_venda * (0.7 + rng.random() * 0.6))
            else:
                propostas, vendas = 0, 0  # placeholder até integrarmos o Followize de verdade
            _upsert_funil_daily_metric(db, brand.id, day, propostas, vendas, fonte="demo")
    return len(brands)


def _buscar_instagram_account(instagram_business_id: str, access_token: str, days: int) -> dict:
    """Só chamadas de rede, sem tocar no banco — por isso roda em paralelo (mesma ideia do Meta)."""
    top_post = instagram_client.get_top_post(instagram_business_id, access_token)
    if top_post:
        # mesmo motivo dos criativos: a URL do Meta expira, guardamos a imagem localmente.
        top_post["thumbnail_url"] = media_cache.baixar_imagem(top_post["thumbnail_url"], "instagram", top_post["post_id"])
    return {
        "seguidores_atual": instagram_client.get_followers_count(instagram_business_id, access_token),
        "seguidores_ganhos": instagram_client.get_follower_gain(instagram_business_id, access_token, days=days),
        "top_post": top_post,
    }


def _sync_instagram(db: Session, days: int) -> int:
    accounts = db.query(InstagramAccount).all()
    synced = 0

    # Fase 1: buscar na API, várias contas ao mesmo tempo (não toca no banco).
    dados_por_conta: dict[int, dict] = {}
    com_token = [a for a in accounts if a.access_token]
    if com_token:
        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
            futures = {
                executor.submit(_buscar_instagram_account, a.instagram_business_id, a.access_token, days): a
                for a in com_token
            }
            for fut in as_completed(futures):
                account = futures[fut]
                try:
                    dados_por_conta[account.id] = fut.result()
                except Exception as exc:
                    print(f"[sync] falha na conta Instagram '{account.nome}' ({account.instagram_business_id}): {exc}")

    # Fase 2: gravar, uma de cada vez.
    for account in accounts:
        if account.access_token and account.id not in dados_por_conta:
            continue  # a busca falhou e já foi registrada acima
        try:
            _sync_instagram_account(db, account, days, dados_por_conta.get(account.id))
            db.commit()
            synced += 1
        except Exception as exc:
            db.rollback()
            print(f"[sync] falha ao gravar conta Instagram '{account.nome}' ({account.instagram_business_id}): {exc}")
    return synced


def _sync_instagram_account(db: Session, account: InstagramAccount, days: int, dados: dict | None = None) -> None:
    """Grava no banco os dados de uma conta. `dados` vem da fase de busca; se não vier, busca
    na hora (volta a ser rede + banco junto)."""
    if account.access_token and dados is None:
        dados = _buscar_instagram_account(account.instagram_business_id, account.access_token, days)

    if not account.access_token:
        rng = random.Random(account.id * 53)
        seguidores_atual = 500 + (account.id * 733) % 12000
        seguidores_ganhos = round((5 + account.id % 20) * (days / 30) * (0.7 + rng.random() * 0.8))
        top_post = {
            "post_id": f"demo-post-{account.id}",
            "legenda": f"Post demo — {account.nome}",
            "thumbnail_url": None,
            "permalink": None,
            "curtidas": 40 + rng.randint(0, 300),
            "comentarios": rng.randint(0, 30),
            "publicado_em": None,
        }
    else:
        seguidores_atual = dados["seguidores_atual"]
        seguidores_ganhos = dados["seguidores_ganhos"]
        top_post = dados["top_post"]

    stats = db.query(InstagramStatsCache).filter(InstagramStatsCache.instagram_account_id == account.id).first()
    if not stats:
        stats = InstagramStatsCache(instagram_account_id=account.id)
        db.add(stats)
    stats.seguidores_atual = seguidores_atual
    stats.seguidores_ganhos_periodo = seguidores_ganhos
    stats.atualizado_em = datetime.utcnow()

    if top_post:
        post_cache = db.query(InstagramTopPostCache).filter(InstagramTopPostCache.instagram_account_id == account.id).first()
        if not post_cache:
            post_cache = InstagramTopPostCache(instagram_account_id=account.id)
            db.add(post_cache)
        post_cache.post_id = top_post["post_id"]
        post_cache.legenda = top_post["legenda"]
        post_cache.thumbnail_url = top_post["thumbnail_url"]
        post_cache.permalink = top_post["permalink"]
        post_cache.curtidas = top_post["curtidas"]
        post_cache.comentarios = top_post["comentarios"]
        post_cache.publicado_em = top_post["publicado_em"]
        post_cache.atualizado_em = datetime.utcnow()


def _upsert_ad_daily_metrics(db: Session, brand_id: int, rows: list[dict], desde: str) -> None:
    """Grava o desempenho dia a dia de cada anúncio da marca. O dashboard recalcula o top 3
    de criativos filtrando essas linhas pelo período selecionado (igual já faz com leads e
    gasto por dia) — não existe mais um "top 3" fixo pré-calculado aqui no backend."""
    # A API do Meta às vezes devolve mais de uma linha pro mesmo anúncio no mesmo dia (ex.:
    # picos de paginação) — sem juntar isso antes, a segunda linha tentava inserir de novo
    # o mesmo (marca, anúncio, dia) e estourava a restrição de unicidade da tabela.
    linhas_por_chave: dict[tuple[str, str], dict] = {}
    for row in rows:
        chave = (row["ad_id"], row["data"])
        acumulado = linhas_por_chave.get(chave)
        if not acumulado:
            linhas_por_chave[chave] = dict(row)
        else:
            acumulado["gasto"] += row["gasto"]
            acumulado["leads"] += row["leads"]

    thumbnails_cache: dict[str, str | None] = {}
    for row in linhas_por_chave.values():
        ad_id = row["ad_id"]
        if ad_id not in thumbnails_cache:
            # a URL que o Meta devolve expira depois de um tempo (~1-2 dias) — baixamos a
            # imagem uma vez por anúncio (não por dia) e guardamos no nosso servidor.
            thumbnails_cache[ad_id] = media_cache.baixar_imagem(row["thumbnail_url"], "criativos", ad_id)
        day = datetime.strptime(row["data"], "%Y-%m-%d").date()
        metric = (
            db.query(AdDailyMetric)
            .filter(AdDailyMetric.brand_id == brand_id, AdDailyMetric.ad_id == ad_id, AdDailyMetric.data == day)
            .first()
        )
        if not metric:
            metric = AdDailyMetric(brand_id=brand_id, ad_id=ad_id, data=day)
            db.add(metric)
        metric.nome_anuncio = row["nome_anuncio"]
        metric.thumbnail_url = thumbnails_cache[ad_id]
        metric.gasto = row["gasto"]
        metric.leads = row["leads"]
        metric.atualizado_em = datetime.utcnow()

    # a janela sincronizada é sempre "mês anterior + mês atual" — qualquer linha mais velha
    # que isso é de um mês que já rolou pra fora da janela, então some do histórico.
    limite = datetime.strptime(desde, "%Y-%m-%d").date()
    db.query(AdDailyMetric).filter(AdDailyMetric.brand_id == brand_id, AdDailyMetric.data < limite).delete()

    # Remove anúncios que estavam salvos dentro da janela mas não vieram mais nessa
    # sincronização — ex.: um anúncio de uma campanha que passou a ser excluída pelo filtro
    # de campanha da marca (aconteceu com a Porto Velho na conta da Jeep Ji-Paraná) fica
    # "fantasma" no histórico se a gente só apagar por data.
    ad_ids_atuais = {row["ad_id"] for row in rows}
    query_janela = db.query(AdDailyMetric).filter(AdDailyMetric.brand_id == brand_id, AdDailyMetric.data >= limite)
    if ad_ids_atuais:
        query_janela = query_janela.filter(~AdDailyMetric.ad_id.in_(ad_ids_atuais))
    query_janela.delete(synchronize_session=False)


def _gerar_criativos_demo(db: Session, brand: Brand, since: str, until: str) -> None:
    rng = random.Random(brand.id * 97)
    inicio = datetime.strptime(since, "%Y-%m-%d").date()
    fim = datetime.strptime(until, "%Y-%m-%d").date()
    num_dias = (fim - inicio).days + 1
    num_anuncios = 3 + brand.id % 2  # 3 ou 4 anúncios demo por marca
    rows = []
    for i in range(num_anuncios):
        base_leads = max(1, (5 + brand.id % 20) * (1 - i * 0.2))
        cpl_demo = 15 + rng.random() * 25
        for d in range(num_dias):
            day = inicio + timedelta(days=d)
            leads = max(0, round(base_leads * (0.5 + rng.random())))
            rows.append(
                {
                    "ad_id": f"demo-{brand.id}-{i + 1}",
                    "nome_anuncio": f"Criativo demo #{i + 1} — {brand.nome}",
                    "thumbnail_url": None,
                    "data": day.isoformat(),
                    "gasto": round(leads * cpl_demo, 2),
                    "leads": leads,
                }
            )
    _upsert_ad_daily_metrics(db, brand.id, rows, since)


def _upsert_daily_metric(db: Session, brand_id: int, day: date, row: dict, fonte: str):
    metric = db.query(DailyMetric).filter(DailyMetric.brand_id == brand_id, DailyMetric.data == day).first()
    if not metric:
        metric = DailyMetric(brand_id=brand_id, data=day)
        db.add(metric)
    metric.gasto = row["gasto"]
    metric.leads_formulario = row["leads_formulario"]
    metric.leads_whatsapp = row["leads_whatsapp"]
    metric.fonte = fonte


def _upsert_google_daily_metric(db: Session, brand_id: int, day: date, row: dict):
    metric = db.query(GoogleDailyMetric).filter(GoogleDailyMetric.brand_id == brand_id, GoogleDailyMetric.data == day).first()
    if not metric:
        metric = GoogleDailyMetric(brand_id=brand_id, data=day)
        db.add(metric)
    metric.gasto = row["gasto"]
    metric.cliques = row["cliques"]
    metric.conversoes = row["conversoes"]


def _upsert_funil_daily_metric(db: Session, brand_id: int, day: date, propostas: int, vendas: int, fonte: str = "demo"):
    metric = db.query(FunilDailyMetric).filter(FunilDailyMetric.brand_id == brand_id, FunilDailyMetric.data == day).first()
    if not metric:
        metric = FunilDailyMetric(brand_id=brand_id, data=day)
        db.add(metric)
    metric.propostas = propostas
    metric.vendas = vendas
    metric.fonte = fonte


def _replace_google_keywords(db: Session, brand_id: int, keywords: list[dict]):
    existing = {c.rank: c for c in db.query(GoogleKeywordCache).filter(GoogleKeywordCache.brand_id == brand_id).all()}
    for i, item in enumerate(keywords):
        rank = i + 1
        cache = existing.pop(rank, None)
        if not cache:
            cache = GoogleKeywordCache(brand_id=brand_id, rank=rank)
            db.add(cache)
        cache.texto = item["texto"]
        cache.gasto = item["gasto"]
        cache.cliques = item["cliques"]
        cache.conversoes = item["conversoes"]
        cache.atualizado_em = datetime.utcnow()
    for leftover in existing.values():
        db.delete(leftover)


def _replace_google_locations(db: Session, brand_id: int, locations: list[dict]):
    # Duas localidades diferentes do Google (ex: cidade vs. região metropolitana) podem
    # resolver pro mesmo nome — junta por cidade antes de gravar pra não violar o unique.
    merged: dict[str, dict] = {}
    for item in locations:
        cidade = item["cidade"]
        entry = merged.setdefault(cidade, {"cidade": cidade, "estado": item.get("estado", ""), "gasto": 0.0, "cliques": 0, "conversoes": 0.0})
        entry["gasto"] += item["gasto"]
        entry["cliques"] += item["cliques"]
        entry["conversoes"] += item["conversoes"]
    locations = sorted(merged.values(), key=lambda x: x["conversoes"], reverse=True)

    existing = {c.cidade: c for c in db.query(GoogleLocationCache).filter(GoogleLocationCache.brand_id == brand_id).all()}
    for item in locations:
        cidade = item["cidade"]
        cache = existing.pop(cidade, None)
        if not cache:
            cache = GoogleLocationCache(brand_id=brand_id, cidade=cidade)
            db.add(cache)
        cache.estado = item.get("estado", "")
        cache.gasto = item["gasto"]
        cache.cliques = item["cliques"]
        cache.conversoes = item["conversoes"]
        cache.atualizado_em = datetime.utcnow()
        if cache.latitude is None or cache.longitude is None:
            coords = geocoding.get_coordinates(db, cidade, cache.estado)
            if coords:
                cache.latitude, cache.longitude = coords
    # remove cidades que sumiram da última sincronização
    for leftover in existing.values():
        db.delete(leftover)


def _replace_google_top_ads(db: Session, brand_id: int, top3: list[dict]):
    existing = {c.rank: c for c in db.query(GoogleTopAdCache).filter(GoogleTopAdCache.brand_id == brand_id).all()}
    for i, item in enumerate(top3):
        rank = i + 1
        cache = existing.pop(rank, None)
        if not cache:
            cache = GoogleTopAdCache(brand_id=brand_id, rank=rank)
            db.add(cache)
        cache.ad_id = item["ad_id"]
        cache.titulo = item["titulo"]
        cache.campanha = item["campanha"]
        cache.gasto = item["gasto"]
        cache.cliques = item["cliques"]
        cache.conversoes = item["conversoes"]
        cache.atualizado_em = datetime.utcnow()
    for leftover in existing.values():
        db.delete(leftover)
