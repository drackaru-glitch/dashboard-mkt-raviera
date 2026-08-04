"""Migra os dados do banco SQLite atual (backend/raviera.db) pra um banco PostgreSQL novo.

Uso:
    1. Peça pro time de dev os dados de acesso do Postgres (ver checklist no guia de TI).
    2. Configure DATABASE_URL no .env apontando pro Postgres, por exemplo:
       DATABASE_URL=postgresql://usuario:senha@host:5432/nome_do_banco
    3. Rode (a partir da pasta backend/, com o ambiente virtual ativado):
       python migrate_to_postgres.py

O script sempre lê do arquivo raviera.db (independente do que estiver em .env) e escreve
no Postgres apontado por DATABASE_URL. Cria a estrutura de tabelas automaticamente e copia
os dados na ordem certa de dependência entre tabelas (chaves estrangeiras primeiro), depois
ajusta os contadores de auto-incremento do Postgres pra continuarem de onde pararam.

É seguro rodar mais de uma vez num banco Postgres vazio (recomeça do zero). Não é seguro
rodar duas vezes num banco que já tem dados migrados — isso duplicaria as linhas.
"""

import sys

from sqlalchemy import MetaData, create_engine, select

from app.config import DATABASE_URL
from app.models import Base

SQLITE_URL = "sqlite:///./raviera.db"


def main():
    if DATABASE_URL.startswith("sqlite"):
        print("ERRO: o DATABASE_URL no .env ainda aponta pra SQLite.")
        print("Configure DATABASE_URL=postgresql://usuario:senha@host:porta/banco no .env antes de rodar.")
        sys.exit(1)

    destino_censurado = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    print(f"Origem (SQLite): {SQLITE_URL}")
    print(f"Destino (Postgres): {destino_censurado}")

    origem_engine = create_engine(SQLITE_URL)
    destino_engine = create_engine(DATABASE_URL)

    print("\nCriando estrutura das tabelas no Postgres...")
    Base.metadata.create_all(bind=destino_engine)

    origem_meta = MetaData()
    origem_meta.reflect(bind=origem_engine)

    print("\nCopiando dados...")
    with origem_engine.connect() as origem_conn, destino_engine.begin() as destino_conn:
        for tabela in Base.metadata.sorted_tables:
            if tabela.name not in origem_meta.tables:
                print(f"  (pulando '{tabela.name}' — não existe no SQLite de origem)")
                continue
            origem_tabela = origem_meta.tables[tabela.name]
            linhas = origem_conn.execute(select(origem_tabela)).mappings().all()
            if not linhas:
                print(f"  {tabela.name}: 0 linhas")
                continue
            destino_conn.execute(tabela.insert(), [dict(linha) for linha in linhas])
            print(f"  {tabela.name}: {len(linhas)} linhas migradas")

    print("\nAjustando contadores de auto-incremento no Postgres...")
    colunas_id = {t.name for t in Base.metadata.sorted_tables if "id" in [c.name for c in t.columns]}
    with destino_engine.begin() as conn:
        for nome_tabela in colunas_id:
            conn.exec_driver_sql(
                f"SELECT setval(pg_get_serial_sequence('{nome_tabela}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {nome_tabela}), 1), "
                f"(SELECT MAX(id) FROM {nome_tabela}) IS NOT NULL)"
            )

    print("\nMigração concluída. Confira os dados no Postgres antes de trocar a aplicação de vez pra ele.")


if __name__ == "__main__":
    main()
