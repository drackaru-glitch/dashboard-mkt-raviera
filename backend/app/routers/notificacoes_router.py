from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import allowed_brand_ids, get_current_user
from app.database import get_db
from app.models import Notificacao, User

router = APIRouter(prefix="/api/notificacoes", tags=["notificacoes"])

# Quantas notificações o sino mostra — é um sino, não uma central de histórico completo.
LIMITE = 30


@router.get("")
def listar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ids_marcas = allowed_brand_ids(user, db)  # None = admin (vê tudo)

    query = db.query(Notificacao)
    if ids_marcas is not None:
        # brand_id nulo = notificação geral (nova versão) — todo mundo vê, independente de
        # quais marcas a pessoa tem acesso.
        query = query.filter((Notificacao.brand_id.is_(None)) | (Notificacao.brand_id.in_(ids_marcas)))
    itens = query.order_by(Notificacao.criado_em.desc()).limit(LIMITE).all()

    lidas_ate = user.notificacoes_lidas_ate
    nao_lidas = sum(1 for n in itens if not lidas_ate or n.criado_em > lidas_ate)

    return {
        "naoLidas": nao_lidas,
        "itens": [
            {
                "id": n.id,
                "tipo": n.tipo,
                "titulo": n.titulo,
                "mensagem": n.mensagem,
                "marca": n.brand.nome if n.brand else None,
                "criadoEm": n.criado_em.isoformat() + "Z",
                "lida": bool(lidas_ate and n.criado_em <= lidas_ate),
            }
            for n in itens
        ],
    }


@router.post("/marcar-lidas")
def marcar_lidas(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.notificacoes_lidas_ate = datetime.utcnow()
    db.commit()
    return {"ok": True}
