from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, verify_password
from app.database import get_db
from app.models import User

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
        **_permissoes(user),
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "role": user.role, "nome": user.nome, **_permissoes(user)}
