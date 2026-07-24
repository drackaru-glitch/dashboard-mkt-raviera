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

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@raviera.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "troque-esta-senha")
