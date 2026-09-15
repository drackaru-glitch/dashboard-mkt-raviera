"""Recebe os webhooks da Letalk (tags do contato no WhatsApp: "Lead", "Não Responderam",
"Test Drive").

A Letalk não assina a requisição nem manda nada no header (confirmado direto na documentação
deles) — a URL cadastrada no bloco de "Enviar Webhook" do bot precisa incluir "?token=" com o
valor de LETALK_WEBHOOK_TOKEN, mesma proteção já usada pro Followize.

A Letalk não manda "de qual marca" é o contato — isso precisa vir de um campo personalizado
fixo chamado "marca" configurado em CADA fluxo de automação (um por loja/número de WhatsApp),
mapeado depois pra uma marca nossa em LetalkMarcaMap (ver app.models, preenchido à mão em
Configuração). Enquanto um fluxo não tiver esse campo, o evento ainda é gravado, só que sem
marca — nada se perde.

Por enquanto só grava o evento bruto (mesma estratégia adotada pro Followize): a Letalk manda
o estado ATUAL de todas as tags do contato a cada disparo, não um "diff" de qual tag mudou
agora — a agregação por marca/dia vem depois que virmos payloads reais chegando.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import LETALK_WEBHOOK_TOKEN
from app.database import get_db
from app.models import LetalkLeadEvent, LetalkMarcaMap

router = APIRouter(prefix="/webhooks/letalk", tags=["letalk"])


def _verificar_token(token: str | None):
    if not LETALK_WEBHOOK_TOKEN or token != LETALK_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")


def _resolver_brand_id(db: Session, marca_nome: str | None) -> int | None:
    if not marca_nome:
        return None
    mapa = db.query(LetalkMarcaMap).filter(LetalkMarcaMap.marca_nome == marca_nome).first()
    return mapa.brand_id if mapa else None


@router.post("/evento")
async def webhook_evento(request: Request, token: str | None = None, db: Session = Depends(get_db)):
    _verificar_token(token)
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    payload = await request.json()
    # O exemplo na documentação da Letalk manda um array com um objeto só — aceita objeto
    # solto também, pra não quebrar se isso vier diferente.
    if isinstance(payload, list):
        payload = payload[0] if payload else {}

    custom_fields = payload.get("custom_fields") or {}
    marca_nome = custom_fields.get("marca")

    registro = LetalkLeadEvent(
        telefone=payload.get("phone"),
        nome=payload.get("first_name"),
        email=payload.get("email"),
        marca_nome=marca_nome,
        tags=payload.get("tags"),
        brand_id=_resolver_brand_id(db, marca_nome),
        payload_bruto=raw_body,
    )
    db.add(registro)
    db.commit()
    return {"ok": True}
