"""Regera os tokens de página do Instagram a partir de um token de usuário.

Por que isso existe: os 23 tokens de Instagram foram colados à mão, um a um, e todos foram
derivados do token PESSOAL do Felipe. Token de página herda o prazo e os escopos de quem o
gerou — então todos venciam no mesmo dia e todos ficaram sem `instagram_manage_comments`
(que é o que faz o autor do comentário vir vazio na aba de Redes Sociais).

Depois de atribuir as páginas ao usuário do sistema no Gerenciador de Negócios e gerar um
token novo com os escopos completos, este script rederiva os 23 sozinho.

Uso:
    python regerar_tokens_instagram.py                 # mostra o que faria, sem gravar
    python regerar_tokens_instagram.py --gravar        # grava de verdade

Por padrão usa o META_ACCESS_TOKEN do .env. Pra testar um token novo antes de trocar o .env:
    python regerar_tokens_instagram.py --token EAAG... --gravar
"""

import argparse
import sys
from datetime import datetime

import httpx

from app.config import META_ACCESS_TOKEN, META_API_VERSION
from app.database import SessionLocal
from app.models import InstagramAccount

BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"

# Tudo que o sistema usa hoje mais o que já sabemos que vai usar (responder comentário,
# publicar, ler mensagem quando o App Review sair). Gerar com o conjunto completo de uma vez
# é o que evita ter que refazer isso a cada recurso novo.
ESCOPOS_DESEJADOS = {
    "instagram_basic",
    "instagram_manage_insights",
    "instagram_manage_comments",
    "pages_show_list",
    "pages_read_engagement",
}


def _get(path: str, token: str, params: dict | None = None) -> dict:
    resp = httpx.get(f"{BASE_URL}/{path}", params={**(params or {}), "access_token": token}, timeout=30)
    dados = resp.json()
    if not resp.is_success:
        raise RuntimeError(dados.get("error", {}).get("message", resp.text[:200]))
    return dados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default=META_ACCESS_TOKEN, help="token de usuário de onde derivar (padrão: .env)")
    parser.add_argument("--gravar", action="store_true", help="grava no banco (sem isto, só simula)")
    args = parser.parse_args()

    if not args.token:
        print("Nenhum token informado e META_ACCESS_TOKEN está vazio.")
        return 1

    # 1) Confere quem é o token e o que ele traz, antes de qualquer coisa.
    info = _get("debug_token", args.token, {"input_token": args.token}).get("data", {})
    escopos = set(info.get("scopes", []))
    acesso_expira = info.get("data_access_expires_at") or 0
    print(f"Token informado: tipo={info.get('type')} valido={info.get('is_valid')}")
    print(
        "  prazo de acesso: "
        + ("SEM PRAZO (usuário do sistema)" if not acesso_expira else datetime.fromtimestamp(acesso_expira).strftime("%d/%m/%Y"))
    )
    faltando = ESCOPOS_DESEJADOS - escopos
    if faltando:
        print(f"  ATENÇÃO — escopos faltando: {', '.join(sorted(faltando))}")
        print("  Os tokens gerados vão herdar essa falta. Gere de novo marcando esses escopos.")
    else:
        print("  escopos: todos os necessários presentes")
    if info.get("type") != "SYSTEM_USER":
        print("  ATENÇÃO — não é token de usuário do sistema: os tokens gerados vão vencer junto com ele.")
    print()

    # 2) Lista as páginas que esse token alcança e, de cada uma, a conta de Instagram ligada.
    paginas = _get("me/accounts", args.token, {"fields": "id,name,access_token,instagram_business_account{id,username}", "limit": 100})
    por_ig: dict[str, dict] = {}
    for p in paginas.get("data", []):
        ig = p.get("instagram_business_account")
        if ig and p.get("access_token"):
            por_ig[ig["id"]] = {"token": p["access_token"], "pagina": p.get("name"), "username": ig.get("username")}
    print(f"{len(paginas.get('data', []))} páginas alcançadas, {len(por_ig)} com Instagram ligado\n")

    # 3) Cruza com o que está cadastrado no FEFO.
    db = SessionLocal()
    try:
        contas = db.query(InstagramAccount).order_by(InstagramAccount.username).all()
        atualizadas = iguais = ausentes = 0
        for conta in contas:
            novo = por_ig.get(conta.instagram_business_id)
            if not novo:
                print(f"  [SEM ACESSO] @{conta.username} — esse token não alcança essa conta")
                ausentes += 1
                continue
            if novo["token"] == conta.access_token:
                print(f"  [igual]      @{conta.username}")
                iguais += 1
                continue
            print(f"  [ATUALIZA]   @{conta.username}  (página: {novo['pagina']})")
            if args.gravar:
                conta.access_token = novo["token"]
            atualizadas += 1
        if args.gravar:
            db.commit()
        print(f"\n{atualizadas} a atualizar · {iguais} já iguais · {ausentes} fora do alcance deste token")
        if ausentes:
            print("As que ficaram fora precisam ser atribuídas ao usuário do sistema no Gerenciador de Negócios.")
        if not args.gravar:
            print("\nSimulação — nada foi gravado. Rode de novo com --gravar quando estiver satisfeito.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
