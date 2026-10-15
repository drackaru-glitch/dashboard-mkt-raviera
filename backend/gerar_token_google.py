"""Gera um novo token de renovação (refresh token) do Google Ads, confere se ele funciona nas
contas cadastradas e só então grava no .env.

Por que existe: o token antigo foi revogado/expirou (o Google responde `invalid_grant`) e a
sincronização do Google Ads parou em 03/08/2026. Ver CHANGELOG ("Conhecido / pendente").

Uso, de dentro de backend/:
    .venv\\Scripts\\python.exe gerar_token_google.py            # abre o navegador, valida e grava
    .venv\\Scripts\\python.exe gerar_token_google.py --testar   # só testa o token que JÁ está no .env

Como funciona: abre a tela de autorização do Google no navegador. Quem clica em "Permitir"
precisa ser uma conta Google com acesso à conta gerenciadora (MCC) do Google Ads. O resultado
é testado nas contas cadastradas no FEFO ANTES de qualquer coisa ser gravada, e o .env antigo
é copiado para .env.bak-<data> antes de ser alterado.

Se o Google mostrar "redirect_uri_mismatch": o cliente OAuth do projeto é do tipo "Aplicativo
da Web" e precisa ter http://localhost:8085/ nos "URIs de redirecionamento autorizados" (Google
Cloud Console > APIs e serviços > Credenciais). Se mostrar "acesso bloqueado" / "app não
verificado": a tela de consentimento está em modo Teste — adicione a conta como "usuário de
teste" ou publique o app ("Em produção"), senão o token volta a expirar em 7 dias.
"""

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from app.config import (  # noqa: E402
    GOOGLE_ADS_API_VERSION,
    GOOGLE_ADS_CLIENT_ID,
    GOOGLE_ADS_CLIENT_SECRET,
    GOOGLE_ADS_DEVELOPER_TOKEN,
    GOOGLE_ADS_LOGIN_CUSTOMER_ID,
    GOOGLE_ADS_REFRESH_TOKEN,
)

ENV = BACKEND / ".env"
ESCOPO = ["https://www.googleapis.com/auth/adwords"]
PORTA = 8085
BASE = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"


def _erro_curto(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        if isinstance(err, dict):
            return f"{resp.status_code} {err.get('status', '')} - {str(err.get('message', ''))[:200]}"
        return f"{resp.status_code} {err} - {resp.json().get('error_description', '')}"
    except Exception:
        return f"{resp.status_code} {resp.text[:200]}"


def _access_token(refresh_token: str) -> tuple[str | None, str]:
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_ADS_CLIENT_ID,
            "client_secret": GOOGLE_ADS_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    if not resp.is_success:
        return None, _erro_curto(resp)
    return resp.json()["access_token"], ""


def testar(refresh_token: str) -> bool:
    """Devolve True se o GOOGLE aceitou o token (é isso que decide se grava). Falha em conta
    específica só é relatada — não é problema do token, é de acesso daquela conta."""
    access, erro = _access_token(refresh_token)
    if not access:
        print(f"\nO Google RECUSOU o token: {erro}")
        return False
    print("\nO Google aceitou o token.")

    headers = {"Authorization": f"Bearer {access}", "developer-token": GOOGLE_ADS_DEVELOPER_TOKEN}
    r = httpx.get(f"{BASE}/customers:listAccessibleCustomers", headers=headers, timeout=30)
    if r.is_success:
        n = len(r.json().get("resourceNames", []))
        print(f"Developer token aceito. Essa conta Google enxerga {n} conta(s) do Google Ads diretamente.")
    else:
        print(f"ATENCAO - developer token / acesso: {_erro_curto(r)}")

    from app.database import SessionLocal
    from app.models import GoogleAdsAccount

    db = SessionLocal()
    try:
        contas = [(c.id, c.nome, c.customer_id) for c in db.query(GoogleAdsAccount).order_by(GoogleAdsAccount.id)]
    finally:
        db.close()

    print(f"\nTestando as {len(contas)} contas cadastradas no FEFO (login-customer-id {GOOGLE_ADS_LOGIN_CUSTOMER_ID}):")
    ok = 0
    for _, nome, customer_id in contas:
        resp = httpx.post(
            f"{BASE}/customers/{customer_id}/googleAds:search",
            headers={**headers, "login-customer-id": GOOGLE_ADS_LOGIN_CUSTOMER_ID, "Content-Type": "application/json"},
            json={"query": "SELECT customer.descriptive_name, customer.status FROM customer LIMIT 1"},
            timeout=30,
        )
        if resp.is_success:
            ok += 1
            print(f"  OK     {nome} ({customer_id})")
        else:
            print(f"  FALHOU {nome} ({customer_id}): {_erro_curto(resp)}")
    print(f"\n{ok} de {len(contas)} contas responderam.")
    return True


def gravar_token(novo: str) -> Path:
    """Troca só a linha GOOGLE_ADS_REFRESH_TOKEN, preservando o resto do .env byte a byte
    (inclusive o tipo de quebra de linha), depois de copiar o arquivo atual pra um backup."""
    backup = ENV.with_name(".env.bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(ENV, backup)
    with open(ENV, encoding="utf-8", newline="") as f:
        texto = f.read()
    novo_texto, n = re.subn(
        r"^GOOGLE_ADS_REFRESH_TOKEN=[^\r\n]*", lambda _: "GOOGLE_ADS_REFRESH_TOKEN=" + novo, texto, flags=re.M
    )
    if n == 0:
        sep = "\r\n" if "\r\n" in texto else "\n"
        novo_texto = texto + ("" if texto.endswith(("\n", "\r")) else sep) + "GOOGLE_ADS_REFRESH_TOKEN=" + novo + sep
    with open(ENV, "w", encoding="utf-8", newline="") as f:
        f.write(novo_texto)
    return backup


def main():
    if "--testar" in sys.argv:
        if not GOOGLE_ADS_REFRESH_TOKEN:
            print("Nao ha GOOGLE_ADS_REFRESH_TOKEN no .env.")
            sys.exit(1)
        sys.exit(0 if testar(GOOGLE_ADS_REFRESH_TOKEN) else 1)

    if not (GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET):
        print("Faltam GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET no .env — sem eles nao da pra autorizar.")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    # O Google as vezes devolve os escopos em outra ordem/com extras; sem isso a biblioteca
    # trata como erro.
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

    config = {
        "installed": {
            "client_id": GOOGLE_ADS_CLIENT_ID,
            "client_secret": GOOGLE_ADS_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://localhost:{PORTA}/"],
        }
    }
    flow = InstalledAppFlow.from_client_config(config, scopes=ESCOPO)

    print("Vou abrir o navegador na tela de autorizacao do Google.")
    print("Entre com a conta Google que administra a conta gerenciadora (MCC) do Google Ads e clique em 'Permitir'.")
    print("Aguardando ate 5 minutos...\n")
    try:
        creds = flow.run_local_server(
            port=PORTA,
            open_browser=True,
            access_type="offline",  # sem isso o Google nao devolve refresh token
            prompt="consent",  # forca o refresh token novo mesmo se ja tinha autorizado antes
            timeout_seconds=300,
            authorization_prompt_message="Se o navegador nao abriu, acesse: {url}",
            success_message="Autorizado! Pode fechar esta aba e voltar para a janela do FEFO.",
        )
    except Exception as exc:
        print(f"\nA autorizacao nao foi concluida: {exc}")
        sys.exit(1)

    novo = creds.refresh_token
    if not novo:
        print("\nO Google nao devolveu um refresh token. Nada foi gravado.")
        sys.exit(1)

    if not testar(novo):
        print("\nO token novo foi recusado no teste — NADA foi gravado no .env.")
        sys.exit(1)

    backup = gravar_token(novo)
    print(f"\nToken novo gravado no .env (comeca com {novo[:6]}...). Copia do .env anterior: {backup.name}")
    print("IMPORTANTE: feche o FEFO Master e abra de novo — ele so le o .env quando liga.")


if __name__ == "__main__":
    main()
