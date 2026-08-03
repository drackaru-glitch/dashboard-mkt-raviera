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


def get_follower_gain(instagram_business_id: str, token: str, days: int = 30) -> int:
    """Soma o ganho líquido diário de seguidores (métrica follower_count) no período."""
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    data = _get(
        f"{instagram_business_id}/insights",
        token,
        {"metric": "follower_count", "period": "day", "since": since, "until": until},
    )
    values = (data.get("data") or [{}])[0].get("values", [])
    return sum(int(v.get("value", 0) or 0) for v in values)


def get_top_post(instagram_business_id: str, token: str, limit: int = 50) -> dict | None:
    data = _get(
        f"{instagram_business_id}/media",
        token,
        {"fields": "id,caption,media_type,media_url,thumbnail_url,like_count,comments_count,permalink,timestamp", "limit": limit},
    )
    posts = data.get("data", [])
    if not posts:
        return None
    best = max(posts, key=lambda p: p.get("like_count", 0) or 0)
    timestamp = best.get("timestamp")
    return {
        "post_id": best.get("id"),
        "legenda": (best.get("caption") or "").strip(),
        "thumbnail_url": best.get("thumbnail_url") or best.get("media_url"),
        "permalink": best.get("permalink"),
        "curtidas": int(best.get("like_count", 0) or 0),
        "comentarios": int(best.get("comments_count", 0) or 0),
        "publicado_em": datetime.fromisoformat(timestamp) if timestamp else None,
    }
