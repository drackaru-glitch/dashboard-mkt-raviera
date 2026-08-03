import os

from dotenv import load_dotenv

load_dotenv()

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-secret-troque-em-producao")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 12

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./raviera.db")

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
META_API_VERSION = "v21.0"
META_DEMO_MODE = META_ACCESS_TOKEN == ""

# Tokens do Instagram ficam por conta (app.models.InstagramAccount.access_token), não aqui —
# cada portfólio/Business Manager tem o seu.

GOOGLE_ADS_CLIENT_ID = os.environ.get("GOOGLE_ADS_CLIENT_ID", "").strip()
GOOGLE_ADS_CLIENT_SECRET = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "").strip()
GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
GOOGLE_ADS_REFRESH_TOKEN = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "").strip()
GOOGLE_ADS_LOGIN_CUSTOMER_ID = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
GOOGLE_ADS_API_VERSION = "v23"
GOOGLE_ADS_DEMO_MODE = GOOGLE_ADS_REFRESH_TOKEN == ""

# Funil de vendas (Proposta/Venda viriam do NBS — integração descartada, sem nenhuma API
# disponível). Agora migrando para o Followize/Dealer Space via webhook. Sem eventos reais
# recebidos ainda, então continua em modo demo até o primeiro webhook chegar.
NBS_DEMO_MODE = True

# Token que protege os webhooks do Followize/Dealer Space — a URL cadastrada lá dentro do
# painel deles precisa incluir "?token=" com esse valor, já que eles não assinam as requisições.
FOLLOWIZE_WEBHOOK_TOKEN = os.environ.get("FOLLOWIZE_WEBHOOK_TOKEN", "").strip()

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@raviera.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "troque-esta-senha")
