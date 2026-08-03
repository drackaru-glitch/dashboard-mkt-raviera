"""Baixa e guarda localmente imagens que vêm de URLs temporárias do Meta (miniaturas de
anúncios e de posts do Instagram). Essas URLs são assinadas e expiram depois de um tempo
(a validade fica embutida no parâmetro "oe" da própria URL — geralmente ~1 a 2 dias), então só
cachear o link não resolve: a miniatura para de carregar sozinha depois que a URL vence.
Guardando o arquivo aqui (servido em /media), ele nunca expira.
"""

import re
from pathlib import Path

import httpx

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"


def _extensao_por_content_type(content_type: str) -> str:
    if "png" in content_type:
        return "png"
    if "webp" in content_type:
        return "webp"
    return "jpg"


def _chave_segura(chave: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", chave)


def baixar_imagem(url: str | None, pasta: str, chave: str) -> str | None:
    """Baixa a imagem de `url` e salva em media/<pasta>/<chave>.<ext>, sobrescrevendo se já
    existir. Retorna o caminho servido (ex: /media/criativos/123.jpg) ou None se `url` for
    vazia ou o download falhar (nesse caso, quem chamou deve tratar como "sem miniatura")."""
    if not url:
        return None
    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True)
        if not resp.is_success:
            return None
        ext = _extensao_por_content_type(resp.headers.get("content-type", ""))
        destino_dir = MEDIA_DIR / pasta
        destino_dir.mkdir(parents=True, exist_ok=True)
        destino = destino_dir / f"{_chave_segura(chave)}.{ext}"
        destino.write_bytes(resp.content)
        return f"/media/{pasta}/{destino.name}"
    except httpx.HTTPError:
        return None
