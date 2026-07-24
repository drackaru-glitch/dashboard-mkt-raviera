"""Cliente da Graph API do Meta. Só é chamado quando META_ACCESS_TOKEN está configurado
(ver app.config.META_DEMO_MODE) — sync.py decide quando usar isto ou gerar dados de demonstração.
"""

from datetime import date, timedelta

import httpx

from app.config import META_ACCESS_TOKEN, META_API_VERSION

BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"

# action_types que a Graph API usa para lead ads e conversas de WhatsApp iniciadas via anúncio.
# Confirmar/ajustar assim que testarmos com as contas reais do usuário.
LEAD_ACTION_TYPES = {"lead", "onsite_conversion.lead_grouped"}
WHATSAPP_ACTION_TYPES = {
    "onsite_conversion.messaging_conversation_started_7d",
    "onsite_conversion.total_messaging_connection",
}


def _get(path: str, params: dict) -> dict:
    params = {**params, "access_token": META_ACCESS_TOKEN}
    resp = httpx.get(f"{BASE_URL}/{path}", params=params, timeout=30)
    if not resp.is_success:
        raise RuntimeError(f"Meta API error {resp.status_code} em {path}: {resp.text[:300]}")
    return resp.json()


def get_ad_account_balance(meta_account_id: str) -> dict:
    data = _get(meta_account_id, {"fields": "balance,amount_spent,spend_cap,currency"})
    spend_cap = float(data.get("spend_cap", 0) or 0)
    amount_spent = float(data.get("amount_spent", 0) or 0)
    balance = data.get("balance")
    if balance is not None:
        saldo = float(balance)
    elif spend_cap:
        saldo = max(spend_cap - amount_spent, 0)
    else:
        saldo = 0.0
    return {"saldo": saldo, "moeda": data.get("currency", "BRL")}


def get_daily_insights(meta_account_id: str, days: int = 30) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    data = _get(
        f"{meta_account_id}/insights",
        {
            "level": "campaign",
            "time_increment": 1,
            "time_range": f'{{"since":"{since}","until":"{until}"}}',
            "fields": "date_start,campaign_name,spend,actions",
            "limit": 500,
        },
    )
    rows = []
    for entry in data.get("data", []):
        leads_formulario = 0
        leads_whatsapp = 0
        for action in entry.get("actions", []) or []:
            action_type = action.get("action_type")
            qty = int(float(action.get("value", 0)))
            if action_type in LEAD_ACTION_TYPES:
                leads_formulario += qty
            elif action_type in WHATSAPP_ACTION_TYPES:
                leads_whatsapp += qty
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


def get_top_ad(meta_account_id: str) -> list[dict]:
    data = _get(
        f"{meta_account_id}/ads",
        {
            "fields": "name,campaign{name},creative{thumbnail_url},insights.date_preset(last_30d){actions,spend}",
            "limit": 200,
        },
    )
    ads = []
    for ad in data.get("data", []):
        insights = (ad.get("insights", {}) or {}).get("data", [])
        leads = 0
        if insights:
            for action in insights[0].get("actions", []) or []:
                if action.get("action_type") in LEAD_ACTION_TYPES | WHATSAPP_ACTION_TYPES:
                    leads += int(float(action.get("value", 0)))
        ads.append(
            {
                "ad_id": ad.get("id"),
                "nome_anuncio": ad.get("name"),
                "campaign_name": (ad.get("campaign") or {}).get("name", ""),
                "thumbnail_url": (ad.get("creative") or {}).get("thumbnail_url"),
                "leads": leads,
            }
        )
    return ads
