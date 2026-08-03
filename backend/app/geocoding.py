"""Geocodificação de cidades (Nominatim/OpenStreetMap), com cache permanente em
GeocodeCache — cada cidade só é consultada na API uma única vez.
"""

import re
import time

import httpx
from sqlalchemy.orm import Session

from app.models import GeocodeCache

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "dashboard-mkt-raviera/1.0 (uso interno, contato: drackaru@gmail.com)"

_last_request_at = 0.0


def _respect_rate_limit():
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_request_at = time.monotonic()


def _clean_estado(estado: str) -> str:
    """O Google Ads devolve nomes tipo 'State of Mato Grosso do Sul' — o Nominatim
    só reconhece o nome real ('Mato Grosso do Sul'), então tira esse prefixo."""
    if not estado:
        return ""
    return re.sub(r"^state of\s+", "", estado, flags=re.IGNORECASE).strip()


def _query_nominatim(params: dict) -> tuple[float, float] | None:
    resp = httpx.get(NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    if not resp.is_success:
        return None
    results = resp.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def _lookup_nominatim(cidade: str, estado: str) -> tuple[float, float] | None:
    estado_limpo = _clean_estado(estado)

    _respect_rate_limit()
    params = {"city": cidade, "country": "Brazil", "format": "json", "limit": 1}
    if estado_limpo:
        params["state"] = estado_limpo
    coords = _query_nominatim(params)
    if coords:
        return coords

    # fallback: busca livre, mais tolerante a hierarquias diferentes (ex: microrregiões)
    _respect_rate_limit()
    query = f"{cidade}, {estado_limpo}, Brazil" if estado_limpo else f"{cidade}, Brazil"
    return _query_nominatim({"q": query, "format": "json", "limit": 1})


def get_coordinates(db: Session, cidade: str, estado: str) -> tuple[float, float] | None:
    """Retorna (lat, lon) pra uma cidade, geocodificando e cacheando se necessário."""
    cache = db.query(GeocodeCache).filter(GeocodeCache.cidade == cidade, GeocodeCache.estado == estado).first()
    if cache:
        if not cache.encontrado:
            return None
        return cache.latitude, cache.longitude

    coords = _lookup_nominatim(cidade, estado)
    cache = GeocodeCache(cidade=cidade, estado=estado)
    if coords:
        cache.latitude, cache.longitude = coords
        cache.encontrado = True
    else:
        cache.encontrado = False
    db.add(cache)
    db.flush()
    return coords
