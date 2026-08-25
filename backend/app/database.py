from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

# timeout maior no SQLite: se duas conexões tentarem escrever ao mesmo tempo (ex.: um
# request chegando durante a sincronização), espera até 30s em vez de falhar na hora com
# "database is locked".
connect_args = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
# pool_pre_ping evita erros de "conexão perdida" com bancos gerenciados (Postgres) que
# derrubam conexões ociosas — a cada uso, testa a conexão antes e reabre sozinho se precisar.
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
