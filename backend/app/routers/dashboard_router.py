import random
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import allowed_brand_ids, get_current_user
from app.config import (
    ANALISTAS_REDES_SOCIAIS,
    GOOGLE_ADS_DEMO_MODE,
    META_DEMO_MODE,
    NBS_DEMO_MODE,
)
from app.database import get_db
from app.models import (
    AdDailyMetric,
    Brand,
    BrandNewsItem,
    ComentarioSocial,
    DailyMetric,
    FunilDailyMetric,
    GoogleAccountCache,
    GoogleDailyMetric,
    GoogleKeywordCache,
    GoogleLocationCache,
    InstagramFollowerDaily,
    InstagramPostMetric,
    InstagramStatsCache,
    MetaCidadeCache,
    MetaRegionCache,
    SaldoCache,
    TokenSaude,
    User,
)
from app.news_client import MARCAS_CHAVE
from app import concorrentes as concorrentes_svc
from app import notificacoes, runway
from app.sync import SyncEmAndamento, sync_all

if not META_DEMO_MODE:
    from app import meta_client

router = APIRouter(prefix="/api", tags=["dashboard"])

# Com quantos dias de antecedência o painel começa a avisar que um token vai vencer. Trinta
# dá tempo de agendar a troca sem correria — e é bem mais do que o zero de aviso que a gente
# tinha antes, quando o jeito de descobrir era o painel zerar.
AVISO_TOKEN_DIAS = 30


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


def _saude_tokens(db: Session) -> dict:
    """Resumo do prazo dos tokens do Meta, pro painel avisar ANTES de parar de funcionar.

    Só devolve o que precisa de atenção. Token de usuário do sistema não vence, então em
    condição saudável isto volta vazio e a tela não mostra nada.
    """
    agora = datetime.utcnow()
    itens = []
    for t in db.query(TokenSaude).all():
        # O prazo que mata é o de acesso aos dados; `expira_em` quase sempre é nulo.
        prazo = t.acesso_expira_em or t.expira_em
        dias = (prazo - agora).days if prazo else None
        if t.valido and (dias is None or dias > AVISO_TOKEN_DIAS):
            continue
        itens.append(
            {
                "rotulo": t.rotulo,
                "tipo": t.tipo,
                "valido": t.valido,
                "diasRestantes": dias,
                "expiraEm": _iso_utc(prazo),
                "erro": t.erro,
            }
        )
    # Quem vence primeiro (ou já morreu) aparece na frente.
    itens.sort(key=lambda i: (i["valido"], i["diasRestantes"] if i["diasRestantes"] is not None else 9999))
    # Tokens diferentes com a mesma data são um só problema (os do Instagram vencem todos
    # juntos porque saíram do mesmo token de usuário) — a tela mostra o total, não 23 avisos.
    return {"alertas": itens, "total": len(itens)}


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

    # Gasto real médio dos últimos dias FECHADOS por marca — mesmo cálculo do alerta de
    # orçamento subutilizado (notificacoes.py), reaproveitado aqui pra reforçar visualmente a
    # tabela de Saldo sem esperar a notificação (e pra quem já tiver lido/dispensado o alerta).
    # Reaproveita `metrics` (já buscado acima pro gráfico) — nenhuma query extra.
    ontem = date.today() - timedelta(days=1)
    inicio_janela_orc = ontem - timedelta(days=notificacoes.ORCAMENTO_SUBUTILIZADO_DIAS - 1)
    gastos_recentes_por_marca: dict[int, list[float]] = {}
    if brand_ids:
        for m in metrics:
            if inicio_janela_orc <= m.data <= ontem:
                gastos_recentes_por_marca.setdefault(m.brand_id, []).append(m.gasto)

    # Fôlego por CONTA (saldo é da conta, não da marca — ver app.runway). Calculado com todas as
    # marcas da conta, mesmo as que este usuário não enxerga; pra ele só sai a contagem delas.
    runway_por_conta = runway.calcular_por_conta(db)
    nomes_visiveis = {b.id for b in brands}

    saldos = []
    for b in brands:
        conta_runway = runway_por_conta.get(b.ad_account_id) if b.ad_account_id else None
        cache = (
            db.query(SaldoCache).filter(SaldoCache.ad_account_id == b.ad_account_id).first()
            if b.ad_account_id
            else None
        )
        gastos_recentes = gastos_recentes_por_marca.get(b.id, [])
        gasto_medio_recente = sum(gastos_recentes) / len(gastos_recentes) if gastos_recentes else None
        orcamento_subutilizado = (
            b.orcamento_diario_real is not None
            and b.orcamento_diario_real >= notificacoes.ORCAMENTO_SUBUTILIZADO_MINIMO
            and gasto_medio_recente is not None
            and len(gastos_recentes) >= notificacoes.ORCAMENTO_SUBUTILIZADO_DIAS
            and (gasto_medio_recente / b.orcamento_diario_real) <= notificacoes.LIMIAR_ORCAMENTO_SUBUTILIZADO
        )
        # Link direto pra tela de Nota Fiscal no Gerenciador de Negócios — só existe quando os
        # 3 IDs foram preenchidos à mão em Configuração (não tem como buscar isso pela API, ver
        # comentário em app.models.AdAccount). Conta compartilhada entre marcas (ex.: BMW Auto +
        # Motorrad + MINI) tem o mesmo link pras três, porque a nota fiscal é da conta inteira.
        conta = b.ad_account
        nota_fiscal_url = None
        if conta and conta.billing_business_id and conta.billing_asset_id and conta.billing_payment_account_id:
            nota_fiscal_url = (
                "https://adsmanager.facebook.com/adsmanager/billing_hub/payment_activity"
                f"?asset_id={conta.billing_asset_id}&business_id={conta.billing_business_id}"
                f"&payment_account_id={conta.billing_payment_account_id}&placement=BILLING_HUB&entity=NotaFiscal"
            )
        saldos.append(
            {
                "brandId": b.id,
                "marca": b.nome,
                "saldoAtualMeta": cache.saldo if cache else 0,
                "moeda": cache.moeda if cache else "BRL",
                "diarioEsperadoMeta": b.diario_esperado_meta,  # planejado (digitado à mão)
                "orcamentoDiarioReal": b.orcamento_diario_real,  # real (lido do Meta na sincronização)
                "orcamentoAtualizadoEm": _iso_utc(b.orcamento_atualizado_em),
                "gastoMedioRecente": gasto_medio_recente,
                "orcamentoSubutilizado": orcamento_subutilizado,
                "notaFiscalUrl": nota_fiscal_url,
                "contaId": b.ad_account_id,
                "marcasNaConta": (
                    [m.nome for m in conta_runway["marcas"] if m.id in nomes_visiveis and m.id != b.id]
                    if conta_runway else []
                ),
                "totalMarcasNaConta": len(conta_runway["marcas"]) if conta_runway else 1,
                "runwayDias": conta_runway["dias"] if conta_runway else None,
                "ritmoDiario": conta_runway["ritmo"] if conta_runway else None,
                "ritmoFonte": conta_runway["ritmoFonte"] if conta_runway else None,
                "faltaAteFimDoMes": conta_runway["faltaAteFimDoMes"] if conta_runway else 0,
                "diasAteFimDoMes": conta_runway["diasAteFimDoMes"] if conta_runway else 0,
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
    # Conteúdo orgânico sai daqui em bruto — publicações com data e ganho de seguidores dia a
    # dia — e é a tela que recorta pelo período selecionado, igual ao tráfego e aos criativos.
    # Antes daqui saía um "melhor post" já escolhido e um total fixo de 30 dias corridos: o
    # card mostrava, em setembro, publicação de maio, e não mudava ao trocar o filtro.
    if brands_by_ig_account:
        ids_contas = list(brands_by_ig_account)
        stats_por_conta = {
            s.instagram_account_id: s
            for s in db.query(InstagramStatsCache)
            .filter(InstagramStatsCache.instagram_account_id.in_(ids_contas))
            .all()
        }
        ganhos_por_conta: dict[int, list[dict]] = {}
        for linha in (
            db.query(InstagramFollowerDaily)
            .filter(InstagramFollowerDaily.instagram_account_id.in_(ids_contas))
            .all()
        ):
            ganhos_por_conta.setdefault(linha.instagram_account_id, []).append(
                {"data": linha.data.isoformat(), "ganho": linha.ganho}
            )
        posts_por_conta: dict[int, list[dict]] = {}
        for p in (
            db.query(InstagramPostMetric)
            .filter(InstagramPostMetric.instagram_account_id.in_(ids_contas))
            .order_by(InstagramPostMetric.publicado_em.desc())
            .all()
        ):
            posts_por_conta.setdefault(p.instagram_account_id, []).append(
                {
                    "postId": p.post_id,
                    "legenda": p.legenda,
                    "thumbnailUrl": p.thumbnail_url,
                    "permalink": p.permalink,
                    "curtidas": p.curtidas,
                    "comentarios": p.comentarios,
                    "publicadoEm": p.publicado_em.isoformat() if p.publicado_em else None,
                }
            )

        for ig_account_id, ig_brands in brands_by_ig_account.items():
            stats = stats_por_conta.get(ig_account_id)
            account = ig_brands[0].instagram_account
            organico.append(
                {
                    "instagramAccountId": ig_account_id,
                    "username": account.username,
                    "nomeConta": account.nome,
                    "marcas": [b.nome for b in ig_brands],
                    "seguidoresAtual": stats.seguidores_atual if stats else 0,
                    "ganhosDiarios": ganhos_por_conta.get(ig_account_id, []),
                    "posts": posts_por_conta.get(ig_account_id, []),
                }
            )

    noticias = _build_noticias(db, brands)

    # Mapa de calor por estado — nível mais fino de localização REAL que o Meta expõe (ver
    # meta_client.get_region_performance). Só a lista de cidades configuradas por estado vem
    # aqui; os nomes das cidades em si só são buscados ao abrir (endpoint /cidades-configuradas
    # abaixo), pra não mandar isso tudo em toda carga do painel à toa.
    mapa_calor_meta = []
    if brand_ids:
        regioes_por_marca: dict[int, list] = {}
        for r in db.query(MetaRegionCache).filter(MetaRegionCache.brand_id.in_(brand_ids)).all():
            regioes_por_marca.setdefault(r.brand_id, []).append(r)
        for b in brands:
            regioes = regioes_por_marca.get(b.id)
            if not regioes:
                continue
            # Segmentação ampla (ou remarketing) sempre alcança uns centavos de gasto em
            # estado nenhum a ver com o negócio — mostrar isso como "estado" no mapa é ruído,
            # não sinal. Só entra estado com gasto de verdade OU que gerou lead de verdade.
            relevantes = [r for r in regioes if r.gasto >= 10 or r.leads > 0]
            relevantes.sort(key=lambda r: (r.leads, r.gasto), reverse=True)
            if not relevantes:
                continue
            mapa_calor_meta.append(
                {
                    "brandId": b.id,
                    "marca": b.nome,
                    "regioes": [{"regiao": r.regiao, "gasto": r.gasto, "leads": r.leads} for r in relevantes[:8]],
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
        last_sync = _iso_utc(latest_cache.atualizado_em if latest_cache else None)

    return {
        "brands": [{"id": b.id, "nome": b.nome, "analista": b.analista} for b in brands],
        "trafego": trafego,
        "saldos": saldos,
        "criativosDiarios": criativos_diarios,
        "organico": organico,
        "noticias": noticias,
        "mapaCalorMeta": mapa_calor_meta,
        "meta": {"lastSync": last_sync, "demoMode": META_DEMO_MODE, "tokens": _saude_tokens(db)},
    }


@router.get("/cidades-configuradas")
def cidades_configuradas(
    brand_id: int, regiao: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Cidades que os conjuntos ativos dessa marca têm configuradas como segmentação nesse
    estado — aberto ao clicar num estado do mapa de calor. Buscado sob demanda (não vai junto
    com /dashboard) porque só interessa quando alguém realmente clica."""
    ids_permitidas = allowed_brand_ids(user, db)
    if ids_permitidas is not None and brand_id not in ids_permitidas:
        raise HTTPException(status_code=403, detail="Sem acesso a essa marca.")
    cidades = (
        db.query(MetaCidadeCache)
        .filter(MetaCidadeCache.brand_id == brand_id, MetaCidadeCache.regiao == regiao)
        .order_by(MetaCidadeCache.cidade)
        .all()
    )
    return {"cidades": [c.cidade for c in cidades]}


def _ad_account_da_marca(db: Session, user: User, brand_id: int) -> Brand:
    ids_permitidas = allowed_brand_ids(user, db)
    if ids_permitidas is not None and brand_id not in ids_permitidas:
        raise HTTPException(status_code=403, detail="Sem acesso a essa marca.")
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand or not brand.ad_account:
        raise HTTPException(status_code=404, detail="Marca sem conta de anúncio do Meta configurada.")
    return brand


@router.get("/campanhas")
def listar_campanhas(brand_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Campanhas ativas dessa marca — alimenta o seletor do diagnóstico de campanha (ver
    /campanhas/diagnostico). Buscado ao vivo, não fica em cache: só interessa quando alguém
    abre o diagnóstico, não vale a pena sincronizar isso pra todas as marcas todo dia."""
    brand = _ad_account_da_marca(db, user, brand_id)
    if META_DEMO_MODE:
        return {"campanhas": [], "modoDemo": True}
    campanhas = meta_client.get_active_campaign_names(brand.ad_account.meta_account_id, brand.campaign_filter)
    return {"campanhas": campanhas, "modoDemo": False}


@router.get("/campanhas/diagnostico")
def diagnostico_campanha(
    brand_id: int, campanha: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Diagnóstico ao vivo de uma campanha específica: orçamento/público/fase de aprendizado
    de cada conjunto, desempenho diário e ranking de anúncios. Ver app.meta_client.get_campaign_diagnostico."""
    brand = _ad_account_da_marca(db, user, brand_id)
    if META_DEMO_MODE:
        raise HTTPException(status_code=400, detail="Diagnóstico de campanha não está disponível em modo demonstração.")
    try:
        return meta_client.get_campaign_diagnostico(brand.ad_account.meta_account_id, campanha)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


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
        "brands": [{"id": b.id, "nome": b.nome, "analista": b.analista} for b in brands],
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
        "brands": [{"id": b.id, "nome": b.nome, "analista": b.analista} for b in brands],
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


@router.get("/redes-sociais")
def dados_redes_sociais(
    dias: int = 30,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dados da aba Redes Sociais: o que chegou nas redes das marcas e o que ainda não foi
    respondido, por marca e por analista.

    Hoje isso é comentário em post do Instagram. Mensagem direta (Direct/Messenger) depende
    de App Review do Meta que ainda não foi aprovado — `canais` diz o que está ligado, pra
    tela não fingir que a caixa de entrada inteira está aqui.
    """
    dias = max(1, min(dias, 90))
    brands = _scoped_brands(db, user)
    brands_by_id = {b.id: b for b in brands}
    if not brands_by_id:
        return {"marcas": [], "comentarios": [], "resumo": {}, "canais": {}, "periodoDias": dias}

    corte = datetime.utcnow() - timedelta(days=dias)
    comentarios = (
        db.query(ComentarioSocial)
        .filter(
            ComentarioSocial.brand_id.in_(list(brands_by_id)),
            ComentarioSocial.criado_em >= corte,
        )
        .order_by(ComentarioSocial.criado_em.desc())
        .all()
    )

    agora = datetime.utcnow()
    itens = []
    por_marca: dict[int, dict] = {}
    for c in comentarios:
        brand = brands_by_id[c.brand_id]
        espera_h = None
        if c.respondido and c.respondido_em and c.criado_em:
            espera_h = max((c.respondido_em - c.criado_em).total_seconds() / 3600, 0)
        elif not c.respondido and c.criado_em:
            espera_h = max((agora - c.criado_em).total_seconds() / 3600, 0)

        itens.append(
            {
                "id": c.id,
                "brandId": brand.id,
                "marca": brand.nome,
                "analista": brand.analista or "",
                "canal": c.canal,
                "autor": c.autor,
                "texto": c.texto,
                "permalink": c.permalink,
                "legendaPost": c.legenda_post,
                "criadoEm": _iso_utc(c.criado_em),
                "respondido": bool(c.respondido),
                "respondidoEm": _iso_utc(c.respondido_em),
                "horasEspera": round(espera_h, 1) if espera_h is not None else None,
            }
        )

        agg = por_marca.setdefault(
            brand.id,
            {
                "brandId": brand.id,
                "marca": brand.nome,
                "analista": brand.analista or "",
                "recebidos": 0,
                "respondidos": 0,
                "pendentes": 0,
                "_somaResposta": 0.0,
                "ultimoEm": None,
            },
        )
        agg["recebidos"] += 1
        if c.respondido:
            agg["respondidos"] += 1
            if espera_h is not None:
                agg["_somaResposta"] += espera_h
        else:
            agg["pendentes"] += 1
        iso = _iso_utc(c.criado_em)
        if iso and (agg["ultimoEm"] is None or iso > agg["ultimoEm"]):
            agg["ultimoEm"] = iso

    marcas = []
    for agg in por_marca.values():
        respondidos = agg.pop("_somaResposta")
        marcas.append(
            {
                **agg,
                "taxaResposta": round(100 * agg["respondidos"] / agg["recebidos"], 1) if agg["recebidos"] else 0.0,
                "tempoMedioRespostaH": round(respondidos / agg["respondidos"], 1) if agg["respondidos"] else None,
            }
        )
    # Mais pendência primeiro: a aba existe pra mostrar o que está parado esperando resposta.
    marcas.sort(key=lambda m: (-m["pendentes"], -m["recebidos"]))

    total = len(itens)
    respondidos = sum(1 for m in itens if m["respondido"])
    tempos = [m["horasEspera"] for m in itens if m["respondido"] and m["horasEspera"] is not None]

    return {
        "marcas": marcas,
        "comentarios": itens,
        "resumo": {
            "recebidos": total,
            "respondidos": respondidos,
            "pendentes": total - respondidos,
            "taxaResposta": round(100 * respondidos / total, 1) if total else 0.0,
            "tempoMedioRespostaH": round(sum(tempos) / len(tempos), 1) if tempos else None,
        },
        "canais": {
            # Ligado = já traz dado real. Desligado = depende de App Review do Meta.
            "instagramComentario": not META_DEMO_MODE,
            "instagramDireto": False,
            "messenger": False,
        },
        "analistasColetados": sorted(ANALISTAS_REDES_SOCIAIS),
        "periodoDias": dias,
    }


@router.get("/concorrentes")
def dados_concorrentes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dados da aba Insights da Concorrência. Não é filtrado por marca do usuário: o que está
    aqui é anúncio público de concorrente, não dado de conta nossa."""
    return concorrentes_svc.montar_visao(db)
