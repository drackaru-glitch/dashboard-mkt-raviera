from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import hash_password, require_admin
from app.database import get_db
from app.models import AdAccount, Brand, User, UserBrand
from app.schemas import AdAccountIn, AdAccountOut, BrandIn, BrandOut, UserIn, UserOut, UserUpdate

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        ativo=user.ativo,
        brand_ids=[link.brand_id for link in user.brands],
    )


# --- Contas de anúncio ---


@router.get("/ad-accounts", response_model=list[AdAccountOut])
def list_ad_accounts(db: Session = Depends(get_db)):
    return db.query(AdAccount).all()


@router.post("/ad-accounts", response_model=AdAccountOut)
def create_ad_account(payload: AdAccountIn, db: Session = Depends(get_db)):
    if db.query(AdAccount).filter(AdAccount.meta_account_id == payload.meta_account_id).first():
        raise HTTPException(status_code=400, detail="Já existe uma conta com esse meta_account_id.")
    account = AdAccount(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/ad-accounts/{account_id}", response_model=AdAccountOut)
def update_ad_account(account_id: int, payload: AdAccountIn, db: Session = Depends(get_db)):
    account = db.query(AdAccount).filter(AdAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    account.meta_account_id = payload.meta_account_id
    account.nome = payload.nome
    db.commit()
    db.refresh(account)
    return account


@router.delete("/ad-accounts/{account_id}")
def delete_ad_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(AdAccount).filter(AdAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    db.delete(account)
    db.commit()
    return {"ok": True}


# --- Marcas ---


@router.get("/brands", response_model=list[BrandOut])
def list_brands(db: Session = Depends(get_db)):
    return db.query(Brand).all()


@router.post("/brands", response_model=BrandOut)
def create_brand(payload: BrandIn, db: Session = Depends(get_db)):
    if db.query(Brand).filter(Brand.nome == payload.nome).first():
        raise HTTPException(status_code=400, detail="Já existe uma marca com esse nome.")
    brand = Brand(**payload.model_dump())
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return brand


@router.put("/brands/{brand_id}", response_model=BrandOut)
def update_brand(brand_id: int, payload: BrandIn, db: Session = Depends(get_db)):
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Marca não encontrada.")
    for field, value in payload.model_dump().items():
        setattr(brand, field, value)
    db.commit()
    db.refresh(brand)
    return brand


@router.delete("/brands/{brand_id}")
def delete_brand(brand_id: int, db: Session = Depends(get_db)):
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Marca não encontrada.")
    db.delete(brand)
    db.commit()
    return {"ok": True}


# --- Usuários ---


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return [_user_out(u) for u in db.query(User).all()]


@router.post("/users", response_model=UserOut)
def create_user(payload: UserIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Já existe um usuário com esse e-mail.")
    user = User(email=payload.email, password_hash=hash_password(payload.password), role=payload.role, ativo=True)
    db.add(user)
    db.flush()
    for brand_id in payload.brand_ids:
        db.add(UserBrand(user_id=user.id, brand_id=brand_id))
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if payload.role is not None:
        user.role = payload.role
    if payload.ativo is not None:
        user.ativo = payload.ativo
    if payload.password:
        user.password_hash = hash_password(payload.password)
    if payload.brand_ids is not None:
        db.query(UserBrand).filter(UserBrand.user_id == user.id).delete()
        for brand_id in payload.brand_ids:
            db.add(UserBrand(user_id=user.id, brand_id=brand_id))
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    db.delete(user)
    db.commit()
    return {"ok": True}
