from typing import Optional

from pydantic import BaseModel, EmailStr


class AdAccountIn(BaseModel):
    meta_account_id: str
    nome: str


class AdAccountOut(AdAccountIn):
    id: int

    class Config:
        from_attributes = True


class BrandIn(BaseModel):
    nome: str
    tier: str = ""
    ad_account_id: Optional[int] = None
    campaign_filter: Optional[str] = None
    diario_esperado_meta: float = 0


class BrandOut(BrandIn):
    id: int

    class Config:
        from_attributes = True


class UserIn(BaseModel):
    email: EmailStr
    password: str
    role: str = "gerente"
    brand_ids: list[int] = []


class UserUpdate(BaseModel):
    role: Optional[str] = None
    ativo: Optional[bool] = None
    brand_ids: Optional[list[int]] = None
    password: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    ativo: bool
    brand_ids: list[int]

    class Config:
        from_attributes = True
