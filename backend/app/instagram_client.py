"""Cliente da Graph API do Instagram (tráfego orgânico). Cada conta guarda o próprio
access_token (um por Business Manager/portfólio) — ver app.models.InstagramAccount.
sync.py decide, por conta, se usa isto ou gera dados de demonstração (quando não há token).
"""

from datetime import date, datetime, timedelta

import httpx

from app.config import META_API_VERSION

BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"


def _get(path: str, token: str, params: dict) -> dict:
    params = {**params, "access_token": token}
    resp = httpx.get(f"{BASE_URL}/{path}", params=params, timeout=30)
    if not resp.is_success:
        raise RuntimeError(f"Instagram API error {resp.status_code} em {path}: {resp.text[:300]}")
    return resp.json()


def get_followers_count(instagram_business_id: str, token: str) -> int:
    data = _get(instagram_business_id, token, {"fields": "followers_count"})
    return int(data.get("followers_count", 0) or 0)


def get_follower_gain_daily(instagram_business_id: str, token: str, days: int = 30) -> list[dict]:
    """Ganho líquido de seguidores DIA A DIA (métrica follower_count) no período.

    Devolve o valor de cada dia em vez do total somado: só assim o painel consegue recalcular
    o ganho para o período que o usuário escolher. Somar aqui, como era feito antes,
    congelava o número numa janela fixa de 30 dias corridos que não batia com mês nenhum.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    data = _get(
        f"{instagram_business_id}/insights",
        token,
        {"metric": "follower_count", "period": "day", "since": since, "until": until},
    )
    values = (data.get("data") or [{}])[0].get("values", [])
    linhas = []
    for v in values:
        fim = v.get("end_time")
        if not fim:
            continue
        # end_time é o instante do fechamento da janela do dia; a métrica se refere ao dia
        # anterior a esse fechamento.
        dia = datetime.fromisoformat(fim.replace("Z", "+00:00")).date() - timedelta(days=1)
        linhas.append({"data": dia, "ganho": int(v.get("value", 0) or 0)})
    return linhas


def get_media(instagram_business_id: str, token: str, limit: int = 50) -> list[dict]:
    """Últimos posts da conta. Uma chamada só, reaproveitada por quem precisa: o melhor post
    do período e a coleta de comentários (comments_count e permalink já vêm aqui, então
    olhar comentário de post orgânico não custa nenhuma requisição a mais)."""
    data = _get(
        f"{instagram_business_id}/media",
        token,
        {
            "fields": "id,caption,media_type,media_url,thumbnail_url,like_count,"
            "comments_count,permalink,timestamp",
            "limit": limit,
        },
    )
    return data.get("data", [])


# Havia aqui um get_top_post() que escolhia o post de mais curtidas entre os últimos 50, sem
# olhar data — era a origem do card de conteúdo orgânico mostrar, em setembro, publicação de
# maio. Escolher o melhor post é decisão de período, e período é da tela: agora guardamos
# todas as publicações datadas (InstagramPostMetric) e o recorte acontece no painel.
