"""Coleta comentários dos posts do Instagram.

Mensagens diretas (Instagram Direct e Messenger) NÃO entram aqui: o app
"Integração Dashboard Raviera" não tem a capacidade liberada pelo Meta — a Graph API
responde `(#3) Application does not have the capability to make this API call` mesmo com o
escopo `instagram_manage_messages` presente no token, porque isso depende de App Review
(Advanced Access), não do token. Quando/se for aprovado, o modelo ComentarioSocial já tem o
campo `canal` pronto pra receber "instagram_direct" e "messenger" sem mudar o resto.

O tempo de sincronização é a restrição principal deste módulo. Duas decisões vêm daí:

1. A listagem de posts chega de graça — instagram_client.get_media já é buscada pela
   sincronização do Instagram, e traz comments_count, permalink e legenda; aqui só
   reaproveitamos a mesma resposta.
2. Os comentários em si vão por requisição em lote da Graph API: 28 mídias levavam ~30s uma
   a uma e levam ~1,8s num lote só.

Somado ao cache de contagem (só relê os comentários de uma mídia quando o número mudou), no
regime normal isso é 1 chamada HTTP por marca.
"""

import json

import httpx

from app.config import META_API_VERSION

BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"

# Limite da própria Graph API para requisições em lote.
LOTE_MAX = 50

# Quantos comentários por mídia trazemos. Post de concessionária raramente passa disso, e o
# que interessa aqui é o que chegou recente, não o histórico completo.
COMENTARIOS_POR_MIDIA = 50


def _batch(token: str, pedidos: list[str]) -> list[dict | None]:
    """Executa vários GETs da Graph API numa única requisição HTTP.

    `pedidos` são relative_urls (ex.: "17900.../comments?limit=50"). Devolve uma lista do mesmo
    tamanho, na mesma ordem, com o corpo já decodificado — ou None onde o sub-pedido falhou
    (mídia apagada, permissão, etc.). Um sub-pedido que falha não derruba os outros.
    """
    resultados: list[dict | None] = []
    for inicio in range(0, len(pedidos), LOTE_MAX):
        fatia = pedidos[inicio : inicio + LOTE_MAX]
        lote = [{"method": "GET", "relative_url": url} for url in fatia]
        resp = httpx.post(
            BASE_URL,
            data={"access_token": token, "batch": json.dumps(lote)},
            timeout=60,
        )
        if not resp.is_success:
            raise RuntimeError(f"Instagram batch error {resp.status_code}: {resp.text[:300]}")
        for item in resp.json():
            if not item or item.get("code") != 200:
                resultados.append(None)
                continue
            try:
                resultados.append(json.loads(item["body"]))
            except (ValueError, KeyError):
                resultados.append(None)
    return resultados


def inspecionar_tokens(token_inspetor: str, tokens: list[str]) -> list[dict | None]:
    """Passa vários tokens pelo debug_token do Meta numa requisição só.

    `token_inspetor` é quem faz a pergunta (usamos o do sistema); `tokens` são os
    inspecionados. Devolve na mesma ordem, com None onde a inspeção falhou.
    """
    if not tokens:
        return []
    pedidos = [f"debug_token?input_token={t}" for t in tokens]
    return [(c or {}).get("data") if c else None for c in _batch(token_inspetor, pedidos)]


def buscar_comentarios(token: str, media_ids: list[str], nosso_username: str) -> dict[str, list[dict]]:
    """Comentários das mídias informadas, em lote.

    Sobre o autor: `username` só volta preenchido quando o token tem o escopo
    `instagram_manage_comments`. Os tokens de página guardados hoje não têm (foram gerados
    antes desta aba existir), então vem vazio nas contas que dependem deles — medido: 0 de 126
    comentários com autor pelo token de página, 18 de 18 pelo token do sistema, que tem o
    escopo. Regerar os tokens de página com `instagram_manage_comments` resolve; não depende
    de App Review, porque o app já tem essa permissão concedida.

    Sobre "respondido": quando sabemos o autor, é resposta NOSSA no fio do comentário. Quando
    não sabemos, cai pra "existe alguma resposta no fio" — em post de loja isso é quase sempre
    a própria loja atendendo, mas é aproximação, e `autor_identificado` avisa qual dos dois
    critérios valeu pra cada comentário.
    """
    if not media_ids:
        return {}
    campos = "id,text,username,timestamp,replies.limit(10)%7Bid,username,timestamp%7D"
    pedidos = [f"{mid}/comments?fields={campos}&limit={COMENTARIOS_POR_MIDIA}" for mid in media_ids]
    alvo = (nosso_username or "").lower()

    saida: dict[str, list[dict]] = {}
    for mid, corpo in zip(media_ids, _batch(token, pedidos)):
        if corpo is None:
            continue
        comentarios = []
        for c in corpo.get("data", []):
            autor = (c.get("username") or "").strip()
            if autor and autor.lower() == alvo:
                continue  # comentário da própria loja não é atendimento a receber
            respostas = (c.get("replies") or {}).get("data", [])
            if autor:
                nossa = next((r for r in respostas if (r.get("username") or "").lower() == alvo), None)
            else:
                nossa = respostas[0] if respostas else None
            comentarios.append(
                {
                    "comentario_id": c.get("id"),
                    "autor": autor or None,
                    "autor_identificado": bool(autor),
                    "texto": (c.get("text") or "").strip(),
                    "timestamp": c.get("timestamp"),
                    "respondido": nossa is not None,
                    "respondido_em": nossa.get("timestamp") if nossa else None,
                }
            )
        saida[mid] = comentarios
    return saida
