"""Sincroniza saldo, gasto/leads diários e melhor criativo com o Meta (ou gera dados de
demonstração quando META_DEMO_MODE está ativo). Chamado pelo endpoint /api/sync.
"""

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import META_DEMO_MODE
from app.models import AdAccount, Brand, DailyMetric, SaldoCache, TopCreativeCache

if not META_DEMO_MODE:
    from app import meta_client


def _brand_matches_campaign(brand: Brand, campaign_name: str) -> bool:
    if not brand.campaign_filter:
        return True  # conta não compartilhada: tudo pertence à única marca
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


def sync_all(db: Session, days: int = 30) -> dict:
    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all()
    accounts = db.query(AdAccount).all()
    accounts_by_id = {a.id: a for a in accounts}
    brands_by_account: dict[int, list[Brand]] = {}
    for b in brands:
        brands_by_account.setdefault(b.ad_account_id, []).append(b)

    synced_accounts = 0
    synced_days = 0

    for account_id, account_brands in brands_by_account.items():
        account = accounts_by_id.get(account_id)
        if not account:
            continue
        synced_accounts += 1

        # --- saldo ---
        if META_DEMO_MODE:
            saldo = round(500 + (account.id * 733) % 5000, 2)
            moeda = "BRL"
        else:
            result = meta_client.get_ad_account_balance(account.meta_account_id)
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
                    synced_days += 1
        else:
            insights = meta_client.get_daily_insights(account.meta_account_id, days=days)
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
                    synced_days += 1

        # --- melhor criativo ---
        for brand in account_brands:
            if META_DEMO_MODE:
                top = {
                    "ad_id": f"demo-{brand.id}",
                    "nome_anuncio": f"Criativo demo — {brand.nome}",
                    "thumbnail_url": None,
                    "metrica_label": f"{5 + brand.id % 20} leads (demo)",
                }
            else:
                ads = meta_client.get_top_ad(account.meta_account_id)
                brand_ads = [a for a in ads if _brand_matches_campaign(brand, a["campaign_name"])]
                brand_ads.sort(key=lambda a: a["leads"], reverse=True)
                if not brand_ads:
                    continue
                best = brand_ads[0]
                top = {
                    "ad_id": best["ad_id"],
                    "nome_anuncio": best["nome_anuncio"],
                    "thumbnail_url": best["thumbnail_url"],
                    "metrica_label": f"{best['leads']} leads",
                }
            creative_cache = db.query(TopCreativeCache).filter(TopCreativeCache.brand_id == brand.id).first()
            if not creative_cache:
                creative_cache = TopCreativeCache(brand_id=brand.id)
                db.add(creative_cache)
            creative_cache.ad_id = top["ad_id"]
            creative_cache.nome_anuncio = top["nome_anuncio"]
            creative_cache.thumbnail_url = top["thumbnail_url"]
            creative_cache.metrica_label = top["metrica_label"]
            creative_cache.atualizado_em = datetime.utcnow()

    db.commit()
    return {"contas_sincronizadas": synced_accounts, "registros_diarios": synced_days, "modo_demo": META_DEMO_MODE}


def _upsert_daily_metric(db: Session, brand_id: int, day: date, row: dict, fonte: str):
    metric = db.query(DailyMetric).filter(DailyMetric.brand_id == brand_id, DailyMetric.data == day).first()
    if not metric:
        metric = DailyMetric(brand_id=brand_id, data=day)
        db.add(metric)
    metric.gasto = row["gasto"]
    metric.leads_formulario = row["leads_formulario"]
    metric.leads_whatsapp = row["leads_whatsapp"]
    metric.fonte = fonte
