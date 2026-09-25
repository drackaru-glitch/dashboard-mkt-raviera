from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import allowed_brand_ids, get_current_user, require_admin
from app.database import get_db
from app.models import CampanhaDailyMetric, RelatorioDiario, SnapshotDiario, User

router = APIRouter(prefix="/api/historico", tags=["historico"])

LIMITE_LINHAS = 5000


def _filtro_marcas(query, coluna, user: User, db: Session, brand_id: int | None):
    ids = allowed_brand_ids(user, db)  # None = admin (vê tudo)
    if brand_id is not None:
        if ids is not None and brand_id not in ids:
            raise HTTPException(status_code=403, detail="Sem acesso a essa marca.")
        return query.filter(coluna == brand_id)
    if ids is not None:
        return query.filter(coluna.in_(ids))
    return query


# O texto consolidado traz TODAS as marcas numa tabela só — por isso só admin. Quem tem acesso a
# marcas específicas consulta os números delas em /snapshots e /campanhas.
@router.get("/relatorios", dependencies=[Depends(require_admin)])
def listar_relatorios(limite: int = 60, db: Session = Depends(get_db)):
    itens = db.query(RelatorioDiario).order_by(RelatorioDiario.data.desc()).limit(max(1, min(limite, 400))).all()
    return [
        {"data": r.data.isoformat(), "totalGasto": r.total_gasto, "totalLeads": r.total_leads, "fonte": r.fonte}
        for r in itens
    ]


@router.get("/relatorios/{dia}", dependencies=[Depends(require_admin)])
def obter_relatorio(dia: date, db: Session = Depends(get_db)):
    r = db.query(RelatorioDiario).filter(RelatorioDiario.data == dia).first()
    if not r:
        raise HTTPException(status_code=404, detail="Sem relatório gravado pra esse dia.")
    return {
        "data": r.data.isoformat(), "texto": r.texto, "totalGasto": r.total_gasto,
        "totalLeads": r.total_leads, "fonte": r.fonte,
    }


@router.get("/snapshots")
def listar_snapshots(
    de: date, ate: date, brand_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    q = _filtro_marcas(db.query(SnapshotDiario), SnapshotDiario.brand_id, user, db, brand_id)
    itens = (
        q.filter(SnapshotDiario.data >= de, SnapshotDiario.data <= ate)
        .order_by(SnapshotDiario.data, SnapshotDiario.brand_id).limit(LIMITE_LINHAS).all()
    )
    return [
        {
            "data": s.data.isoformat(), "brandId": s.brand_id, "marca": s.brand.nome, "gasto": s.gasto,
            "leadsFormulario": s.leads_formulario, "leadsWhatsapp": s.leads_whatsapp,
            "saldoConta": s.saldo_conta, "orcamentoPlanejado": s.orcamento_planejado,
            "orcamentoReal": s.orcamento_real, "fonte": s.fonte,
        }
        for s in itens
    ]


@router.get("/campanhas")
def listar_campanhas_historico(
    de: date, ate: date, brand_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    q = _filtro_marcas(db.query(CampanhaDailyMetric), CampanhaDailyMetric.brand_id, user, db, brand_id)
    itens = (
        q.filter(CampanhaDailyMetric.data >= de, CampanhaDailyMetric.data <= ate)
        .order_by(CampanhaDailyMetric.data, CampanhaDailyMetric.brand_id).limit(LIMITE_LINHAS).all()
    )
    return [
        {
            "data": c.data.isoformat(), "brandId": c.brand_id, "marca": c.brand.nome, "campanha": c.campanha,
            "gasto": c.gasto, "leadsFormulario": c.leads_formulario, "leadsWhatsapp": c.leads_whatsapp,
            "destino": c.destino, "orcamentoDiario": c.orcamento_diario,
        }
        for c in itens
    ]
