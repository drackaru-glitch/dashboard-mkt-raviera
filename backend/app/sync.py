"""Sincroniza saldo, gasto/leads diários e melhor criativo com o Meta (ou gera dados de
demonstração quando META_DEMO_MODE está ativo). Chamado pelo endpoint /api/sync.
"""

import hashlib
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import (
    ANALISTAS_REDES_SOCIAIS,
    DATABASE_URL,
    GOOGLE_ADS_DEMO_MODE,
    META_ACCESS_TOKEN,
    META_ACCESS_TOKEN_PESSOAL,
    META_API_VERSION,
    META_DEMO_MODE,
    NBS_DEMO_MODE,
    REDES_SOCIAIS_DIAS_RECONFERIR,
    REDES_SOCIAIS_DIAS_RETENCAO,
)
from app.database import SessionLocal
from app.models import (
    AdAccount,
    AdDailyMetric,
    Brand,
    BrandNewsItem,
    ComentarioSocial,
    DailyMetric,
    FunilDailyMetric,
    GoogleAccountCache,
    GoogleAdsAccount,
    GoogleDailyMetric,
    GoogleKeywordCache,
    GoogleLocationCache,
    GoogleTopAdCache,
    InstagramAccount,
    InstagramFollowerDaily,
    InstagramPostMetric,
    InstagramStatsCache,
    MediaComentarioCache,
    MetaCidadeCache,
    MetaRegionCache,
    SaldoCache,
    TokenSaude,
)

if not META_DEMO_MODE:
    from app import meta_client

if not GOOGLE_ADS_DEMO_MODE:
    from app import google_ads_client

from app import geocoding, historico, instagram_client, media_cache, news_client, notificacoes, social_client

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

# Por quantos dias pra trás guardamos a miniatura das publicações do Instagram. O painel
# compara mês atual com mês anterior, então 70 dias cobrem qualquer combinação sem baixar as
# 50 publicações de cada uma das 23 contas a cada sincronização.
DIAS_MINIATURA_INSTAGRAM = 70

# Se uma sincronização ficar "presa" por mais que isso (algo travou sem lançar exceção),
# a próxima tentativa destrava e segue em vez de bloquear pra sempre com a mensagem de
# "já tem uma sincronização em andamento".
SYNC_TIMEOUT_SECONDS = 15 * 60


class SyncEmAndamento(Exception):
    """Já existe uma sincronização em andamento neste processo."""


def _brand_matches_campaign(brand: Brand, campaign_name: str) -> bool:
    # Mesma regra duplicada em meta_client._campaign_matches_filter (import circular impede
    # reaproveitar uma só função) — mudou aqui, muda lá também.
    if not brand.campaign_filter:
        return True  # conta não compartilhada: tudo pertence à única marca
    if brand.campaign_filter.startswith("!"):
        # Exclusão: a conta é "dedicada" a essa marca, mas tem uma campanha de outra
        # cidade/marca misturada por engano (aconteceu com Jeep Ji-Paraná/Porto Velho) — em
        # vez de exigir um prefixo exato (os nomes reais nem sempre são consistentes), só
        # exclui quem contém o texto informado e aceita todo o resto da conta.
        return brand.campaign_filter[1:].lower() not in campaign_name.lower()
    # Contém, não "começa com": só prefixo quebrou silenciosamente pra BMW Auto Dourados e
    # Motorrad Dourados quando quem gerencia as campanhas da Dourados mudou a convenção de
    # nome (deixou de por "BMW AUTO DDS"/"MOTORRAD DDS" logo no início) — 46 dias (01/08 a
    # 15/09/2026) com investimento, lead, orçamento real, mapa de calor e criativo ZERADOS
    # pras duas marcas, sem erro nenhum aparecer, porque a query só filtrava e não sobrava
    # nada. As outras 6 marcas que dividem conta já batiam com prefixo OU substring (uma
    # implica a outra), então essa troca não muda nada pra elas.
    return brand.campaign_filter.lower() in campaign_name.lower()


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
    """Faz as chamadas à API do Meta de uma conta. Só rede, sem tocar no banco — é isso
    que permite buscar várias contas ao mesmo tempo mesmo usando SQLite."""
    # Precisa vir antes das duas de baixo: sem saber que campanha manda pra WhatsApp/Messenger
    # (destination_type do conjunto, não o objetivo da campanha — ver meta_client), conversa
    # iniciada em campanha de Geração de Cadastro fica de fora da contagem de lead. Foi
    # exatamente isso que zerou o resultado de 9 das 13 marcas do Felipe em 09/09/2026, depois
    # que passaram a rodar Click-to-WhatsApp dentro de campanha OUTCOME_LEADS.
    destinos = meta_client.get_campaign_destinos(meta_account_id)
    return {
        "destinos": destinos,
        "saldo": meta_client.get_ad_account_balance(meta_account_id),
        "insights": meta_client.get_daily_insights(meta_account_id, days=days, destinos=destinos),
        "ads_daily": meta_client.get_ads_daily(meta_account_id, since_criativos, until_criativos, destinos=destinos),
        "orcamentos": meta_client.get_campaign_orcamentos(meta_account_id),
        # Janela própria de 30 dias, independente de `days` (que agora é só 7, ver
        # DIAS_JANELA_SYNC_PADRAO) — padrão geográfico não precisa de frescor diário, e uma
        # janela maior estabiliza melhor quais estados realmente concentram resultado.
        "regioes": meta_client.get_region_performance(meta_account_id, days=30, destinos=destinos),
        "cidades_configuradas": meta_client.get_campaign_cidades(meta_account_id),
        "aprendizado": meta_client.get_learning_status(meta_account_id),
        "targeting_signatures": meta_client.get_targeting_signatures(meta_account_id),
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
    historico.gravar_saldo_historico(db, account, saldo)

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
        historico.gravar_campanhas_conta(
            db, account_brands, insights, dados.get("destinos", {}), dados["orcamentos"], _brand_matches_campaign
        )
        sem_marca: dict[str, float] = {}
        for r in insights:
            if r["gasto"] > 0 and not any(_brand_matches_campaign(br, r["campaign_name"]) for br in account_brands):
                sem_marca[r["campaign_name"]] = sem_marca.get(r["campaign_name"], 0.0) + r["gasto"]
        notificacoes.gerar_alerta_gasto_sem_marca(db, account, account_brands, sem_marca)

    # --- orçamento diário REAL (o que está configurado agora no Meta, não o digitado à mão) ---
    if META_DEMO_MODE:
        rng_orc = random.Random(account.id * 911)
        for brand in account_brands:
            # gira em torno do planejado (±20%) só pra ter algo plausível pra mostrar sem token
            fator = 0.8 + rng_orc.random() * 0.4
            brand.orcamento_diario_real = round((brand.diario_esperado_meta or 50) * fator, 2)
            brand.orcamento_atualizado_em = datetime.utcnow()
    else:
        orcamentos = dados["orcamentos"]
        # `effective_status: ACTIVE` no conjunto NÃO garante que ele está gastando de verdade —
        # achamos, ao investigar uma divergência real (Nissan Dourados: R$241 calculado vs
        # R$105 de verdade), duas campanhas de meses passados ("...JULHO", "...MARÇO") ainda
        # com esse status e orçamento configurado, mas ZERO gasto nos últimos dias: ninguém
        # pausou, mas pararam de rodar. Cruza com o gasto recente (mesmos `insights` já
        # buscados pra leads/gasto, nenhuma chamada extra) e só conta campanha que gastou algo
        # de verdade na janela de sincronização.
        campanhas_com_gasto_recente = {r["campaign_name"] for r in dados["insights"] if r["gasto"] > 0}
        for brand in account_brands:
            total = sum(
                valor
                for nome, valor in orcamentos.items()
                if nome in campanhas_com_gasto_recente and _brand_matches_campaign(brand, nome)
            )
            brand.orcamento_diario_real = total
            brand.orcamento_atualizado_em = datetime.utcnow()

    # --- mapa de calor por estado + cidades configuradas (pra abrir ao clicar num estado) ---
    if META_DEMO_MODE:
        rng_geo = random.Random(account.id * 733)
        ESTADOS_DEMO = ["Mato Grosso do Sul", "Acre", "Rondônia"]
        for brand in account_brands:
            estado = ESTADOS_DEMO[brand.id % len(ESTADOS_DEMO)]
            _replace_meta_regioes(db, brand.id, [{"regiao": estado, "gasto": round(rng_geo.uniform(200, 900), 2), "leads": rng_geo.randint(5, 40)}])
            _replace_meta_cidades(db, brand.id, [{"regiao": estado, "cidade": "Cidade Demonstração"}])
    else:
        regioes_conta = dados["regioes"]
        cidades_conta = dados["cidades_configuradas"]
        for brand in account_brands:
            agregado_regiao: dict[str, dict] = {}
            for r in regioes_conta:
                if not _brand_matches_campaign(brand, r["campaign_name"]):
                    continue
                acc = agregado_regiao.setdefault(r["regiao"], {"regiao": r["regiao"], "gasto": 0.0, "leads": 0})
                acc["gasto"] += r["gasto"]
                acc["leads"] += r["leads"]
            _replace_meta_regioes(db, brand.id, list(agregado_regiao.values()))

            cidades_marca: dict[tuple, dict] = {}
            for nome_campanha, cidades in cidades_conta.items():
                if not _brand_matches_campaign(brand, nome_campanha):
                    continue
                for c in cidades:
                    cidades_marca[(c["regiao"], c["cidade"])] = c
            _replace_meta_cidades(db, brand.id, list(cidades_marca.values()))

    # --- fase de aprendizado travada ---
    # Não simulado em modo demo (não há learning_stage_info sem token real) — sem dado, sem
    # alerta, o que é o comportamento correto (nenhuma conta demo "trava" de verdade).
    if not META_DEMO_MODE:
        aprendizado_conta = dados["aprendizado"]
        for brand in account_brands:
            itens_marca = [a for a in aprendizado_conta if _brand_matches_campaign(brand, a["campanha_nome"])]
            if itens_marca:
                notificacoes.gerar_alertas_aprendizado_travado(db, brand, itens_marca)

    # --- públicos sobrepostos ---
    # Mesma justificativa de não simular em demo: sem `targeting` real não existe sobreposição
    # de verdade pra detectar.
    if not META_DEMO_MODE:
        assinaturas_conta = dados["targeting_signatures"]
        for brand in account_brands:
            itens_marca = [a for a in assinaturas_conta if _brand_matches_campaign(brand, a["campanha_nome"])]
            if len(itens_marca) >= 2:
                notificacoes.gerar_alertas_publico_sobreposto(db, brand, itens_marca)

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


# O painel já guarda o histórico de gasto/lead pra sempre em DailyMetric (nada ali é apagado —
# é o que sustenta comparativo mês a mês por até 2 anos). Re-pedir 30 dias ao Meta em TODA
# sincronização (2x por dia + cada clique em "Atualizar") só faz sentido pros dias que ainda
# podem mudar: o Meta reprocessa atribuição por alguns dias após o fato, então um dia com mais
# de uma semana já assentou e pedir de novo é desperdício de tempo de sincronização e de limite
# de chamada da API. Dias mais velhos que isso já estão certos no banco — não tocamos neles.
DIAS_JANELA_SYNC_PADRAO = 7


def sync_all(db: Session, days: int = DIAS_JANELA_SYNC_PADRAO) -> dict:
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


def _sync_all(db: Session, days: int = DIAS_JANELA_SYNC_PADRAO) -> dict:
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
                db,
                account,
                account_brands,
                days,
                since_criativos,
                until_criativos,
                dados_por_conta.get(account_id),
            )
            db.commit()
            synced_accounts += 1
        except Exception as exc:
            db.rollback()
            falhas.append(f"{account.nome}: {exc}")
            print(f"[sync] falha ao gravar conta Meta '{account.nome}' ({account.meta_account_id}): {exc}")

    try:
        alertas_saldo = notificacoes.gerar_alertas_saldo(db)
        alertas_gasto = notificacoes.gerar_alertas_gasto_anomalo(db)
        alertas_leads = notificacoes.gerar_alertas_queda_leads(db)
        alertas_performance = notificacoes.gerar_alertas_alta_performance(db)
        alertas_subutilizado = notificacoes.gerar_alertas_orcamento_subutilizado(db)
        alertas_orcamento = notificacoes.gerar_alertas_orcamento_zerado(db)
        db.commit()
        total_alertas = (
            alertas_saldo + alertas_gasto + alertas_leads + alertas_performance + alertas_subutilizado + alertas_orcamento
        )
        if total_alertas:
            print(
                f"[sync] notificações geradas: {alertas_saldo} de saldo crítico, {alertas_gasto} de gasto fora "
                f"da curva, {alertas_leads} de queda de leads, {alertas_performance} de alta performance, "
                f"{alertas_subutilizado} de orçamento subutilizado, {alertas_orcamento} de orçamento zerado"
            )
    except Exception as exc:
        db.rollback()
        falhas.append(f"Notificações: {exc}")
        print(f"[sync] falha ao gerar notificações: {exc}")

    try:
        relatorios_criados = historico.garantir_snapshots(db, dados_frescos=synced_accounts > 0)
        db.commit()
        if relatorios_criados:
            print(f"[sync] {relatorios_criados} relatório(s) diário(s) gravado(s) no histórico")
    except Exception as exc:
        db.rollback()
        falhas.append(f"Histórico diário: {exc}")
        print(f"[sync] falha ao gravar relatório diário: {exc}")

    try:
        contas_instagram, dados_instagram = _sync_instagram(db, days)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Instagram: {exc}")
        print(f"[sync] falha ao sincronizar Instagram: {exc}")
        contas_instagram, dados_instagram = 0, {}

    try:
        comentarios_novos = _sync_social(db, dados_instagram)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Redes sociais: {exc}")
        print(f"[sync] falha ao sincronizar redes sociais: {exc}")
        comentarios_novos = 0

    erros_google: list[tuple[str, str]] = []
    try:
        contas_google = _sync_google_ads(db, days, erros_google)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Google Ads: {exc}")
        print(f"[sync] falha ao sincronizar Google Ads: {exc}")
        contas_google = 0
    falhas.extend(_resumir_falhas_google(erros_google))

    # Depois da sincronização do Google, porque é aqui que o saldo novo chega: avisa quando uma
    # conta zera (ver notificacoes.gerar_alertas_saldo_google).
    try:
        alertas_saldo_google = notificacoes.gerar_alertas_saldo_google(db)
        db.commit()
        if alertas_saldo_google:
            print(f"[sync] {alertas_saldo_google} alerta(s) de saldo zerado no Google Ads")
    except Exception as exc:
        db.rollback()
        falhas.append(f"Alerta de saldo Google: {exc}")
        print(f"[sync] falha ao gerar alerta de saldo do Google: {exc}")

    try:
        marcas_funil = _sync_funil(db, days)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Funil: {exc}")
        print(f"[sync] falha ao sincronizar funil: {exc}")
        marcas_funil = 0

    try:
        tokens_conferidos = _sync_tokens(db)
        alertas_token = notificacoes.gerar_alertas_token(db)
        db.commit()
        if alertas_token:
            print(f"[sync] notificações geradas: {alertas_token} de saúde de token")
    except Exception as exc:
        db.rollback()
        falhas.append(f"Tokens: {exc}")
        print(f"[sync] falha ao conferir tokens: {exc}")
        tokens_conferidos = 0

    try:
        noticias = _sync_news(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        falhas.append(f"Notícias: {exc}")
        print(f"[sync] falha ao sincronizar notícias: {exc}")
        noticias = 0

    # Por último de propósito: só agora `falhas` tem tudo que deu errado nesta sincronização
    # inteira (Meta, Instagram, Google, funil, tokens, notícias).
    try:
        if notificacoes.gerar_alerta_falha_sync(db, falhas):
            db.commit()
            print("[sync] notificação de falha na sincronização gerada")
    except Exception as exc:
        db.rollback()
        print(f"[sync] falha ao gerar notificação de falha de sincronização: {exc}")

    return {
        "contas_sincronizadas": synced_accounts,
        "registros_diarios": synced_days,
        "contas_instagram_sincronizadas": contas_instagram,
        "comentarios_novos": comentarios_novos,
        "tokens_conferidos": tokens_conferidos,
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


def _resumir_falhas_google(erros: list[tuple[str, str]]) -> list[str]:
    """Contas Google que falharam pelo MESMO motivo viram UMA linha de aviso (token revogado
    derruba as 13 de uma vez — são 13 linhas iguais no sino, ou uma dizendo quantas e quais)."""
    por_motivo: dict[str, list[str]] = {}
    for nome, erro in erros:
        por_motivo.setdefault(erro[:180], []).append(nome)
    resumo = []
    for motivo, nomes in por_motivo.items():
        quem = ", ".join(nomes[:3]) + (f" e mais {len(nomes) - 3}" if len(nomes) > 3 else "")
        resumo.append(f"Google Ads ({len(nomes)} conta{'s' if len(nomes) != 1 else ''}: {quem}): {motivo}")
    return resumo


def _sync_google_ads(db: Session, days: int, erros: list[tuple[str, str]] | None = None) -> int:
    """Devolve quantas contas sincronizaram. Se `erros` for passado, recebe (nome da conta,
    erro) de cada conta que falhou — sem isso a falha só ia pro log do servidor e a
    sincronização "concluía" normalmente: o token do Google ficou revogado de 04/08 a 02/10
    sem nenhum aviso no sino."""
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
                if erros is not None:
                    erros.append((account.nome, erro))

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

    # --- saldo da conta, direto do Google (nunca calculado a partir do orçamento mensal) ---
    # Demo não tem de onde tirar saldo de verdade, então fica vazio em vez de inventado. Falha
    # só nesta consulta não derruba o resto da conta: o saldo fica vazio ("—" na tela).
    cache.saldo = None
    if not GOOGLE_ADS_DEMO_MODE:
        try:
            balanco = google_ads_client.get_account_balance(account.customer_id)
            cache.saldo = balanco["saldo"] if balanco else None
        except Exception as exc:
            print(f"[sync] não consegui ler o saldo da conta Google '{account.nome}': {exc}")

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
    # A listagem de posts é buscada uma vez e usada duas: aqui, pro conteúdo orgânico, e
    # depois em _sync_social, pros comentários — que assim não custam requisição nenhuma.
    posts = instagram_client.get_media(instagram_business_id, access_token)

    # A URL de imagem do Meta expira, então guardamos a miniatura localmente — mas só das
    # publicações recentes. Baixar as 50 de cada uma das 23 contas seria mais de mil imagens
    # por sincronização; o painel só mostra mês atual e mês anterior, e o cache em disco faz
    # a mesma imagem não ser baixada duas vezes.
    corte_miniatura = date.today() - timedelta(days=DIAS_MINIATURA_INSTAGRAM)
    enriquecidos = []
    for p in posts:
        publicado = _ts(p.get("timestamp"))
        thumb = None
        if publicado and publicado.date() >= corte_miniatura:
            thumb = media_cache.baixar_imagem(p.get("thumbnail_url") or p.get("media_url"), "instagram", p["id"])
        enriquecidos.append(
            {
                "post_id": p.get("id"),
                "legenda": (p.get("caption") or "").strip(),
                "thumbnail_url": thumb,
                "permalink": p.get("permalink"),
                "curtidas": int(p.get("like_count", 0) or 0),
                "comentarios": int(p.get("comments_count", 0) or 0),
                "publicado_em": publicado,
            }
        )

    return {
        "seguidores_atual": instagram_client.get_followers_count(instagram_business_id, access_token),
        "ganhos_diarios": instagram_client.get_follower_gain_daily(instagram_business_id, access_token, days=days),
        "metricas_posts": enriquecidos,
        "posts": posts,
    }


def _sync_instagram(db: Session, days: int) -> tuple[int, dict[int, dict]]:
    """Devolve quantas contas sincronizaram e o que foi buscado — a listagem de posts vai
    junto porque _sync_social a reaproveita em vez de buscar de novo."""
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
    return synced, dados_por_conta


def _sync_instagram_account(db: Session, account: InstagramAccount, days: int, dados: dict | None = None) -> None:
    """Grava no banco os dados de uma conta. `dados` vem da fase de busca; se não vier, busca
    na hora (volta a ser rede + banco junto)."""
    if account.access_token and dados is None:
        dados = _buscar_instagram_account(account.instagram_business_id, account.access_token, days)

    if not account.access_token:
        rng = random.Random(account.id * 53)
        seguidores_atual = 500 + (account.id * 733) % 12000
        hoje = date.today()
        ganhos_diarios = [
            {"data": hoje - timedelta(days=i), "ganho": rng.randint(-2, 8)} for i in range(days)
        ]
        metricas_posts = [
            {
                "post_id": f"demo-post-{account.id}-{i}",
                "legenda": f"Post demo {i + 1} — {account.nome}",
                "thumbnail_url": None,
                "permalink": None,
                "curtidas": 40 + rng.randint(0, 300),
                "comentarios": rng.randint(0, 30),
                "publicado_em": datetime.combine(hoje - timedelta(days=i * 4), datetime.min.time()),
            }
            for i in range(12)
        ]
    else:
        seguidores_atual = dados["seguidores_atual"]
        ganhos_diarios = dados["ganhos_diarios"]
        metricas_posts = dados["metricas_posts"]

    # Seguidores no total é um retrato do momento, não um acumulado — continua em cache.
    stats = db.query(InstagramStatsCache).filter(InstagramStatsCache.instagram_account_id == account.id).first()
    if not stats:
        stats = InstagramStatsCache(instagram_account_id=account.id)
        db.add(stats)
    stats.seguidores_atual = seguidores_atual
    stats.atualizado_em = datetime.utcnow()

    # Ganho de seguidores dia a dia — o painel soma o intervalo que estiver selecionado.
    existentes_dia = {
        linha.data: linha
        for linha in db.query(InstagramFollowerDaily)
        .filter(InstagramFollowerDaily.instagram_account_id == account.id)
        .all()
    }
    for linha in ganhos_diarios:
        atual = existentes_dia.get(linha["data"])
        if atual:
            atual.ganho = linha["ganho"]
        else:
            db.add(
                InstagramFollowerDaily(
                    instagram_account_id=account.id, data=linha["data"], ganho=linha["ganho"]
                )
            )

    # Cada post com sua data: o "melhor post" deixa de ser escolhido aqui e passa a ser
    # calculado na tela, para o período que estiver selecionado.
    existentes_post = {
        p.post_id: p
        for p in db.query(InstagramPostMetric)
        .filter(InstagramPostMetric.instagram_account_id == account.id)
        .all()
    }
    for p in metricas_posts:
        if not p.get("post_id"):
            continue
        alvo = existentes_post.get(p["post_id"])
        if not alvo:
            alvo = InstagramPostMetric(instagram_account_id=account.id, post_id=p["post_id"])
            db.add(alvo)
        alvo.legenda = p["legenda"]
        # Miniatura só é baixada pros posts recentes; não apaga a que já existe.
        if p["thumbnail_url"]:
            alvo.thumbnail_url = p["thumbnail_url"]
        alvo.permalink = p["permalink"]
        alvo.curtidas = p["curtidas"]
        alvo.comentarios = p["comentarios"]
        alvo.publicado_em = p["publicado_em"]
        alvo.atualizado_em = datetime.utcnow()


def _ts(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        # A Graph API devolve com fuso (ex.: 2026-08-30T14:02:11+0000); o banco guarda tudo
        # em UTC sem fuso, igual ao resto do sistema.
        dt = datetime.fromisoformat(valor)
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def _buscar_social_marca(
    token: str,
    username: str,
    posts: list[dict],
    contagens_conhecidas: dict[str, int],
    midias_pendentes: set[str],
) -> list[dict]:
    """Comentários de uma marca. Só rede, sem tocar no banco (roda em paralelo, como o resto).

    É no máximo UMA requisição HTTP por marca: a listagem de posts já veio da sincronização do
    Instagram (com contagem, permalink e legenda), então só os comentários em si precisam ser
    buscados — e vão todos num lote só. Post cujo número de comentários não mudou desde a
    última coleta nem entra no lote.
    """
    info: dict[str, dict] = {
        p["id"]: {
            "comentarios_count": int(p.get("comments_count", 0) or 0),
            "permalink": p.get("permalink"),
            "legenda": (p.get("caption") or "").strip(),
        }
        for p in posts
        if p.get("id")
    }

    # Relemos uma mídia quando ela ganhou comentário novo OU quando ainda tem comentário nosso
    # sem resposta. A segunda condição não é luxo: responder um comentário nem sempre mexe no
    # comments_count do post, então sem ela um comentário atendido ficaria pendente pra sempre
    # na tela — que é justamente a informação que a aba existe pra dar.
    candidatas = [
        mid
        for mid, d in info.items()
        if d["comentarios_count"] > 0
        and (contagens_conhecidas.get(mid) != d["comentarios_count"] or mid in midias_pendentes)
    ]

    coletados: list[dict] = []
    for mid, comentarios in social_client.buscar_comentarios(token, candidatas, username).items():
        for c in comentarios:
            coletados.append({**c, "media_id": mid, **{k: info[mid][k] for k in ("permalink", "legenda")}})

    # A contagem só é dada como "vista" pras mídias que a gente realmente releu agora.
    contagens_novas = {mid: info[mid]["comentarios_count"] for mid in candidatas}
    return [{"comentarios": coletados, "contagens": contagens_novas}]


def _sync_social(db: Session, dados_instagram: dict[int, dict]) -> int:
    """Coleta os comentários das marcas em ANALISTAS_REDES_SOCIAIS.

    `dados_instagram` é a colheita que _sync_instagram já fez (a listagem /media de cada
    conta) — reaproveitá-la é o que faz esta etapa quase não custar requisição nenhuma.
    """
    if META_DEMO_MODE:
        return 0

    marcas = [
        b
        for b in db.query(Brand).filter(Brand.instagram_account_id.isnot(None)).all()
        if not ANALISTAS_REDES_SOCIAIS or (b.analista or "") in ANALISTAS_REDES_SOCIAIS
    ]
    if not marcas:
        return 0

    contas = {a.id: a for a in db.query(InstagramAccount).all()}
    cache = {c.media_id: c.comentarios_count for c in db.query(MediaComentarioCache).all()}
    limite_reconferir = datetime.utcnow() - timedelta(days=REDES_SOCIAIS_DIAS_RECONFERIR)
    pendentes_por_marca: dict[int, set[str]] = {}
    for brand_id, media_id in (
        db.query(ComentarioSocial.brand_id, ComentarioSocial.media_id)
        .filter(
            ComentarioSocial.respondido.is_(False),
            ComentarioSocial.media_id.isnot(None),
            ComentarioSocial.criado_em >= limite_reconferir,
        )
        .distinct()
        .all()
    ):
        pendentes_por_marca.setdefault(brand_id, set()).add(media_id)

    # Fase 1: buscar, várias marcas ao mesmo tempo.
    resultado_por_marca: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
        futures = {}
        for brand in marcas:
            conta = contas.get(brand.instagram_account_id)
            if not conta or not conta.access_token:
                continue
            posts = (dados_instagram.get(conta.id) or {}).get("posts") or []
            futures[
                executor.submit(
                    _buscar_social_marca,
                    conta.access_token,
                    conta.username,
                    posts,
                    cache,
                    pendentes_por_marca.get(brand.id, set()),
                )
            ] = brand
        for fut in as_completed(futures):
            brand = futures[fut]
            try:
                resultado_por_marca[brand.id] = fut.result()[0]
            except Exception as exc:
                print(f"[sync] falha ao coletar comentários de '{brand.nome}': {exc}")

    # Fase 2: gravar, uma de cada vez.
    novos = 0
    for brand in marcas:
        resultado = resultado_por_marca.get(brand.id)
        if not resultado:
            continue
        novos += _gravar_comentarios(db, brand, resultado["comentarios"])
        for media_id, contagem in resultado["contagens"].items():
            linha = db.query(MediaComentarioCache).filter(MediaComentarioCache.media_id == media_id).first()
            if not linha:
                linha = MediaComentarioCache(media_id=media_id)
                db.add(linha)
            linha.comentarios_count = contagem
            linha.atualizado_em = datetime.utcnow()
        db.commit()

    _limpar_comentarios_antigos(db)
    db.commit()
    return novos


def _sync_tokens(db: Session) -> int:
    """Confere o prazo de validade de todos os tokens do Meta que o sistema usa.

    Uma requisição em lote resolve os 25+ tokens, então cabe em qualquer sincronização. O
    valor que importa é `data_access_expires_at`: `expires_at` vem 0 em praticamente todos os
    nossos tokens e dá a falsa impressão de que nenhum vence.
    """
    if META_DEMO_MODE:
        return 0

    # `prova` é um caminho barato da Graph API que só responde se o token estiver vivo. Serve
    # de desempate: o debug_token só inspeciona token do MESMO app, então ele falha em token
    # vindo de outro Business Manager — e falha de inspeção não é token morto. Sem esse
    # desempate o painel acusava o @acrediesel de vencido enquanto ele funcionava normalmente.
    alvos: list[tuple[str, str, str, str]] = [("sistema", "Token do sistema (Meta Ads)", META_ACCESS_TOKEN, "me")]
    if META_ACCESS_TOKEN_PESSOAL:
        alvos.append(("pessoal", "Token pessoal (contas Jeep)", META_ACCESS_TOKEN_PESSOAL, "me"))
    for conta in db.query(InstagramAccount).all():
        if conta.access_token:
            alvos.append(
                (f"instagram:{conta.id}", f"Instagram @{conta.username}", conta.access_token, conta.instagram_business_id)
            )

    try:
        infos = social_client.inspecionar_tokens(META_ACCESS_TOKEN, [t for _, _, t, _ in alvos])
    except Exception as exc:
        print(f"[sync] não deu pra conferir os tokens: {exc}")
        return 0

    def _quando(valor) -> datetime | None:
        # 0 (ou ausente) quer dizer "sem prazo", não "vence agora" — cuidado com falsy aqui.
        return datetime.utcfromtimestamp(valor) if valor else None

    for (chave, rotulo, token, prova), info in zip(alvos, infos):
        linha = db.query(TokenSaude).filter(TokenSaude.chave == chave).first()
        if not linha:
            linha = TokenSaude(chave=chave)
            db.add(linha)
        linha.rotulo = rotulo
        linha.atualizado_em = datetime.utcnow()

        if info is not None:
            linha.tipo = info.get("type")
            linha.valido = bool(info.get("is_valid"))
            linha.expira_em = _quando(info.get("expires_at"))
            linha.acesso_expira_em = _quando(info.get("data_access_expires_at"))
            linha.erro = (info.get("error") or {}).get("message")
            continue

        # Não deu pra inspecionar: pergunta direto ao token se ele ainda responde.
        linha.tipo = "DESCONHECIDO"
        linha.expira_em = None
        linha.acesso_expira_em = None
        try:
            resp = httpx.get(
                f"https://graph.facebook.com/{META_API_VERSION}/{prova}",
                params={"fields": "id", "access_token": token},
                timeout=15,
            )
            linha.valido = resp.is_success
            linha.erro = None if resp.is_success else resp.json().get("error", {}).get("message", "")[:200]
        except Exception as exc:
            linha.valido = False
            linha.erro = str(exc)[:200]
    db.commit()
    return len(alvos)


def _gravar_comentarios(db: Session, brand: Brand, comentarios: list[dict]) -> int:
    """Insere os comentários novos e atualiza os que passaram a ter resposta.

    Comentário já conhecido não é reinserido, mas `respondido` é revisto toda vez — é
    exatamente a informação que muda depois que alguém atende.
    """
    if not comentarios:
        return 0
    ids = [c["comentario_id"] for c in comentarios if c.get("comentario_id")]
    existentes = {
        c.comentario_id: c
        for c in db.query(ComentarioSocial).filter(ComentarioSocial.comentario_id.in_(ids)).all()
    }
    novos = 0
    for c in comentarios:
        cid = c.get("comentario_id")
        if not cid:
            continue
        atual = existentes.get(cid)
        if atual:
            if c["respondido"] and not atual.respondido:
                atual.respondido = True
                atual.respondido_em = _ts(c.get("respondido_em"))
            continue
        db.add(
            ComentarioSocial(
                brand_id=brand.id,
                canal="instagram_comentario",
                comentario_id=cid,
                media_id=c.get("media_id"),
                autor=c.get("autor"),
                texto=c.get("texto"),
                permalink=c.get("permalink"),
                legenda_post=(c.get("legenda") or "")[:300],
                criado_em=_ts(c.get("timestamp")),
                respondido=bool(c.get("respondido")),
                respondido_em=_ts(c.get("respondido_em")),
            )
        )
        novos += 1
    return novos


def _limpar_comentarios_antigos(db: Session) -> None:
    corte = datetime.utcnow() - timedelta(days=REDES_SOCIAIS_DIAS_RETENCAO)
    db.query(ComentarioSocial).filter(ComentarioSocial.criado_em < corte).delete(synchronize_session=False)


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


def _replace_meta_regioes(db: Session, brand_id: int, regioes: list[dict]):
    """`regioes`: lista de {"regiao", "gasto", "leads"} já somada por estado pra essa marca."""
    existing = {r.regiao: r for r in db.query(MetaRegionCache).filter(MetaRegionCache.brand_id == brand_id).all()}
    for item in regioes:
        nome = item["regiao"]
        cache = existing.pop(nome, None)
        if not cache:
            cache = MetaRegionCache(brand_id=brand_id, regiao=nome)
            db.add(cache)
        cache.gasto = item["gasto"]
        cache.leads = item["leads"]
        cache.atualizado_em = datetime.utcnow()
    for leftover in existing.values():
        db.delete(leftover)


def _replace_meta_cidades(db: Session, brand_id: int, cidades: list[dict]):
    """`cidades`: lista de {"regiao", "cidade"} — cidades configuradas nos conjuntos ativos
    dessa marca agora, já deduplicada."""
    existing = {(c.regiao, c.cidade): c for c in db.query(MetaCidadeCache).filter(MetaCidadeCache.brand_id == brand_id).all()}
    for item in cidades:
        chave = (item["regiao"], item["cidade"])
        cache = existing.pop(chave, None)
        if not cache:
            db.add(MetaCidadeCache(brand_id=brand_id, regiao=item["regiao"], cidade=item["cidade"]))
        else:
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
