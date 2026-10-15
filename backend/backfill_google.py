"""Carga única do Google Ads depois de renovar o token (a sincronização ficou parada de 04/08 a
02/10/2026). Idempotente: rodar de novo só reescreve as mesmas linhas.

1) Gasto/cliques/conversões DIÁRIOS dos últimos DIAS dias, por conta — só isso usa a janela
   grande. Palavras-chave, localidades e anúncios continuam sendo "os últimos 7 dias" (a janela
   da sincronização normal), por isso o passo 2 roda a sincronização padrão, não uma janela maior.
2) Sincronização normal do Google (orçamento, palavras-chave, localidades, top anúncios).

Uso, de dentro de backend/:  .venv\\Scripts\\python.exe backfill_google.py
"""

from datetime import date, datetime

from app import google_ads_client
from app.database import SessionLocal
from app.models import Brand, GoogleAdsAccount, GoogleDailyMetric
from app.sync import DIAS_JANELA_SYNC_PADRAO, _sync_google_ads, _upsert_google_daily_metric

DIAS = 75


def main():
    db = SessionLocal()
    try:
        marcas_por_conta = {}
        for b in db.query(Brand).filter(Brand.google_ads_account_id.isnot(None)).all():
            marcas_por_conta.setdefault(b.google_ads_account_id, []).append(b)

        total_linhas = 0
        for conta in db.query(GoogleAdsAccount).order_by(GoogleAdsAccount.id):
            marcas = marcas_por_conta.get(conta.id)
            if not marcas:
                continue
            # 1 conta = 1 marca (ver _sync_google_account) — os dados da conta são todos dela.
            marca = marcas[0]
            try:
                linhas = google_ads_client.get_daily_metrics(conta.customer_id, days=DIAS)
            except Exception as exc:
                print(f"  FALHOU {conta.nome}: {exc}")
                continue
            for row in linhas:
                dia = datetime.strptime(row["data"], "%Y-%m-%d").date()
                _upsert_google_daily_metric(db, marca.id, dia, row)
            db.commit()
            total_linhas += len(linhas)
            print(f"  OK     {conta.nome}: {len(linhas)} dias com dado")

        print(f"\nPasso 1 concluído: {total_linhas} linhas de métricas diárias gravadas/atualizadas.")
        print(f"Passo 2: sincronização normal do Google (janela de {DIAS_JANELA_SYNC_PADRAO} dias)...")
        contas_ok = _sync_google_ads(db, DIAS_JANELA_SYNC_PADRAO)
        db.commit()
        print(f"Passo 2 concluído: {contas_ok} contas sincronizadas.")

        hoje = date.today()
        ultimo = db.query(GoogleDailyMetric.data).order_by(GoogleDailyMetric.data.desc()).first()
        print(f"\nDado mais recente do Google no banco agora: {ultimo[0] if ultimo else '—'} (hoje é {hoje}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
