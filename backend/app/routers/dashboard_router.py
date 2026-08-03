import random
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import allowed_brand_ids, get_current_user
from app.config import GOOGLE_ADS_DEMO_MODE, META_DEMO_MODE, NBS_DEMO_MODE
from app.database import get_db
from app.models import (
    AdDailyMetric,
    Brand,
    BrandNewsItem,
    DailyMetric,
    FunilDailyMetric,
    GoogleAccountCache,
    GoogleDailyMetric,
    GoogleKeywordCache,
    GoogleLocationCache,
    InstagramStatsCache,
    InstagramTopPostCache,
    SaldoCache,
    User,
)
from app.news_client import MARCAS_CHAVE
from app.sync import SyncEmAndamento, sync_all

router = APIRouter(prefix="/api", tags=["dashboard"])


def _iso_utc(dt: datetime | None) -> str | None:
    # datetime.utcnow() devolve um horário "ingênuo" (sem fuso marcado) — sem indicar que é
    # UTC, o navegador interpretava a hora como se já fosse local e mostrava adiantado (ex.:
    # "15:16" quando na verdade eram ~12h). O "Z" no final avisa o navegador que é UTC, pra
    # ele converter certinho pro horário local de quem estiver olhando.
    return (dt.isoformat() + "Z") if dt else None


def _scoped_brands(db: Session, user: User):
    query = db.query(Brand)
    ids = allowed_brand_ids(user, db)
    if ids is not None:
        query = query.filter(Brand.id.in_(ids)) if ids else query.filter(False)
    return query.all()


def _build_noticias(db: Session, brands: list[Brand]) -> list[dict]:
    """Usada tanto no dashboard Meta Ads quanto no Informações Gerais — aparece nos dois."""
    noticias = []
    todos_itens_noticia = db.query(BrandNewsItem).order_by(BrandNewsItem.publicado_em.desc()).all()
    for b in brands:
        nome_lower = b.nome.lower()
        marcas_da_brand = [m for m in MARCAS_CHAVE if m.lower() in nome_lower]
        if not marcas_da_brand:
            continue
        itens_brand = [it for it in todos_itens_noticia if it.marca_chave in marcas_da_brand][:8]
        if not itens_brand:
            continue
        noticias.append(
            {
                "brandId": b.id,
                "marca": b.nome,
                "itens": [
                    {
                        "titulo": it.titulo,
                        "link": it.link,
                        "fonte": it.fonte,
                        "publicadoEm": it.publicado_em.isoformat() if it.publicado_em else None,
                        "thumbnailUrl": it.thumbnail_url,
                    }
                    for it in itens_brand
                ],
            }
        )
    return noticias


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

    # Desempenho dia a dia de cada anúncio — o frontend filtra pelo período selecionado
    # (Mês Atual, Mês Anterior etc.) e recalcula o top 3 na hora, igual já faz com leads e
    # gasto por dia. Não existe mais um "top 3" fixo pré-calculado aqui no backend.
    criativos_diarios = []
    if brand_ids:
        brands_by_id_c = {b.id: b for b in brands}
        ad_metrics = db.query(AdDailyMetric).filter(AdDailyMetric.brand_id.in_(brand_ids)).all()
        for m in ad_metrics:
            brand = brands_by_id_c[m.brand_id]
            criativos_diarios.append(
                {
                    "brandId": m.brand_id,
                    "marca": brand.nome,
                    "adId": m.ad_id,
                    "nomeAnuncio": m.nome_anuncio,
                    "thumbnailUrl": m.thumbnail_url,
                    "data": m.data.isoformat(),
                    "gasto": m.gasto,
                    "leads": m.leads,
                }
            )

    organico = []
    brands_by_ig_account: dict[int, list[Brand]] = {}
    for b in brands:
        if b.instagram_account_id:
            brands_by_ig_account.setdefault(b.instagram_account_id, []).append(b)
    for ig_account_id, ig_brands in brands_by_ig_account.items():
        stats = db.query(InstagramStatsCache).filter(InstagramStatsCache.instagram_account_id == ig_account_id).first()
        post = db.query(InstagramTopPostCache).filter(InstagramTopPostCache.instagram_account_id == ig_account_id).first()
        account = ig_brands[0].instagram_account
        organico.append(
            {
                "instagramAccountId": ig_account_id,
                "username": account.username,
                "nomeConta": account.nome,
                "marcas": [b.nome for b in ig_brands],
                "seguidoresAtual": stats.seguidores_atual if stats else 0,
                "seguidoresGanhosPeriodo": stats.seguidores_ganhos_periodo if stats else 0,
                "topPost": (
                    {
                        "legenda": post.legenda,
                        "thumbnailUrl": post.thumbnail_url,
                        "permalink": post.permalink,
                        "curtidas": post.curtidas,
                        "comentarios": post.comentarios,
                        "publicadoEm": post.publicado_em.isoformat() if post.publicado_em else None,
                    }
                    if post
                    else None
                ),
            }
        )

    noticias = _build_noticias(db, brands)

    last_sync = None
    if brand_ids:
        latest_cache = (
            db.query(SaldoCache)
            .filter(SaldoCache.ad_account_id.in_([b.ad_account_id for b in brands if b.ad_account_id]))
            .order_by(SaldoCache.atualizado_em.desc())
            .first()
        )
        last_sync = _iso_utc(latest_cache.atualizado_em if latest_cache else None)

    return {
        "brands": [{"id": b.id, "nome": b.nome, "tier": b.tier, "analista": b.analista} for b in brands],
        "trafego": trafego,
        "saldos": saldos,
        "criativosDiarios": criativos_diarios,
        "organico": organico,
        "noticias": noticias,
        "meta": {"lastSync": last_sync, "demoMode": META_DEMO_MODE},
    }


@router.get("/dashboard/google")
def get_google_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_brands = _scoped_brands(db, user)
    brands = [b for b in all_brands if b.google_ads_account_id]
    brand_ids = [b.id for b in brands]

    trafego = []
    if brand_ids:
        metrics = db.query(GoogleDailyMetric).filter(GoogleDailyMetric.brand_id.in_(brand_ids)).all()
        brands_by_id = {b.id: b for b in brands}
        for m in metrics:
            brand = brands_by_id[m.brand_id]
            trafego.append(
                {
                    "brandId": m.brand_id,
                    "marca": brand.nome,
                    "data": m.data.isoformat(),
                    "gasto": m.gasto,
                    "cliques": m.cliques,
                    "conversoes": m.conversoes,
                    "cpa": (m.gasto / m.conversoes) if m.conversoes else 0,
                }
            )

    mes_atual_inicio = date.today().replace(day=1)
    gasto_mes_por_marca: dict[int, float] = {}
    if brand_ids:
        rows = (
            db.query(GoogleDailyMetric)
            .filter(GoogleDailyMetric.brand_id.in_(brand_ids), GoogleDailyMetric.data >= mes_atual_inicio)
            .all()
        )
        for r in rows:
            gasto_mes_por_marca[r.brand_id] = gasto_mes_por_marca.get(r.brand_id, 0) + r.gasto

    orcamentos = []
    for b in brands:
        cache = (
            db.query(GoogleAccountCache).filter(GoogleAccountCache.google_ads_account_id == b.google_ads_account_id).first()
            if b.google_ads_account_id
            else None
        )
        gasto_mes = gasto_mes_por_marca.get(b.id, 0)
        orcamentos.append(
            {
                "brandId": b.id,
                "marca": b.nome,
                "tier": b.tier,
                "orcamentoDiario": cache.orcamento_diario if cache else 0,
                "status": cache.status if cache else "",
                "orcamentoMensal": b.google_orcamento_mensal,
                "gastoMesAtual": gasto_mes,
                "saldoAtual": b.google_orcamento_mensal - gasto_mes,
            }
        )

    top_keywords = []
    for b in brands:
        caches = (
            db.query(GoogleKeywordCache)
            .filter(GoogleKeywordCache.brand_id == b.id)
            .order_by(GoogleKeywordCache.rank)
            .all()
        )
        if caches:
            top_keywords.append(
                {
                    "brandId": b.id,
                    "marca": b.nome,
                    "palavrasChave": [
                        {
                            "rank": c.rank,
                            "texto": c.texto,
                            "gasto": c.gasto,
                            "cliques": c.cliques,
                            "conversoes": c.conversoes,
                            "custoPorConversao": (c.gasto / c.conversoes) if c.conversoes else 0,
                            "atualizadoEm": _iso_utc(c.atualizado_em),
                        }
                        for c in caches
                    ],
                }
            )

    heatmap = []
    for b in brands:
        locais = (
            db.query(GoogleLocationCache)
            .filter(GoogleLocationCache.brand_id == b.id)
            .order_by(GoogleLocationCache.conversoes.desc())
            .all()
        )
        if locais:
            heatmap.append(
                {
                    "brandId": b.id,
                    "marca": b.nome,
                    "localidades": [
                        {
                            "cidade": l.cidade,
                            "estado": l.estado,
                            "latitude": l.latitude,
                            "longitude": l.longitude,
                            "gasto": l.gasto,
                            "cliques": l.cliques,
                            "conversoes": l.conversoes,
                        }
                        for l in locais
                    ],
                }
            )

    last_sync = None
    if brand_ids:
        latest_cache = (
            db.query(GoogleAccountCache)
            .filter(GoogleAccountCache.google_ads_account_id.in_([b.google_ads_account_id for b in brands]))
            .order_by(GoogleAccountCache.atualizado_em.desc())
            .first()
        )
        last_sync = _iso_utc(latest_cache.atualizado_em if latest_cache else None)

    return {
        "brands": [{"id": b.id, "nome": b.nome, "tier": b.tier, "analista": b.analista} for b in brands],
        "trafego": trafego,
        "orcamentos": orcamentos,
        "topKeywords": top_keywords,
        "heatmap": heatmap,
        "meta": {"lastSync": last_sync, "demoMode": GOOGLE_ADS_DEMO_MODE},
    }


@router.get("/dashboard/funil")
def get_funil_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_brands = _scoped_brands(db, user)
    brands = [b for b in all_brands if b.ad_account_id]
    brand_ids = [b.id for b in brands]

    trafego = []
    if brand_ids:
        brands_by_id = {b.id: b for b in brands}
        leads_by_brand_data: dict[tuple[int, date], int] = {}
        for m in db.query(DailyMetric).filter(DailyMetric.brand_id.in_(brand_ids)).all():
            leads_by_brand_data[(m.brand_id, m.data)] = m.leads_formulario + m.leads_whatsapp

        for f in db.query(FunilDailyMetric).filter(FunilDailyMetric.brand_id.in_(brand_ids)).all():
            brand = brands_by_id[f.brand_id]
            leads = leads_by_brand_data.get((f.brand_id, f.data), 0)
            trafego.append(
                {
                    "brandId": f.brand_id,
                    "marca": brand.nome,
                    "data": f.data.isoformat(),
                    "leads": leads,
                    "propostas": f.propostas,
                    "vendas": f.vendas,
                }
            )

    noticias = _build_noticias(db, brands)

    # Estatísticas de resposta do SAC (Instagram/Facebook) — 100% demonstração por enquanto:
    # ler mensagens exige permissões do Meta (Messaging API) que ainda não pedimos/aprovamos,
    # é uma categoria de acesso diferente da que já temos pra anúncios. Quando integrarmos de
    # verdade, isso passa a ser sincronizado igual ao resto (ver sync.py).
    sac_stats = []
    for b in brands:
        rng = random.Random(b.id * 211)
        base_msgs = 3 + (b.id * 7) % 15
        for i in range(35):
            day = date.today() - timedelta(days=i)
            dow = day.weekday()
            fator_fds = 0.5 if dow >= 5 else 1.0
            recebidas = max(0, round(base_msgs * fator_fds * (0.6 + rng.random() * 0.8)))
            respondidas = min(recebidas, round(recebidas * (0.75 + rng.random() * 0.24)))
            tempo_resposta_min = round(5 + rng.random() * 55, 1)
            sac_stats.append(
                {
                    "brandId": b.id,
                    "marca": b.nome,
                    "data": day.isoformat(),
                    "recebidas": recebidas,
                    "respondidas": respondidas,
                    "tempoRespostaMin": tempo_resposta_min,
                }
            )

    last_sync = None
    if brand_ids:
        latest = (
            db.query(FunilDailyMetric)
            .filter(FunilDailyMetric.brand_id.in_(brand_ids))
            .order_by(FunilDailyMetric.data.desc())
            .first()
        )
        last_sync = latest.data.isoformat() if latest else None

    return {
        "brands": [{"id": b.id, "nome": b.nome, "tier": b.tier, "analista": b.analista} for b in brands],
        "trafego": trafego,
        "noticias": noticias,
        "sacStats": sac_stats,
        "meta": {"lastSync": last_sync, "demoMode": NBS_DEMO_MODE, "sacDemoMode": True},
    }


@router.post("/sync")
def trigger_sync(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        result = sync_all(db)
    except SyncEmAndamento:
        raise HTTPException(status_code=409, detail="Já existe uma sincronização em andamento. Aguarde ela terminar.")
    return {**result, "sincronizadoEm": datetime.utcnow().isoformat()}
