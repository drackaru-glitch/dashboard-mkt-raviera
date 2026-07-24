from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import allowed_brand_ids, get_current_user
from app.config import META_DEMO_MODE
from app.database import get_db
from app.models import Brand, DailyMetric, SaldoCache, TopCreativeCache, User
from app.sync import sync_all

router = APIRouter(prefix="/api", tags=["dashboard"])


def _scoped_brands(db: Session, user: User):
    query = db.query(Brand)
    ids = allowed_brand_ids(user, db)
    if ids is not None:
        query = query.filter(Brand.id.in_(ids)) if ids else query.filter(False)
    return query.all()


@router.get("/dashboard")
def get_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    brands = _scoped_brands(db, user)
    brand_ids = [b.id for b in brands]

    trafego = []
    if brand_ids:
        metrics = db.query(DailyMetric).filter(DailyMetric.brand_id.in_(brand_ids)).all()
        brands_by_id = {b.id: b for b in brands}
        for m in metrics:
            brand = brands_by_id[m.brand_id]
            quantidade = m.leads_formulario + m.leads_whatsapp
            trafego.append(
                {
                    "brandId": m.brand_id,
                    "marca": brand.nome,
                    "data": m.data.isoformat(),
                    "gasto": m.gasto,
                    "quantidade": quantidade,
                    "leadsFormulario": m.leads_formulario,
                    "leadsWhatsapp": m.leads_whatsapp,
                    "cpl": (m.gasto / quantidade) if quantidade else 0,
                }
            )

    saldos = []
    for b in brands:
        cache = (
            db.query(SaldoCache).filter(SaldoCache.ad_account_id == b.ad_account_id).first()
            if b.ad_account_id
            else None
        )
        saldos.append(
            {
                "brandId": b.id,
                "marca": b.nome,
                "tier": b.tier,
                "saldoAtualMeta": cache.saldo if cache else 0,
                "moeda": cache.moeda if cache else "BRL",
                "diarioEsperadoMeta": b.diario_esperado_meta,
            }
        )

    top_creatives = []
    for b in brands:
        cache = db.query(TopCreativeCache).filter(TopCreativeCache.brand_id == b.id).first()
        if cache:
            top_creatives.append(
                {
                    "brandId": b.id,
                    "marca": b.nome,
                    "nomeAnuncio": cache.nome_anuncio,
                    "thumbnailUrl": cache.thumbnail_url,
                    "metricaLabel": cache.metrica_label,
                    "atualizadoEm": cache.atualizado_em.isoformat() if cache.atualizado_em else None,
                }
            )

    last_sync = None
    if brand_ids:
        latest_cache = (
            db.query(SaldoCache)
            .filter(SaldoCache.ad_account_id.in_([b.ad_account_id for b in brands if b.ad_account_id]))
            .order_by(SaldoCache.atualizado_em.desc())
            .first()
        )
        last_sync = latest_cache.atualizado_em.isoformat() if latest_cache else None

    return {
        "brands": [{"id": b.id, "nome": b.nome, "tier": b.tier} for b in brands],
        "trafego": trafego,
        "saldos": saldos,
        "topCreatives": top_creatives,
        "meta": {"lastSync": last_sync, "demoMode": META_DEMO_MODE},
    }


@router.post("/sync")
def trigger_sync(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = sync_all(db)
    return {**result, "sincronizadoEm": datetime.utcnow().isoformat()}
