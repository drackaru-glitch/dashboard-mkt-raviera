"""Cliente da Graph API do Meta. Só é chamado quando META_ACCESS_TOKEN está configurado
(ver app.config.META_DEMO_MODE) — sync.py decide quando usar isto ou gerar dados de demonstração.
"""

import re
import time
from datetime import date, timedelta

import httpx

from app.config import CONTAS_TOKEN_PESSOAL, META_ACCESS_TOKEN, META_ACCESS_TOKEN_PESSOAL, META_API_VERSION

BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"

# A Graph API devolve VÁRIAS action_types pro mesmo evento (ex.: "lead" e
# "onsite_conversion.lead_grouped" são o MESMO cadastro contado de duas formas — somar as
# duas dobra o número). Por isso usamos só uma por tipo, nunca um conjunto somado.
LEAD_ACTION_TYPE = "lead"
WHATSAPP_ACTION_TYPE = "onsite_conversion.messaging_conversation_started_7d"

# Campanha de Geração de Cadastro (OUTCOME_LEADS) às vezes gera conversa de WhatsApp como
# efeito colateral (alguém manda mensagem pela página), e isso não é o resultado da campanha
# — só deve contar como lead quando o anúncio de verdade manda a pessoa pra uma conversa.
#
# Até aqui essa decisão era tomada pelo OBJETIVO da campanha (só contava se fosse
# OUTCOME_ENGAGEMENT). Isso quebrou quando o grupo passou a rodar Click-to-WhatsApp DENTRO de
# campanhas com objetivo OUTCOME_LEADS — configuração hoje comum no Meta, onde o objetivo da
# campanha é "Geração de Cadastro" mas o CONJUNTO manda pra conversa. Resultado: em 09/09/2026,
# 9 das 13 marcas do Felipe apareceram com ZERO lead no dia, incluindo contas que rotineiramente
# convertem bem (Nissan CG gastou R$163 "sem gerar lead nenhum") — a conversa estava acontecendo
# (`onsite_conversion.messaging_conversation_started_7d` de 8 a 27 por dia, por campanha), só
# não era contada porque a campanha não era mais OUTCOME_ENGAGEMENT.
#
# O sinal confiável não é o objetivo da campanha, é o DESTINO DO CONJUNTO
# (`destination_type` — WHATSAPP ou MESSENGER), que é propriedade do adset e reflete pra onde
# o anúncio manda a pessoa de verdade, independente de como a campanha foi rotulada.
DESTINOS_CONVERSA = {"WHATSAPP", "MESSENGER"}


def get_campaign_destinos(meta_account_id: str) -> dict[str, str]:
    """Nome da campanha -> destino de conversa (WHATSAPP/MESSENGER), pras campanhas que têm.

    Uma chamada leve e separada (~1 a 3s mesmo em conta grande — testado numa com 378
    conjuntos): só dois campos escalares, nada de insight nem de criativo. `_contar_leads`
    usa isso pra decidir se "conversa iniciada" é resultado do anúncio ou efeito colateral.
    """
    data = _get(
        f"{meta_account_id}/adsets",
        {"fields": "destination_type,campaign{name}", "limit": 500},
        meta_account_id,
    )
    destinos: dict[str, str] = {}
    for adset in data.get("data", []):
        destino = adset.get("destination_type")
        nome_campanha = (adset.get("campaign") or {}).get("name")
        if nome_campanha and destino in DESTINOS_CONVERSA:
            destinos[nome_campanha] = destino
    return destinos


def get_campaign_orcamentos(meta_account_id: str) -> dict[str, float]:
    """Nome da campanha -> orçamento diário REAL configurado agora no Meta (em R$), só de
    conjuntos ativos. Não confundir com `diario_esperado_meta` do banco (Brand): aquele é
    digitado à mão na tela de Marcas e não se atualiza sozinho quando alguém reajusta o
    orçamento direto no Gerenciador de Anúncios — este aqui é lido de novo a cada sincronização.

    Deduplica CBO (Campaign Budget Optimization) x ABO (orçamento por conjunto): numa campanha
    CBO, `campaign.daily_budget` é UM valor só pra campanha inteira, não por conjunto — somar
    esse valor uma vez por conjunto multiplicaria o orçamento pelo número de conjuntos.

    Chamada separada de `get_campaign_destinos` de propósito: aquela decide contagem de lead
    (já causou um incidente sério, ver DESTINOS_CONVERSA acima) — não vale economizar uma
    chamada de API (~1 a 3s) arriscando mexer nela.

    IMPORTANTE — `effective_status == "ACTIVE"` aqui NÃO garante que a campanha está gastando
    de verdade: achamos campanha de meses passados ainda "ativa" e com orçamento configurado,
    mas zero gasto real há dias (ninguém pausou, só parou de rodar). Por isso quem chama esta
    função (sync.py) cruza o resultado com o gasto recente antes de somar por marca — não
    trate o valor daqui como definitivo sem esse cruzamento.
    """
    data = _get(
        f"{meta_account_id}/adsets",
        {"fields": "daily_budget,effective_status,campaign{name,daily_budget}", "limit": 500},
        meta_account_id,
    )
    orcamentos: dict[str, float] = {}
    campanhas_cbo_contadas: set[str] = set()
    for adset in data.get("data", []):
        if adset.get("effective_status") != "ACTIVE":
            continue  # conjunto pausado não consome orçamento — não deve inflar o "real"
        campaign = adset.get("campaign") or {}
        nome_campanha = campaign.get("name")
        if not nome_campanha:
            continue
        orcamento_campanha = campaign.get("daily_budget")
        if orcamento_campanha is not None:
            # CBO: orçamento é da campanha, não do conjunto — soma uma vez só por campanha.
            if nome_campanha not in campanhas_cbo_contadas:
                orcamentos[nome_campanha] = float(orcamento_campanha) / 100
                campanhas_cbo_contadas.add(nome_campanha)
        else:
            # ABO: cada conjunto tem seu próprio orçamento — soma todos os da campanha.
            orcamento_adset = float(adset.get("daily_budget") or 0) / 100
            orcamentos[nome_campanha] = orcamentos.get(nome_campanha, 0.0) + orcamento_adset
    return orcamentos


def _contar_leads(actions: list[dict] | None, eh_campanha_de_conversa: bool) -> tuple[int, int]:
    leads_formulario = 0
    leads_whatsapp = 0
    for action in actions or []:
        action_type = action.get("action_type")
        qty = int(float(action.get("value", 0) or 0))
        if action_type == LEAD_ACTION_TYPE:
            leads_formulario += qty
        elif action_type == WHATSAPP_ACTION_TYPE and eh_campanha_de_conversa:
            leads_whatsapp += qty
    return leads_formulario, leads_whatsapp


def _token_para_conta(meta_account_id: str) -> str:
    if meta_account_id in CONTAS_TOKEN_PESSOAL and META_ACCESS_TOKEN_PESSOAL:
        return META_ACCESS_TOKEN_PESSOAL
    return META_ACCESS_TOKEN


# Erros que o Meta devolve por sobrecarga momentânea, não por algo errado no pedido: code 1
# ("Please reduce the amount of data you're asking for") e os de limite de chamadas. Aparecem
# mais desde que a sincronização voltou a buscar várias contas ao mesmo tempo, e somem sozinhos
# ao repetir — as contas maiores (mais de mil anúncios/dia) são as que mais esbarram neles.
CODIGOS_TENTAR_DE_NOVO = {1, 2, 4, 17, 32, 613}
TENTATIVAS = 3


def _get(path: str, params: dict, meta_account_id: str) -> dict:
    params = {**params, "access_token": _token_para_conta(meta_account_id)}
    for tentativa in range(1, TENTATIVAS + 1):
        resp = httpx.get(f"{BASE_URL}/{path}", params=params, timeout=30)
        if resp.is_success:
            return resp.json()
        try:
            codigo = resp.json().get("error", {}).get("code")
        except ValueError:
            codigo = None
        if codigo not in CODIGOS_TENTAR_DE_NOVO or tentativa == TENTATIVAS:
            raise RuntimeError(f"Meta API error {resp.status_code} em {path}: {resp.text[:300]}")
        time.sleep(2 * tentativa)  # espera crescente: 2s, depois 4s


def _parse_valor_brl(texto: str) -> float | None:
    """Extrai o número de textos tipo 'Saldo disponível (R$4.336,66 BRL)' — formato
    brasileiro (ponto = milhar, vírgula = decimal)."""
    m = re.search(r"R\$\s*([\d.,]+)", texto)
    if not m:
        return None
    return float(m.group(1).replace(".", "").replace(",", "."))


def get_ad_account_balance(meta_account_id: str) -> dict:
    data = _get(
        meta_account_id,
        {
            "fields": "balance,amount_spent,spend_cap,currency,is_prepay_account,"
            "funding_source_details{display_string,type}"
        },
        meta_account_id,
    )
    currency = data.get("currency", "BRL")

    # Todas as contas do grupo são pré-pagas (conferido na Graph API) — pra essas, o campo
    # "balance" NÃO é o saldo disponível (é praticamente sempre ~0, relacionado a fatura/threshold
    # de pós-pago). O saldo disponível de verdade só existe em funding_source_details.display_string
    # (ex.: "Saldo disponível (R$4.336,66 BRL)"), que é o mesmo valor mostrado no Gerenciador de
    # Anúncios. Antes disso, o app usava "balance" direto e aparecia 100x menor que o real.
    if data.get("is_prepay_account"):
        funding = data.get("funding_source_details") or {}
        saldo_prepago = _parse_valor_brl(funding.get("display_string") or "")
        if saldo_prepago is not None:
            return {"saldo": saldo_prepago, "moeda": currency}

    # Contas pós-pagas (faturamento por fatura): balance/amount_spent/spend_cap vêm na menor
    # unidade da moeda (centavos pra BRL/USD), por isso dividimos por 100 aqui.
    spend_cap = float(data.get("spend_cap", 0) or 0)
    amount_spent = float(data.get("amount_spent", 0) or 0)
    balance = data.get("balance")
    if balance is not None:
        saldo_centavos = float(balance)
    elif spend_cap:
        saldo_centavos = max(spend_cap - amount_spent, 0)
    else:
        saldo_centavos = 0.0
    return {"saldo": saldo_centavos / 100, "moeda": currency}


def get_daily_insights(meta_account_id: str, days: int = 30, destinos: dict[str, str] | None = None) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    data = _get(
        f"{meta_account_id}/insights",
        {
            "level": "campaign",
            "time_increment": 1,
            "time_range": f'{{"since":"{since}","until":"{until}"}}',
            "fields": "date_start,campaign_id,campaign_name,objective,spend,actions",
            "limit": 500,
        },
        meta_account_id,
    )
    destinos = destinos or {}
    rows = []
    for entry in data.get("data", []):
        eh_conversa = entry.get("campaign_name") in destinos
        leads_formulario, leads_whatsapp = _contar_leads(entry.get("actions"), eh_conversa)
        rows.append(
            {
                "data": entry["date_start"],
                "campaign_id": entry.get("campaign_id"),
                "campaign_name": entry.get("campaign_name", ""),
                "gasto": float(entry.get("spend", 0) or 0),
                "leads_formulario": leads_formulario,
                "leads_whatsapp": leads_whatsapp,
            }
        )
    return rows


def get_ads_daily(
    meta_account_id: str, since: str, until: str, destinos: dict[str, str] | None = None
) -> list[dict]:
    """Desempenho dia a dia de cada anúncio no intervalo [since, until] — usado pra calcular
    o top 3 de criativos de qualquer período (mês atual, mês anterior, etc.) sem precisar
    fazer uma chamada nova pra API toda vez que o filtro de período muda no dashboard."""
    destinos = destinos or {}
    insights_range = f'{{"since":"{since}","until":"{until}"}}'
    data = _get(
        f"{meta_account_id}/ads",
        {
            # thumbnail_url vem sempre em ~64x64 (o Meta ignora thumbnail_width/height nesse
            # endpoint) — fica borrado ampliado no popup. image_url traz a imagem original em
            # resolução bem maior, mas só existe pra anúncios de imagem; vídeo cai no thumbnail.
            # .limit(100) na conexão de insights evita paginação pro tamanho de janela que usamos.
            "fields": f"name,campaign{{name,objective}},creative{{thumbnail_url,image_url}},"
            f"insights.time_range({insights_range}).time_increment(1).limit(100){{actions,spend,date_start}}",
            "limit": 200,
        },
        meta_account_id,
    )
    rows = []
    for ad in data.get("data", []):
        campaign = ad.get("campaign") or {}
        creative = ad.get("creative") or {}
        thumbnail_url = creative.get("image_url") or creative.get("thumbnail_url")
        eh_conversa = campaign.get("name") in destinos
        for dia in (ad.get("insights", {}) or {}).get("data", []):
            leads_formulario, leads_whatsapp = _contar_leads(dia.get("actions"), eh_conversa)
            rows.append(
                {
                    "ad_id": ad.get("id"),
                    "nome_anuncio": ad.get("name"),
                    "campaign_name": campaign.get("name", ""),
                    "thumbnail_url": thumbnail_url,
                    "data": dia.get("date_start"),
                    "gasto": float(dia.get("spend", 0) or 0),
                    "leads": leads_formulario + leads_whatsapp,
                }
            )
    return rows


# Com `breakdowns=region` pedido, o Meta troca os nomes das ações de lead — a mesma armadilha
# do incidente de 09/09/2026 (ver DESTINOS_CONVERSA), só que desta vez nem "lead" nem
# "onsite_conversion.messaging_conversation_started_7d" aparecem NENHUMA vez nas linhas com
# breakdown, mesmo quando a conta claramente teve os dois. Comparei os dois formatos lado a
# lado no MESMO período pra achar o substituto certo, não estou supondo:
#   sem breakdown:  lead=369                                          | messaging_conversation_started_7d=88
#   com breakdown:  onsite_conversion.lead_grouped=369 (igual)        | onsite_conversion.total_messaging_connection=87 (~igual)
# `lead_grouped` bateu exato; `total_messaging_connection` bateu a 1 de diferença (dentro do
# ruído normal de atribuição) — por isso os dois entram aqui, e SÓ aqui (a contagem principal
# em `_contar_leads` continua com `lead`/`messaging_conversation_started_7d`, intocada).
LEAD_ACTION_TYPE_REGIAO = "onsite_conversion.lead_grouped"
WHATSAPP_ACTION_TYPE_REGIAO = "onsite_conversion.total_messaging_connection"


def _contar_leads_regiao(actions: list[dict] | None, eh_campanha_de_conversa: bool) -> int:
    total = 0
    for action in actions or []:
        action_type = action.get("action_type")
        if action_type == LEAD_ACTION_TYPE_REGIAO:
            total += int(float(action.get("value", 0) or 0))
        elif action_type == WHATSAPP_ACTION_TYPE_REGIAO and eh_campanha_de_conversa:
            total += int(float(action.get("value", 0) or 0))
    return total


def get_region_performance(meta_account_id: str, days: int = 30, destinos: dict[str, str] | None = None) -> list[dict]:
    """Gasto e leads por ESTADO (breakdown `region`) — é o nível mais fino de localização
    real que o Meta expõe nos insights pra contas brasileiras. Testado direto na API: não
    existe breakdown de cidade (`city` não é aceito, `zip` é aceito no schema mas rejeitado
    na prática) — só `country`, `region` e `dma` (dma é exclusivo de mercado americano). Pra
    saber quais cidades cada campanha tem configuradas, ver `get_campaign_cidades` — é um
    dado diferente (segmentação, não resultado) e não deve ser somado com este.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    data = _get(
        f"{meta_account_id}/insights",
        {
            "level": "campaign",
            "breakdowns": "region",
            "time_range": f'{{"since":"{since}","until":"{until}"}}',
            "fields": "campaign_name,spend,actions",
            "limit": 500,
        },
        meta_account_id,
    )
    destinos = destinos or {}
    rows = []
    for entry in data.get("data", []):
        eh_conversa = entry.get("campaign_name") in destinos
        leads = _contar_leads_regiao(entry.get("actions"), eh_conversa)
        regiao = entry.get("region") or "Desconhecida"
        if regiao == "Unknown":  # o Meta devolve isso em inglês mesmo — traduz pro resto da tela
            regiao = "Desconhecida"
        rows.append(
            {
                "campaign_name": entry.get("campaign_name", ""),
                "regiao": regiao,
                "gasto": float(entry.get("spend", 0) or 0),
                "leads": leads,
            }
        )
    return rows


def get_campaign_cidades(meta_account_id: str) -> dict[str, list[dict]]:
    """Nome da campanha -> lista de {"cidade", "regiao"} que os conjuntos ATIVOS dessa
    campanha têm configurados como segmentação geográfica (`targeting.geo_locations.cities`).

    É intenção de segmentação, não resultado — uma cidade aparecer aqui não garante que
    chegou lead de lá (ver get_region_performance pro dado real), só que o anúncio está
    configurado pra tentar alcançá-la. Conjunto sem cidade específica (mirando o país inteiro,
    por exemplo) simplesmente não contribui nenhuma cidade.
    """
    data = _get(
        f"{meta_account_id}/adsets",
        {"fields": "effective_status,targeting{geo_locations},campaign{name}", "limit": 500},
        meta_account_id,
    )
    por_campanha: dict[str, dict[tuple, dict]] = {}
    for adset in data.get("data", []):
        if adset.get("effective_status") != "ACTIVE":
            continue
        campaign = adset.get("campaign") or {}
        nome_campanha = campaign.get("name")
        if not nome_campanha:
            continue
        cidades = ((adset.get("targeting") or {}).get("geo_locations") or {}).get("cities") or []
        bucket = por_campanha.setdefault(nome_campanha, {})
        for c in cidades:
            nome_cidade, regiao = c.get("name"), c.get("region")
            if not nome_cidade or not regiao:
                continue
            bucket[(nome_cidade, regiao)] = {"cidade": nome_cidade, "regiao": regiao}
    return {nome: list(mapa.values()) for nome, mapa in por_campanha.items()}


# Status de aprendizado que significam "o Meta ainda está entregando de forma conservadora,
# testando o que funciona" — enquanto o conjunto estiver aqui, CPL tende a ficar mais caro que
# quando sai (status vira "ACTIVE" no próprio learning_stage_info, não confundir com o
# effective_status do conjunto, que já é ACTIVE desde antes de aprender).
STATUS_APRENDIZADO_PRESO = {"LEARNING", "LEARNING_LIMITED"}


def get_learning_status(meta_account_id: str) -> list[dict]:
    """Conjuntos ATIVOS agora que ainda estão em fase de aprendizado (LEARNING/LEARNING_LIMITED),
    com `last_sig_edit_ts` — quando o Meta considera que o ciclo de aprendizado ATUAL começou
    (reseta a cada edição significativa: orçamento, público, criativo). Não convertemos pra
    "dias travado" aqui: quem chama decide o limiar (ver notificacoes.gerar_alertas_aprendizado_travado).

    Chamada leve e separada, no mesmo espírito de get_campaign_destinos: só campos escalares,
    sem insight nem criativo — cabe numa sincronização diária sem pesar.
    """
    data = _get(
        f"{meta_account_id}/adsets",
        {"fields": "id,name,effective_status,learning_stage_info,campaign{name}", "limit": 500},
        meta_account_id,
    )
    itens = []
    for adset in data.get("data", []):
        if adset.get("effective_status") != "ACTIVE":
            continue
        info = adset.get("learning_stage_info") or {}
        status = info.get("status")
        ts = info.get("last_sig_edit_ts")
        if status not in STATUS_APRENDIZADO_PRESO or not ts:
            continue
        campaign = adset.get("campaign") or {}
        itens.append(
            {
                "adset_id": adset.get("id"),
                "adset_nome": adset.get("name"),
                "campanha_nome": campaign.get("name", ""),
                "status": status,
                "last_sig_edit_ts": int(ts),
            }
        )
    return itens


def _construir_assinatura_publico(targeting: dict) -> str:
    """Reduz um objeto `targeting` do Meta a um texto comparável (idade, gênero, geo,
    interesses, públicos customizados/excluídos) — usado tanto pra achar conjuntos
    competindo pelo mesmo público numa marca inteira (get_targeting_signatures) quanto dentro
    de uma campanha só (get_campaign_diagnostico). Perde nuance de estrutura de propósito (ex.:
    grupos "OR" dentro do `flexible_spec` viram uma lista achatada de IDs de interesse) — o
    objetivo é achar duplicata EXATA (o caso real: BMW Auto Campo Grande, "LINHA X" e "LINHA M"
    configurados idênticos por engano, 15/09/2026), não similaridade aproximada.
    """
    interesses = sorted(
        str(spec.get("id"))
        for grupo in (targeting.get("flexible_spec") or [])
        for spec in (grupo.get("interests") or [])
    )
    publicos = sorted(str(p.get("id")) for p in (targeting.get("custom_audiences") or []))
    excluidos = sorted(str(p.get("id")) for p in (targeting.get("excluded_custom_audiences") or []))
    geo = targeting.get("geo_locations") or {}
    geo_chave = sorted(
        [c.get("key", "") for c in geo.get("cities", [])]
        + [r.get("key", "") for r in geo.get("regions", [])]
        + (geo.get("countries") or [])
    )
    return "|".join(
        [
            str(targeting.get("age_min")),
            str(targeting.get("age_max")),
            ",".join(str(g) for g in sorted(targeting.get("genders") or [])),
            ",".join(geo_chave),
            ",".join(interesses),
            ",".join(publicos),
            ",".join(excluidos),
        ]
    )


TARGETING_FIELDS = (
    "geo_locations,age_min,age_max,genders,flexible_spec,custom_audiences,excluded_custom_audiences"
)


def get_targeting_signatures(meta_account_id: str) -> list[dict]:
    """Assinatura de público de cada conjunto ATIVO agora — usada pra achar conjuntos da MESMA
    marca competindo pelo público idêntico (ver notificacoes.gerar_alertas_publico_sobreposto).
    Só compara dentro da mesma `otimizacao` (quem chama agrupa por isso também): dois conjuntos
    com público igual mas objetivo diferente (ex.: um gera cadastro, outro só engaja) não
    disputam o mesmo leilão do mesmo jeito.

    Chamada leve e separada, no mesmo espírito de get_campaign_destinos/get_learning_status.
    """
    data = _get(
        f"{meta_account_id}/adsets",
        {
            "fields": f"id,name,effective_status,optimization_goal,campaign{{name}},targeting{{{TARGETING_FIELDS}}}",
            "limit": 500,
        },
        meta_account_id,
    )
    itens = []
    for adset in data.get("data", []):
        if adset.get("effective_status") != "ACTIVE":
            continue
        campaign = adset.get("campaign") or {}
        itens.append(
            {
                "adset_id": adset.get("id"),
                "adset_nome": adset.get("name"),
                "campanha_nome": campaign.get("name", ""),
                "otimizacao": adset.get("optimization_goal"),
                "assinatura": _construir_assinatura_publico(adset.get("targeting") or {}),
            }
        )
    return itens


def _campaign_matches_filter(campaign_filter: str | None, campaign_name: str) -> bool:
    """Mesma regra de app.sync._brand_matches_campaign, duplicada aqui (não importada, pra
    não criar import circular: sync.py já importa este módulo) — "contém o texto", não
    "começa com": um prefixo exato quebrou silenciosamente a conta de Dourados quando a
    convenção de nome das campanhas mudou (ver incidente de 15/09/2026)."""
    if not campaign_filter:
        return True
    if campaign_filter.startswith("!"):
        return campaign_filter[1:].lower() not in campaign_name.lower()
    return campaign_filter.lower() in campaign_name.lower()


def get_active_campaign_names(meta_account_id: str, campaign_filter: str | None = None) -> list[dict]:
    """Campanhas com pelo menos um conjunto ATIVO agora — alimenta o seletor do diagnóstico
    de campanha no painel (buscado ao vivo, na hora que alguém abre, não fica em cache).
    Aplica o mesmo `campaign_filter` usado no resto do sistema pra separar contas
    compartilhadas entre marcas (ver _campaign_matches_filter)."""
    data = _get(
        f"{meta_account_id}/adsets",
        {"fields": "effective_status,campaign{name,objective}", "limit": 500},
        meta_account_id,
    )
    campanhas: dict[str, str] = {}
    for adset in data.get("data", []):
        if adset.get("effective_status") != "ACTIVE":
            continue
        campaign = adset.get("campaign") or {}
        nome = campaign.get("name")
        if not nome:
            continue
        if not _campaign_matches_filter(campaign_filter, nome):
            continue
        campanhas[nome] = campaign.get("objective") or ""
    return [{"nome": nome, "objetivo": objetivo} for nome, objetivo in sorted(campanhas.items())]


def get_campaign_diagnostico(meta_account_id: str, campaign_name: str, days: int = 30) -> dict:
    """Diagnóstico completo de UMA campanha, buscado ao vivo (sem cache): estrutura dos
    conjuntos (orçamento, público, destino, fase de aprendizado), desempenho diário
    (gasto/leads/CPL/CTR/CPM/frequência/alcance) e ranking de anúncios no período.

    É a mesma lógica usada pra investigar a "BMW AUTO | LEAD | SETEMBRO | CG" na mão (ver
    conversa de 15/09/2026), só generalizada pra qualquer campanha — por isso sob demanda:
    é pesado demais (adsets + insights diário + ads, 3 chamadas) pra rodar em todas as
    campanhas de todas as contas todo dia sem ninguém pedir.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    # Filtra pelo nome da campanha DIRETO na API (não busca tudo e filtra em Python): contas
    # compartilhadas entre marcas (ex.: BMW Auto + Motorrad + MINI numa só) têm adsets/ads
    # suficientes pra estourar "Please reduce the amount of data you're asking for" quando se
    # pede campos pesados (targeting, learning_stage_info) de TODOS de uma vez.
    filtro_campanha = f'[{{"field":"campaign.name","operator":"EQUAL","value":"{campaign_name}"}}]'

    adsets_data = _get(
        f"{meta_account_id}/adsets",
        {
            "fields": "id,name,effective_status,daily_budget,destination_type,optimization_goal,"
            f"learning_stage_info,targeting{{{TARGETING_FIELDS}}},"
            "campaign{name,objective,daily_budget,effective_status}",
            "filtering": filtro_campanha,
            "limit": 500,
        },
        meta_account_id,
    )
    adsets = adsets_data.get("data", [])
    if not adsets:
        raise RuntimeError(f"Campanha '{campaign_name}' não encontrada ou sem conjuntos configurados.")

    eh_conversa = any(a.get("destination_type") in DESTINOS_CONVERSA for a in adsets)
    campaign_info = adsets[0].get("campaign") or {}

    # Públicos sobrepostos DENTRO desta campanha (mesma assinatura + mesma otimização, ambos
    # ativos) — pra marcar na tabela quais conjuntos competem entre si. Não olha campanhas
    # IRMÃS da mesma marca de propósito: manter isso local ao que já foi buscado aqui evita
    # uma chamada extra só pra popular um aviso secundário do diagnóstico (o alerta de sino,
    # gerar_alertas_publico_sobreposto, já cobre a marca inteira).
    grupos_publico: dict[tuple, list[str]] = {}
    for a in adsets:
        if a.get("effective_status") != "ACTIVE":
            continue
        chave = (_construir_assinatura_publico(a.get("targeting") or {}), a.get("optimization_goal"))
        grupos_publico.setdefault(chave, []).append(a.get("name"))

    conjuntos = []
    orcamento_total = 0.0
    orcamento_ja_contado_cbo = False
    for a in adsets:
        camp = a.get("campaign") or {}
        orcamento_campanha = camp.get("daily_budget")
        if orcamento_campanha is not None:
            orcamento_adset = float(orcamento_campanha) / 100  # CBO: valor é da campanha inteira, repetido aqui só informativamente
            if a.get("effective_status") == "ACTIVE" and not orcamento_ja_contado_cbo:
                orcamento_total += orcamento_adset
                orcamento_ja_contado_cbo = True
        else:
            orcamento_adset = float(a.get("daily_budget") or 0) / 100  # ABO: cada conjunto tem o seu
            if a.get("effective_status") == "ACTIVE":
                orcamento_total += orcamento_adset
        geo = (a.get("targeting") or {}).get("geo_locations") or {}
        targeting = a.get("targeting") or {}
        chave = (_construir_assinatura_publico(targeting), a.get("optimization_goal"))
        outros_no_grupo = [nome for nome in grupos_publico.get(chave, []) if nome != a.get("name")]
        conjuntos.append(
            {
                "nome": a.get("name"),
                "status": a.get("effective_status"),
                "destino": a.get("destination_type"),
                "otimizacao": a.get("optimization_goal"),
                "orcamentoDiario": orcamento_adset,
                "orcamentoTipo": "cbo" if orcamento_campanha is not None else "abo",
                "cidadesAlvo": [c.get("name") for c in geo.get("cities", [])],
                "idadeMin": targeting.get("age_min"),
                "idadeMax": targeting.get("age_max"),
                "faseAprendizado": (a.get("learning_stage_info") or {}).get("status"),
                "publicoSobrepostoCom": outros_no_grupo if a.get("effective_status") == "ACTIVE" else [],
            }
        )

    insights_data = _get(
        f"{meta_account_id}/insights",
        {
            "level": "campaign",
            "time_increment": 1,
            "time_range": f'{{"since":"{since}","until":"{until}"}}',
            "filtering": filtro_campanha,
            "fields": "date_start,spend,actions,ctr,cpm,frequency,reach",
            "limit": 500,
        },
        meta_account_id,
    )
    diario = []
    total_gasto = 0.0
    total_leads = 0
    for row in sorted(insights_data.get("data", []), key=lambda r: r["date_start"]):
        leads_formulario, leads_whatsapp = _contar_leads(row.get("actions"), eh_conversa)
        leads = leads_formulario + leads_whatsapp
        gasto = float(row.get("spend", 0) or 0)
        total_gasto += gasto
        total_leads += leads
        diario.append(
            {
                "data": row["date_start"],
                "gasto": gasto,
                "leads": leads,
                "ctr": float(row.get("ctr", 0) or 0),
                "cpm": float(row.get("cpm", 0) or 0),
                "frequencia": float(row.get("frequency", 0) or 0),
                "alcance": int(float(row.get("reach", 0) or 0)),
            }
        )

    ads_data = _get(
        f"{meta_account_id}/ads",
        {
            "fields": "name,effective_status,campaign{name},"
            f'insights.time_range({{"since":"{since}","until":"{until}"}}){{spend,actions,ctr}}',
            "filtering": filtro_campanha,
            "limit": 200,
        },
        meta_account_id,
    )
    anuncios = []
    for ad in ads_data.get("data", []):
        linhas_insight = (ad.get("insights") or {}).get("data", [])
        row = linhas_insight[0] if linhas_insight else {}
        leads_formulario, leads_whatsapp = _contar_leads(row.get("actions"), eh_conversa)
        anuncios.append(
            {
                "nome": ad.get("name"),
                "status": ad.get("effective_status"),
                "gasto": float(row.get("spend", 0) or 0),
                "leads": leads_formulario + leads_whatsapp,
                "ctr": float(row.get("ctr", 0) or 0),
            }
        )
    anuncios.sort(key=lambda a: a["gasto"], reverse=True)

    return {
        "campanha": campaign_name,
        "objetivo": campaign_info.get("objective"),
        "statusCampanha": campaign_info.get("effective_status"),
        "orcamentoTotalDiario": orcamento_total,
        "conjuntos": conjuntos,
        "diario": diario,
        "anuncios": anuncios,
        "totalGasto": total_gasto,
        "totalLeads": total_leads,
    }
