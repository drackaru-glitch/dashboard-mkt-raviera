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

# Campanhas de Geração de Cadastro (OUTCOME_LEADS) às vezes geram conversa de WhatsApp como
# efeito colateral (alguém manda mensagem pela página), mas isso não é o resultado da
# campanha — só contamos "conversa iniciada" como lead quando a campanha É de
# engajamento/mensagens (OUTCOME_ENGAGEMENT), que é o objetivo usado pelas campanhas de
# WhatsApp do grupo.
OBJETIVOS_WHATSAPP = {"OUTCOME_ENGAGEMENT"}


def _contar_leads(actions: list[dict] | None, objective: str | None) -> tuple[int, int]:
    leads_formulario = 0
    leads_whatsapp = 0
    for action in actions or []:
        action_type = action.get("action_type")
        qty = int(float(action.get("value", 0) or 0))
        if action_type == LEAD_ACTION_TYPE:
            leads_formulario += qty
        elif action_type == WHATSAPP_ACTION_TYPE and objective in OBJETIVOS_WHATSAPP:
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


def get_daily_insights(meta_account_id: str, days: int = 30) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    data = _get(
        f"{meta_account_id}/insights",
        {
            "level": "campaign",
            "time_increment": 1,
            "time_range": f'{{"since":"{since}","until":"{until}"}}',
            "fields": "date_start,campaign_name,objective,spend,actions",
            "limit": 500,
        },
        meta_account_id,
    )
    rows = []
    for entry in data.get("data", []):
        leads_formulario, leads_whatsapp = _contar_leads(entry.get("actions"), entry.get("objective"))
        rows.append(
            {
                "data": entry["date_start"],
                "campaign_name": entry.get("campaign_name", ""),
                "gasto": float(entry.get("spend", 0) or 0),
                "leads_formulario": leads_formulario,
                "leads_whatsapp": leads_whatsapp,
            }
        )
    return rows


def get_ads_daily(meta_account_id: str, since: str, until: str) -> list[dict]:
    """Desempenho dia a dia de cada anúncio no intervalo [since, until] — usado pra calcular
    o top 3 de criativos de qualquer período (mês atual, mês anterior, etc.) sem precisar
    fazer uma chamada nova pra API toda vez que o filtro de período muda no dashboard."""
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
        for dia in (ad.get("insights", {}) or {}).get("data", []):
            leads_formulario, leads_whatsapp = _contar_leads(dia.get("actions"), campaign.get("objective"))
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
