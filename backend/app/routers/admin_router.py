from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import hash_password, require_admin
from app.database import get_db
from app import concorrentes as concorrentes_svc
from app.models import AdAccount, Brand, Concorrente, GoogleAdsAccount, InstagramAccount, User, UserBrand
from app.schemas import (
    AdAccountIn,
    AdAccountOut,
    BrandIn,
    BrandOut,
    ConcorrenteIn,
    ConcorrenteOut,
    ConcorrenteTextoIn,
    GoogleAdsAccountIn,
    GoogleAdsAccountOut,
    InstagramAccountIn,
    InstagramAccountOut,
    UserIn,
    UserOut,
    UserUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        nome=user.nome,
        email=user.email,
        role=user.role,
        ativo=user.ativo,
        brand_ids=[link.brand_id for link in user.brands],
        ver_saldo=user.ver_saldo,
        ver_investimento=user.ver_investimento,
        ver_funil=user.ver_funil,
        ver_noticias=user.ver_noticias,
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


# --- Contas do Instagram ---


@router.get("/instagram-accounts", response_model=list[InstagramAccountOut])
def list_instagram_accounts(db: Session = Depends(get_db)):
    return db.query(InstagramAccount).all()


@router.post("/instagram-accounts", response_model=InstagramAccountOut)
def create_instagram_account(payload: InstagramAccountIn, db: Session = Depends(get_db)):
    if db.query(InstagramAccount).filter(InstagramAccount.instagram_business_id == payload.instagram_business_id).first():
        raise HTTPException(status_code=400, detail="Já existe uma conta com esse ID do Instagram.")
    account = InstagramAccount(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/instagram-accounts/{account_id}", response_model=InstagramAccountOut)
def update_instagram_account(account_id: int, payload: InstagramAccountIn, db: Session = Depends(get_db)):
    account = db.query(InstagramAccount).filter(InstagramAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    account.instagram_business_id = payload.instagram_business_id
    account.username = payload.username
    account.nome = payload.nome
    if payload.access_token:
        account.access_token = payload.access_token
    db.commit()
    db.refresh(account)
    return account


@router.delete("/instagram-accounts/{account_id}")
def delete_instagram_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(InstagramAccount).filter(InstagramAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    db.delete(account)
    db.commit()
    return {"ok": True}


# --- Contas do Google Ads ---


@router.get("/google-ads-accounts", response_model=list[GoogleAdsAccountOut])
def list_google_ads_accounts(db: Session = Depends(get_db)):
    return db.query(GoogleAdsAccount).all()


@router.post("/google-ads-accounts", response_model=GoogleAdsAccountOut)
def create_google_ads_account(payload: GoogleAdsAccountIn, db: Session = Depends(get_db)):
    if db.query(GoogleAdsAccount).filter(GoogleAdsAccount.customer_id == payload.customer_id).first():
        raise HTTPException(status_code=400, detail="Já existe uma conta com esse Customer ID.")
    account = GoogleAdsAccount(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/google-ads-accounts/{account_id}", response_model=GoogleAdsAccountOut)
def update_google_ads_account(account_id: int, payload: GoogleAdsAccountIn, db: Session = Depends(get_db)):
    account = db.query(GoogleAdsAccount).filter(GoogleAdsAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    account.customer_id = payload.customer_id
    account.nome = payload.nome
    db.commit()
    db.refresh(account)
    return account


@router.delete("/google-ads-accounts/{account_id}")
def delete_google_ads_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(GoogleAdsAccount).filter(GoogleAdsAccount.id == account_id).first()
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
    user = User(
        nome=payload.nome,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        ativo=True,
        ver_saldo=payload.ver_saldo,
        ver_investimento=payload.ver_investimento,
        ver_funil=payload.ver_funil,
        ver_noticias=payload.ver_noticias,
    )
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
    if payload.nome is not None:
        user.nome = payload.nome
    if payload.role is not None:
        user.role = payload.role
    if payload.ativo is not None:
        user.ativo = payload.ativo
    if payload.password:
        user.password_hash = hash_password(payload.password)
    if payload.ver_saldo is not None:
        user.ver_saldo = payload.ver_saldo
    if payload.ver_investimento is not None:
        user.ver_investimento = payload.ver_investimento
    if payload.ver_funil is not None:
        user.ver_funil = payload.ver_funil
    if payload.ver_noticias is not None:
        user.ver_noticias = payload.ver_noticias
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


# --- Concorrentes (Insights da Concorrência) ---


@router.get("/concorrentes", response_model=list[ConcorrenteOut])
def list_concorrentes(db: Session = Depends(get_db)):
    return db.query(Concorrente).order_by(Concorrente.nome).all()


@router.post("/concorrentes", response_model=ConcorrenteOut)
def create_concorrente(payload: ConcorrenteIn, db: Session = Depends(get_db)):
    if db.query(Concorrente).filter(Concorrente.pagina == payload.pagina).first():
        raise HTTPException(status_code=400, detail="Já existe um concorrente com essa página.")
    conc = Concorrente(**payload.model_dump())
    db.add(conc)
    db.commit()
    db.refresh(conc)
    return conc


@router.put("/concorrentes/{concorrente_id}", response_model=ConcorrenteOut)
def update_concorrente(concorrente_id: int, payload: ConcorrenteIn, db: Session = Depends(get_db)):
    conc = db.query(Concorrente).filter(Concorrente.id == concorrente_id).first()
    if not conc:
        raise HTTPException(status_code=404, detail="Concorrente não encontrado.")
    for campo, valor in payload.model_dump().items():
        setattr(conc, campo, valor)
    db.commit()
    db.refresh(conc)
    return conc


@router.delete("/concorrentes/{concorrente_id}")
def delete_concorrente(concorrente_id: int, db: Session = Depends(get_db)):
    conc = db.query(Concorrente).filter(Concorrente.id == concorrente_id).first()
    if not conc:
        raise HTTPException(status_code=404, detail="Concorrente não encontrado.")
    db.delete(conc)
    db.commit()
    return {"ok": True}


@router.post("/concorrentes/analisar")
def analisar_texto(payload: ConcorrenteTextoIn, db: Session = Depends(get_db)):
    """Prévia: mostra quais anunciantes vieram no texto colado, sem gravar nada."""
    return concorrentes_svc.analisar(db, payload.texto)


@router.post("/concorrentes/importar")
def importar_texto(payload: ConcorrenteTextoIn, db: Session = Depends(get_db)):
    return concorrentes_svc.importar(db, payload.texto, payload.paginas)
