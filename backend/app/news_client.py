"""Coleta manchetes recentes de portais automotivos e filtra por marca.

v1 sem IA: não resume nem analisa nada, só pega o título/link das notícias cujo título
contém o nome de uma das marcas que vendemos. Quando a chave da Anthropic estiver
configurada, isso pode evoluir pra sintetizar insights de verdade a partir dessas mesmas
notícias, em vez de listar manchetes cruas.

Sites oficiais das montadoras (Toyota, BMW, Jeep etc.) não têm RSS público acessível
(bloqueiam acesso automatizado ou não expõem feed) — por isso a v1 usa só portais gerais
que cobrem todas as marcas.
"""

from datetime import datetime, timezone

import feedparser
import httpx

FEEDS = [
    {"nome": "Quatro Rodas", "url": "https://quatrorodas.abril.com.br/feed/"},
    {"nome": "Motor1 Brasil", "url": "https://motor1.uol.com.br/rss/articles/all/"},
    {"nome": "AutoPapo", "url": "https://autopapo.com.br/feed/"},
]

# Nomes de fabricante usados pra casar com o texto das notícias — não são as marcas internas
# (lojas), e sim os veículos que elas vendem (ex: "Xapuri Motors Toyota" vende "Toyota").
MARCAS_CHAVE = ["Toyota", "Nissan", "BMW", "Jeep", "MINI", "Ram", "BYD", "Hyundai", "Land Rover", "Jaguar"]


# UA de navegador de verdade — alguns portais (AutoPapo) bloqueiam UAs curtos/genéricos.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _baixar_feed(url: str) -> feedparser.FeedParserDict:
    resp = httpx.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _marcas_no_titulo(titulo: str) -> list[str]:
    titulo_lower = titulo.lower()
    return [m for m in MARCAS_CHAVE if m.lower() in titulo_lower]


def _extrair_thumbnail(entry: dict) -> str | None:
    """A foto de capa que o próprio portal usa pra chamar atenção pra matéria."""
    thumb = entry.get("media_thumbnail")
    if thumb:
        return thumb[0].get("url")
    media = entry.get("media_content")
    if media:
        imagens = [m for m in media if m.get("medium") == "image" and m.get("url")]
        if imagens:
            return imagens[0]["url"]
    enclosures = entry.get("enclosures")
    if enclosures:
        imagens = [e for e in enclosures if str(e.get("type", "")).startswith("image/") and e.get("href")]
        if imagens:
            return imagens[0]["href"]
    return None


def coletar_noticias(limite_por_feed: int = 40) -> list[dict]:
    """Retorna uma lista de {marca_chave, titulo, link, fonte, publicado_em, thumbnail_url} —
    uma linha por marca encontrada no título (uma notícia que cita duas marcas gera duas linhas)."""
    itens = []
    for feed in FEEDS:
        try:
            parsed = _baixar_feed(feed["url"])
        except httpx.HTTPError:
            continue  # portal fora do ar ou bloqueando — ignora e segue pros outros
        for entry in parsed.entries[:limite_por_feed]:
            titulo = entry.get("title", "").strip()
            if not titulo:
                continue
            marcas = _marcas_no_titulo(titulo)
            if not marcas:
                continue
            publicado_em = None
            if entry.get("published_parsed"):
                publicado_em = datetime.fromtimestamp(
                    datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).timestamp(), tz=timezone.utc
                ).replace(tzinfo=None)
            thumbnail_url = _extrair_thumbnail(entry)
            for marca in marcas:
                itens.append(
                    {
                        "marca_chave": marca,
                        "titulo": titulo,
                        "link": entry.get("link", ""),
                        "fonte": feed["nome"],
                        "publicado_em": publicado_em,
                        "thumbnail_url": thumbnail_url,
                    }
                )
    return itens
