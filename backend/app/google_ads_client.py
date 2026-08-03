"""Cliente REST (GAQL) da Google Ads API. Só é chamado quando GOOGLE_ADS_DEMO_MODE é False
(ver app.config) — sync.py decide quando usar isto ou gerar dados de demonstração.
"""

from datetime import date, timedelta

import httpx

from app.config import (
    GOOGLE_ADS_API_VERSION,
    GOOGLE_ADS_CLIENT_ID,
    GOOGLE_ADS_CLIENT_SECRET,
    GOOGLE_ADS_DEVELOPER_TOKEN,
    GOOGLE_ADS_LOGIN_CUSTOMER_ID,
    GOOGLE_ADS_REFRESH_TOKEN,
)

BASE_URL = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"


def _get_access_token() -> str:
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_ADS_CLIENT_ID,
            "client_secret": GOOGLE_ADS_CLIENT_SECRET,
            "refresh_token": GOOGLE_ADS_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _search(customer_id: str, query: str) -> list[dict]:
    access_token = _get_access_token()
    results = []
    page_token = None
    while True:
        payload = {"query": query}
        if page_token:
            payload["pageToken"] = page_token
        resp = httpx.post(
            f"{BASE_URL}/customers/{customer_id}/googleAds:search",
            headers={
                "Authorization": f"Bearer {access_token}",
                "developer-token": GOOGLE_ADS_DEVELOPER_TOKEN,
                "login-customer-id": GOOGLE_ADS_LOGIN_CUSTOMER_ID,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if not resp.is_success:
            raise RuntimeError(f"Google Ads API error {resp.status_code} em customer {customer_id}: {resp.text[:400]}")
        data = resp.json()
        results.extend(data.get("results", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return results


def get_account_budget_and_status(customer_id: str) -> dict:
    query = """
        SELECT campaign_budget.amount_micros, campaign.status
        FROM campaign_budget
        WHERE campaign.status = 'ENABLED'
    """
    rows = _search(customer_id, query)
    orcamento = sum(int(r.get("campaignBudget", {}).get("amountMicros", 0)) for r in rows) / 1_000_000
    status = "ENABLED" if rows else "SEM_CAMPANHAS_ATIVAS"
    return {"orcamento_diario": orcamento, "status": status}


def get_daily_metrics(customer_id: str, days: int = 30) -> list[dict]:
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = date.today().strftime("%Y-%m-%d")
    query = f"""
        SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.conversions
        FROM customer
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    rows = _search(customer_id, query)
    out = []
    for r in rows:
        seg = r.get("segments", {})
        metrics = r.get("metrics", {})
        out.append(
            {
                "data": seg.get("date"),
                "gasto": int(metrics.get("costMicros", 0)) / 1_000_000,
                "cliques": int(metrics.get("clicks", 0)),
                "conversoes": float(metrics.get("conversions", 0)),
            }
        )
    return out


def _extract_headline(ad: dict) -> str:
    rsa = ad.get("responsiveSearchAd")
    if rsa and rsa.get("headlines"):
        return rsa["headlines"][0].get("text", "")
    eta = ad.get("expandedTextAd")
    if eta:
        return eta.get("headlinePart1", "") or ad.get("name", "")
    return ad.get("name") or f"Anúncio {ad.get('id', '')}"


def get_top_keywords(customer_id: str, days: int = 30, limit: int = 10) -> list[dict]:
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = date.today().strftime("%Y-%m-%d")
    query = f"""
        SELECT ad_group_criterion.keyword.text, metrics.clicks, metrics.conversions, metrics.cost_micros
        FROM keyword_view
        WHERE segments.date BETWEEN '{since}' AND '{until}'
        ORDER BY metrics.conversions DESC
        LIMIT {limit}
    """
    rows = _search(customer_id, query)
    out = []
    for r in rows:
        kw = r.get("adGroupCriterion", {}).get("keyword", {})
        metrics = r.get("metrics", {})
        out.append(
            {
                "texto": kw.get("text", ""),
                "cliques": int(metrics.get("clicks", 0)),
                "conversoes": float(metrics.get("conversions", 0)),
                "gasto": int(metrics.get("costMicros", 0)) / 1_000_000,
            }
        )
    return out


def get_geographic_performance(customer_id: str, days: int = 30, limit: int = 15) -> list[dict]:
    """Cidades onde as pessoas realmente estavam (LOCATION_OF_PRESENCE) ao converter/clicar."""
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = date.today().strftime("%Y-%m-%d")
    query = f"""
        SELECT segments.geo_target_city, metrics.clicks, metrics.conversions, metrics.cost_micros
        FROM geographic_view
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND geographic_view.location_type = 'LOCATION_OF_PRESENCE'
    """
    rows = _search(customer_id, query)
    agg: dict[str, dict] = {}
    for r in rows:
        city_resource = r.get("segments", {}).get("geoTargetCity")
        if not city_resource:
            continue
        metrics = r.get("metrics", {})
        entry = agg.setdefault(city_resource, {"cliques": 0, "conversoes": 0.0, "gasto": 0.0})
        entry["cliques"] += int(metrics.get("clicks", 0))
        entry["conversoes"] += float(metrics.get("conversions", 0))
        entry["gasto"] += int(metrics.get("costMicros", 0)) / 1_000_000

    if not agg:
        return []

    city_ids = [res.split("/")[-1] for res in agg.keys()]
    name_query = (
        "SELECT geo_target_constant.id, geo_target_constant.name, geo_target_constant.canonical_name "
        f"FROM geo_target_constant WHERE geo_target_constant.id IN ({', '.join(city_ids)})"
    )
    name_rows = _search(customer_id, name_query)
    names = {}
    for r in name_rows:
        gtc = r.get("geoTargetConstant", {})
        names[gtc.get("id")] = {"nome": gtc.get("name", ""), "canonico": gtc.get("canonicalName", "")}

    out = []
    for city_resource, stats in agg.items():
        city_id = city_resource.split("/")[-1]
        info = names.get(city_id, {})
        canonico = info.get("canonico", "")
        partes = [p.strip() for p in canonico.split(",")]
        estado = partes[1] if len(partes) >= 2 else ""
        out.append({"cidade": info.get("nome", city_id), "estado": estado, **stats})

    out.sort(key=lambda x: x["conversoes"], reverse=True)
    return out[:limit]


def get_top_ads(customer_id: str, days: int = 30, limit: int = 50) -> list[dict]:
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = date.today().strftime("%Y-%m-%d")
    query = f"""
        SELECT
          ad_group_ad.ad.id,
          ad_group_ad.ad.name,
          ad_group_ad.ad.responsive_search_ad.headlines,
          ad_group_ad.ad.expanded_text_ad.headline_part1,
          campaign.name,
          metrics.clicks,
          metrics.conversions,
          metrics.cost_micros
        FROM ad_group_ad
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND ad_group_ad.status = 'ENABLED'
        ORDER BY metrics.conversions DESC
        LIMIT {limit}
    """
    rows = _search(customer_id, query)
    ads = []
    for r in rows:
        ad = r.get("adGroupAd", {}).get("ad", {})
        metrics = r.get("metrics", {})
        campaign = r.get("campaign", {})
        ads.append(
            {
                "ad_id": ad.get("id"),
                "titulo": _extract_headline(ad),
                "campanha": campaign.get("name"),
                "cliques": int(metrics.get("clicks", 0)),
                "conversoes": float(metrics.get("conversions", 0)),
                "gasto": int(metrics.get("costMicros", 0)) / 1_000_000,
            }
        )
    return ads
