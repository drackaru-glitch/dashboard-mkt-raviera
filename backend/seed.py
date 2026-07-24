"""Cria o primeiro usuário admin, usando ADMIN_EMAIL/ADMIN_PASSWORD do .env.
Rode uma vez: python seed.py
"""

from app.auth import hash_password
from app.config import ADMIN_EMAIL, ADMIN_PASSWORD
from app.database import Base, SessionLocal, engine
from app.models import User

Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    existing = db.query(User).filter(User.email == ADMIN_EMAIL).first()
    if existing:
        print(f"Usuário admin '{ADMIN_EMAIL}' já existe, nada a fazer.")
    else:
        admin = User(email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD), role="admin", ativo=True)
        db.add(admin)
        db.commit()
        print(f"Usuário admin criado: {ADMIN_EMAIL}")
        print("Troque a senha padrão assim que possível (via área de Configuração > Usuários).")
finally:
    db.close()
