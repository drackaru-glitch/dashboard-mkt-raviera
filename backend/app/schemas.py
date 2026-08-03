from typing import Optional

from pydantic import BaseModel, EmailStr


class AdAccountIn(BaseModel):
    meta_account_id: str
    nome: str


class AdAccountOut(AdAccountIn):
    id: int

    class Config:
        from_attributes = True


class InstagramAccountIn(BaseModel):
    instagram_business_id: str
    username: str
    nome: str
    access_token: str = ""  # write-only: nunca é devolvido nas respostas


class InstagramAccountOut(BaseModel):
    id: int
    instagram_business_id: str
    username: str
    nome: str

    class Config:
        from_attributes = True


class GoogleAdsAccountIn(BaseModel):
    customer_id: str
    nome: str


class GoogleAdsAccountOut(GoogleAdsAccountIn):
    id: int

    class Config:
        from_attributes = True


class BrandIn(BaseModel):
    nome: str
    tier: str = ""
    ad_account_id: Optional[int] = None
    campaign_filter: Optional[str] = None
    diario_esperado_meta: float = 0
    analista: str = ""
    instagram_account_id: Optional[int] = None
    google_ads_account_id: Optional[int] = None
    google_orcamento_mensal: float = 0


class BrandOut(BrandIn):
    id: int

    class Config:
        from_attributes = True


class UserIn(BaseModel):
    nome: str = ""
    email: EmailStr
    password: str
    role: str = "gerente"
    brand_ids: list[int] = []
    ver_saldo: bool = True
    ver_investimento: bool = True
    ver_funil: bool = True
    ver_noticias: bool = True


class UserUpdate(BaseModel):
    nome: Optional[str] = None
    role: Optional[str] = None
    ativo: Optional[bool] = None
    brand_ids: Optional[list[int]] = None
    password: Optional[str] = None
    ver_saldo: Optional[bool] = None
    ver_investimento: Optional[bool] = None
    ver_funil: Optional[bool] = None
    ver_noticias: Optional[bool] = None


class UserOut(BaseModel):
    id: int
    nome: str
    email: str
    role: str
    ativo: bool
    brand_ids: list[int]
    ver_saldo: bool
    ver_investimento: bool
    ver_funil: bool
    ver_noticias: bool

    class Config:
        from_attributes = True
