"""Recebe os webhooks do Followize/Dealer Space (lead criado, proposta submetida, finalização).

O Followize não assina as requisições, então a URL cadastrada no painel deles precisa incluir
"?token=" com o valor de FOLLOWIZE_WEBHOOK_TOKEN — é a única proteção contra alguém mandar
dados falsos pra esses endpoints.

Por enquanto só grava o evento bruto em FollowizeLeadEvent (ver app.models) — a agregação em
FunilDailyMetric (substituindo o modo demo) vem depois que virmos payloads reais chegando e
confirmarmos os nomes de time/marca de verdade.
"""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session
from fastapi import Depends

from app.config import FOLLOWIZE_WEBHOOK_TOKEN
from app.database import get_db
from app.models import FollowizeLeadEvent, FollowizeTeamMap

router = APIRouter(prefix="/webhooks/followize", tags=["followize"])


def _verificar_token(token: str | None):
    if not FOLLOWIZE_WEBHOOK_TOKEN or token != FOLLOWIZE_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")


def _resolver_brand_id(db: Session, team_nome: str | None) -> int | None:
    if not team_nome:
        return None
    mapa = db.query(FollowizeTeamMap).filter(FollowizeTeamMap.team_nome == team_nome).first()
    return mapa.brand_id if mapa else None


def _registrar_evento(db: Session, evento: str, payload: dict, raw_body: str, **extra):
    team_nome = (payload.get("team") or {}).get("name")
    interests = payload.get("interests") or {}
    tracking = payload.get("tracking") or {}
    registro = FollowizeLeadEvent(
        evento=evento,
        followize_lead_id=str(payload.get("id")) if payload.get("id") is not None else None,
        team_nome=team_nome,
        interesse_marca=interests.get("brand"),
        interesse_local=interests.get("location"),
        tracking_source=tracking.get("source"),
        tracking_medium=tracking.get("medium"),
        status=payload.get("status"),
        brand_id=_resolver_brand_id(db, team_nome),
        payload_bruto=raw_body,
        **extra,
    )
    db.add(registro)
    db.commit()
    return registro


@router.post("/lead-criado")
async def webhook_lead_criado(request: Request, token: str | None = None, db: Session = Depends(get_db)):
    _verificar_token(token)
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    payload = await request.json()
    _registrar_evento(db, "lead_criado", payload, raw_body)
    return {"ok": True}


@router.post("/proposta-submetida")
async def webhook_proposta_submetida(request: Request, token: str | None = None, db: Session = Depends(get_db)):
    _verificar_token(token)
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    payload = await request.json()
    _registrar_evento(db, "proposta_submetida", payload, raw_body)
    return {"ok": True}


@router.post("/finalizacao")
async def webhook_finalizacao(request: Request, token: str | None = None, db: Session = Depends(get_db)):
    _verificar_token(token)
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    payload = await request.json()
    finalization = payload.get("finalization") or {}
    _registrar_evento(
        db,
        "finalizacao",
        payload,
        raw_body,
        venda_realizada=finalization.get("sale_performed"),
        valor_venda=float(finalization["amount"]) if finalization.get("sale_performed") and finalization.get("amount") is not None else None,
    )
    return {"ok": True}
