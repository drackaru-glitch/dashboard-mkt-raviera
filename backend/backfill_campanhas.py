"""Carga única do histórico por campanha (24/06 até hoje). Idempotente: rodar de novo só reescreve
as mesmas linhas. Usa a mesma contagem de lead da sincronização (_contar_leads + destino do conjunto)."""
import sys
from datetime import date

from app import meta_client
from app.database import SessionLocal
from app.models import AdAccount, Brand
from app import historico
from app.sync import _brand_matches_campaign

DESDE = "2026-06-24"


def insights_paginado(conta: str, destinos: dict) -> list[dict]:
    rows, after = [], None
    while True:
        params = {
            "level": "campaign", "time_increment": 1,
            "time_range": f'{{"since":"{DESDE}","until":"{date.today().isoformat()}"}}',
            "fields": "date_start,campaign_id,campaign_name,spend,actions", "limit": 500,
        }
        if after:
            params["after"] = after
        data = meta_client._get(f"{conta}/insights", params, conta)
        for e in data.get("data", []):
            conversa = e.get("campaign_name") in destinos
            form, whats = meta_client._contar_leads(e.get("actions"), conversa)
            rows.append({
                "data": e["date_start"], "campaign_id": e.get("campaign_id"),
                "campaign_name": e.get("campaign_name", ""), "gasto": float(e.get("spend", 0) or 0),
                "leads_formulario": form, "leads_whatsapp": whats,
            })
        paging = data.get("paging", {})
        if not paging.get("next"):
            return rows
        after = paging.get("cursors", {}).get("after")


db = SessionLocal()
total = 0
for acc in db.query(AdAccount).order_by(AdAccount.nome).all():
    marcas = db.query(Brand).filter(Brand.ad_account_id == acc.id).all()
    if not marcas:
        continue
    try:
        destinos = meta_client.get_campaign_destinos(acc.meta_account_id)
        rows = insights_paginado(acc.meta_account_id, destinos)
        n = historico.gravar_campanhas_conta(db, marcas, rows, destinos, {}, _brand_matches_campaign)
        db.commit()
        total += n
        print(f"OK   {acc.nome}: {len(rows)} linhas buscadas, {n} gravadas", flush=True)
    except Exception as e:
        db.rollback()
        print(f"FALHA {acc.nome}: {str(e)[:150]}", flush=True)
print("TOTAL gravado:", total, flush=True)
db.close()
