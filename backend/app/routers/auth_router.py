import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, verify_password
from app.database import get_db
from app.models import User
from app.schemas import OrdemBlocosIn

router = APIRouter(prefix="/auth", tags=["auth"])


def _permissoes(user: User) -> dict:
    # Admin sempre vê tudo, independente do que estiver salvo no banco pra essa conta.
    if user.role == "admin":
        return {"verSaldo": True, "verInvestimento": True, "verFunil": True, "verNoticias": True}
    return {
        "verSaldo": user.ver_saldo,
        "verInvestimento": user.ver_investimento,
        "verFunil": user.ver_funil,
        "verNoticias": user.ver_noticias,
    }


def _ordem_blocos(user: User) -> list[str] | None:
    if not user.ordem_blocos:
        return None
    try:
        return json.loads(user.ordem_blocos)
    except (ValueError, TypeError):
        return None


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not user.ativo or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha incorretos.")
    token = create_access_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "email": user.email,
        "nome": user.nome,
        "ordemBlocos": _ordem_blocos(user),
        **_permissoes(user),
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "role": user.role, "nome": user.nome, "ordemBlocos": _ordem_blocos(user), **_permissoes(user)}


@router.put("/me/ordem-blocos")
def salvar_ordem_blocos(body: OrdemBlocosIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Guarda a ordem em que a pessoa arrastou os quadros do painel — ligada à conta, não ao
    navegador, pra ela não precisar reorganizar de novo ao logar em outro computador."""
    user.ordem_blocos = json.dumps(body.ordem)
    db.commit()
    return {"ordemBlocos": body.ordem}
