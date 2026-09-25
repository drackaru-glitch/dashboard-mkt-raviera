"""Registro histórico: relatório diário congelado, gasto por campanha e saldo por dia.

Tudo aqui é gravado a partir de dados que a sincronização já busca — nenhuma chamada extra ao
Meta. Os relatórios diários são gravados uma vez e nunca reescritos (ver SnapshotDiario).
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    AdAccount,
    Brand,
    CampanhaDailyMetric,
    DailyMetric,
    RelatorioDiario,
    SaldoCache,
    SaldoHistorico,
    SnapshotDiario,
)

# Só congela o relatório de "ontem" depois deste horário: antes disso o Meta ainda não fechou o
# dia e uma sincronização feita à meia-noite gravaria um dia incompleto pra sempre.
HORA_MINIMA_CONGELAR = 6

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def _brl(valor: float) -> str:
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def gravar_saldo_historico(db: Session, account: AdAccount, saldo: float) -> None:
    hoje = date.today()
    linha = (
        db.query(SaldoHistorico)
        .filter(SaldoHistorico.ad_account_id == account.id, SaldoHistorico.data == hoje)
        .first()
    )
    if not linha:
        linha = SaldoHistorico(ad_account_id=account.id, data=hoje)
        db.add(linha)
    linha.saldo = saldo
    linha.atualizado_em = datetime.utcnow()


def gravar_campanhas_conta(
    db: Session,
    account_brands: list[Brand],
    insights: list[dict],
    destinos: dict[str, str],
    orcamentos: dict[str, float],
    brand_matches,
) -> int:
    """Grava gasto/leads por campanha e dia de cada marca da conta. `brand_matches` é o mesmo
    filtro de campanha por marca usado no resto da sincronização."""
    if not insights:
        return 0
    corrente_desde = date.today() - timedelta(days=1)
    gravadas = 0
    for brand in account_brands:
        linhas_marca = [r for r in insights if brand_matches(brand, r["campaign_name"])]
        if not linhas_marca:
            continue
        menor_data = min(r["data"] for r in linhas_marca)
        existentes = {
            (c.campanha, c.data): c
            for c in db.query(CampanhaDailyMetric).filter(
                CampanhaDailyMetric.brand_id == brand.id,
                CampanhaDailyMetric.data >= datetime.strptime(menor_data, "%Y-%m-%d").date(),
            )
        }
        for r in linhas_marca:
            dia = datetime.strptime(r["data"], "%Y-%m-%d").date()
            nome = r["campaign_name"]
            linha = existentes.get((nome, dia))
            if not linha:
                linha = CampanhaDailyMetric(brand_id=brand.id, campanha=nome, data=dia)
                db.add(linha)
                existentes[(nome, dia)] = linha
            linha.campaign_id = r.get("campaign_id")
            linha.gasto = r["gasto"]
            linha.leads_formulario = r["leads_formulario"]
            linha.leads_whatsapp = r["leads_whatsapp"]
            linha.destino = destinos.get(nome)
            if dia >= corrente_desde and nome in orcamentos:
                linha.orcamento_diario = orcamentos[nome]
            linha.atualizado_em = datetime.utcnow()
            gravadas += 1
    return gravadas


def montar_texto(dia: date, linhas: list[dict]) -> str:
    """Tabela por marca, no formato dos relatórios do dia a dia. `linhas`: dicts com marca,
    gasto, leads, saldo (opcional)."""
    com_gasto = sorted((l for l in linhas if l["gasto"] > 0), key=lambda l: -l["gasto"])
    sem_gasto = sorted(l["marca"] for l in linhas if l["gasto"] <= 0)
    tem_saldo = any(l.get("saldo") is not None for l in com_gasto)

    cab = "| Marca | Gasto | Leads | CPL |" + (" Saldo da conta |" if tem_saldo else "")
    sep = "|---|---:|---:|---:|" + ("---:|" if tem_saldo else "")
    out = [f"Relatório consolidado — {DIAS_SEMANA[dia.weekday()]}, {dia:%d/%m/%Y}", "", cab, sep]

    total_gasto = sum(l["gasto"] for l in com_gasto)
    total_leads = sum(l["leads"] for l in com_gasto)
    for l in com_gasto:
        cpl = _brl(l["gasto"] / l["leads"]) if l["leads"] else "—"
        linha = f"| {l['marca']} | {_brl(l['gasto'])} | {l['leads']} | {cpl} |"
        if tem_saldo:
            linha += f" {_brl(l['saldo']) if l.get('saldo') is not None else '—'} |"
        out.append(linha)
    cpl_total = _brl(total_gasto / total_leads) if total_leads else "—"
    total = f"| **Total ({len(com_gasto)} marcas)** | **{_brl(total_gasto)}** | **{total_leads}** | **{cpl_total}** |"
    if tem_saldo:
        total += " |"
    out.append(total)
    if sem_gasto:
        out += ["", "Sem gasto no dia: " + ", ".join(sem_gasto) + "."]
    return "\n".join(out)


def _criar_relatorio(db: Session, dia: date, fonte: str) -> bool:
    metricas = db.query(DailyMetric).filter(DailyMetric.data == dia).all()
    if not metricas:
        return False
    marcas = {b.id: b for b in db.query(Brand).all()}

    if fonte == "congelado":
        saldo_por_conta = {s.ad_account_id: s.saldo for s in db.query(SaldoCache).all()}
    else:
        saldo_por_conta = {
            s.ad_account_id: s.saldo for s in db.query(SaldoHistorico).filter(SaldoHistorico.data == dia)
        }

    ja_gravadas = {
        bid for (bid,) in db.query(SnapshotDiario.brand_id).filter(SnapshotDiario.data == dia)
    }
    linhas = []
    for m in metricas:
        brand = marcas.get(m.brand_id)
        if not brand:
            continue  # marca apagada depois — dado órfão, não entra no relatório
        saldo = saldo_por_conta.get(brand.ad_account_id)
        leads = (m.leads_formulario or 0) + (m.leads_whatsapp or 0)
        if brand.id not in ja_gravadas:
            db.add(
                SnapshotDiario(
                    brand_id=brand.id,
                    data=dia,
                    gasto=m.gasto or 0,
                    leads_formulario=m.leads_formulario or 0,
                    leads_whatsapp=m.leads_whatsapp or 0,
                    saldo_conta=saldo,
                    # Só a foto congelada sabe o orçamento da época; numa reconstrução o valor
                    # atual da marca seria de outra data, então fica nulo em vez de errado.
                    orcamento_planejado=brand.diario_esperado_meta if fonte == "congelado" else None,
                    orcamento_real=brand.orcamento_diario_real if fonte == "congelado" else None,
                    fonte=fonte,
                )
            )
        linhas.append({"marca": brand.nome, "gasto": m.gasto or 0, "leads": leads, "saldo": saldo})

    if not linhas:
        return False
    db.add(
        RelatorioDiario(
            data=dia,
            texto=montar_texto(dia, linhas),
            total_gasto=round(sum(l["gasto"] for l in linhas), 2),
            total_leads=sum(l["leads"] for l in linhas),
            fonte=fonte,
        )
    )
    return True


def garantir_snapshots(db: Session, dados_frescos: bool = False) -> int:
    """Cria o relatório de todo dia passado que ainda não tem um. Ontem só é congelado depois
    das 06h E quando `dados_frescos` (quem chama acabou de sincronizar com o Meta) — sem isso
    congelaria o que a última sincronização trouxe, que pode ter sido no meio do dia e ficar
    parcial pra sempre. Qualquer dia mais velho que ficou sem registro é reconstruído do
    `DailyMetric` e marcado como tal."""
    ontem = date.today() - timedelta(days=1)
    existentes = {d for (d,) in db.query(RelatorioDiario.data)}
    datas = {
        d
        for (d,) in db.query(DailyMetric.data).filter(DailyMetric.data <= ontem).distinct()
    }
    criados = 0
    for dia in sorted(datas - existentes):
        if dia == ontem:
            if not dados_frescos or datetime.now().hour < HORA_MINIMA_CONGELAR:
                continue
            fonte = "congelado"
        else:
            fonte = "reconstruido"
        if _criar_relatorio(db, dia, fonte):
            criados += 1
    return criados
