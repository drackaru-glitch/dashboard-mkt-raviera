"""Rotina da manha: puxa o Meta Ads e imprime Data | Marca | CPL | Quantidade | Gasto.

Reusa o backend do Raviera Cockpit (meta_client + _upsert_daily_metric), mas busca apenas os
insights diarios -- sem saldo, criativos, Instagram, Google Ads ou noticias, que nao entram
nessa rotina.

Uso:
    python manha.py                # ontem
    python manha.py 2026-08-26     # dia especifico
    python manha.py --sem-sync     # so le o banco, nao chama a API
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

BACKEND = r"C:\Users\Raviera\dashboard-mkt-raviera\backend"
os.chdir(BACKEND)  # DATABASE_URL padrao e sqlite:///./raviera.db (caminho relativo)
sys.path.insert(0, BACKEND)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import meta_client, sync  # noqa: E402
from app.config import META_DEMO_MODE  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AdAccount, Brand, DailyMetric  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
fazer_sync = "--sem-sync" not in sys.argv
alvo = datetime.strptime(args[0], "%Y-%m-%d").date() if args else date.today() - timedelta(days=1)

# Janela curta: cobre o dia alvo e deixa margem pro Meta reprocessar o dia anterior.
DIAS = max((date.today() - alvo).days + 2, 2)

db = SessionLocal()


def sincronizar():
    if META_DEMO_MODE:
        print("META_ACCESS_TOKEN nao configurado -- o cockpit esta em modo demo.")
        print("Os numeros abaixo NAO sao reais. Rode com o .env do backend carregado.\n")
        return

    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all()
    accounts = {a.id: a for a in db.query(AdAccount).all()}
    por_conta: dict[int, list[Brand]] = {}
    for b in brands:
        if b.ad_account_id in accounts:
            por_conta.setdefault(b.ad_account_id, []).append(b)

    resultados: dict[int, list[dict]] = {}
    falhas: list[str] = []
    with ThreadPoolExecutor(max_workers=sync.MAX_FETCH_WORKERS) as ex:
        futures = {
            ex.submit(meta_client.get_daily_insights, accounts[aid].meta_account_id, DIAS): aid
            for aid in por_conta
        }
        for fut in as_completed(futures):
            aid = futures[fut]
            try:
                resultados[aid] = fut.result()
            except Exception as exc:
                falhas.append(f"{accounts[aid].nome}: {exc}")

    gravados = 0
    for aid, marcas in por_conta.items():
        insights = resultados.get(aid)
        if insights is None:
            continue
        for brand in marcas:
            por_dia: dict[str, dict] = {}
            for r in insights:
                if not sync._brand_matches_campaign(brand, r["campaign_name"]):
                    continue
                d = por_dia.setdefault(r["data"], {"gasto": 0.0, "leads_formulario": 0, "leads_whatsapp": 0})
                d["gasto"] += r["gasto"]
                d["leads_formulario"] += r["leads_formulario"]
                d["leads_whatsapp"] += r["leads_whatsapp"]
            for data_str, row in por_dia.items():
                dia = datetime.strptime(data_str, "%Y-%m-%d").date()
                sync._upsert_daily_metric(db, brand.id, dia, row, fonte="meta")
                gravados += 1
    db.commit()

    print(f"Sincronizado: {len(resultados)}/{len(por_conta)} contas, {gravados} registros diarios.")
    for f in falhas:
        print(f"  FALHA -> {f}")
    print()


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def relatorio():
    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).order_by(Brand.nome).all()
    if not brands:
        print("Nenhuma marca vinculada a conta de anuncio.")
        return

    metricas = {dm.brand_id: dm for dm in db.query(DailyMetric).filter(DailyMetric.data == alvo).all()}

    larg = max(len(b.nome) for b in brands)
    dia = alvo.strftime("%d/%m/%Y")
    print(f"{'Data':<10}  {'Marca':<{larg}}  {'CPL':>12}  {'Qtd':>5}  {'Gasto':>14}")
    print("-" * (10 + larg + 42))

    tot_gasto = tot_leads = 0
    for brand in brands:
        dm = metricas.get(brand.id)
        gasto = dm.gasto or 0 if dm else 0
        leads = ((dm.leads_formulario or 0) + (dm.leads_whatsapp or 0)) if dm else 0
        tot_gasto += gasto
        tot_leads += leads
        cpl = brl(gasto / leads) if leads else "-"
        nome = brand.nome + (" *" if dm and dm.fonte == "manual" else "")
        print(f"{dia:<10}  {nome:<{larg}}  {cpl:>12}  {leads:>5}  {brl(gasto):>14}")

    print("-" * (10 + larg + 42))
    cpl_total = brl(tot_gasto / tot_leads) if tot_leads else "-"
    print(f"{'TOTAL':<10}  {'':<{larg}}  {cpl_total:>12}  {tot_leads:>5}  {brl(tot_gasto):>14}")
    if any(dm.fonte == "manual" for dm in metricas.values()):
        print("\n* = linha ajustada manualmente, a sincronizacao nao sobrescreve")


try:
    if fazer_sync:
        sincronizar()
    relatorio()
finally:
    db.close()
