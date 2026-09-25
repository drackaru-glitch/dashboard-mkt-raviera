"""Fôlego de saldo POR CONTA de anúncio.

O saldo pré-pago é da conta, não da marca: BMW Auto + Motorrad + MINI gastam do mesmo saldo (assim
como Ram Acre + Rondônia e BMW Dourados + Motorrad Dourados). Dividir o saldo da conta só pelo
orçamento de UMA marca dava dias a mais pra cada uma (12 dias pra Motorrad e MINI numa conta que
durava 3), e somar o saldo linha a linha contava a mesma conta duas ou três vezes.
"""

import calendar
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import Brand, DailyMetric, SaldoCache

# Ritmo = o MAIOR entre a média dos últimos 7 dias fechados e a dos últimos 3. Subir orçamento
# ontem quase não mexe numa média de 7 dias, e o saldo de uma conta que acabou de ser acelerada
# acaba bem antes do que essa média indica; já quem desacelerou fica só um pouco conservador.
# Errar pra mais gera um alerta a toa; errar pra menos deixa a conta secar sem aviso.
JANELA_DIAS = 7
JANELA_RECENTE_DIAS = 3


def _gasto_por_dia(db: Session, brand_ids: list[int], ate: date, dias: int) -> dict[date, float]:
    inicio = ate - timedelta(days=dias - 1)
    total: dict[date, float] = {}
    rows = db.query(DailyMetric.data, DailyMetric.gasto).filter(
        DailyMetric.brand_id.in_(brand_ids), DailyMetric.data >= inicio, DailyMetric.data <= ate
    )
    for dia, gasto in rows:
        total[dia] = total.get(dia, 0.0) + (gasto or 0.0)
    return total


def calcular_por_conta(db: Session, hoje: date | None = None) -> dict[int, dict]:
    """`ad_account_id` -> saldo, ritmo de gasto, dias de fôlego e quanto falta até o fim do mês.

    Considera TODAS as marcas da conta, mesmo as que quem chama não enxerga — o gasto delas sai
    do mesmo saldo. Quem expõe isso pra um usuário restrito devolve só o total, não os nomes.
    """
    hoje = hoje or date.today()
    ontem = hoje - timedelta(days=1)
    ultimo_dia = calendar.monthrange(hoje.year, hoje.month)[1]
    dias_ate_fim_do_mes = (date(hoje.year, hoje.month, ultimo_dia) - hoje).days + 1  # inclui hoje

    marcas_por_conta: dict[int, list[Brand]] = {}
    for b in db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all():
        marcas_por_conta.setdefault(b.ad_account_id, []).append(b)
    saldos = {s.ad_account_id: s for s in db.query(SaldoCache).all()}

    resultado: dict[int, dict] = {}
    for conta_id, marcas in marcas_por_conta.items():
        ids = [b.id for b in marcas]
        por_dia = _gasto_por_dia(db, ids, ontem, JANELA_DIAS)
        media_7 = sum(por_dia.get(ontem - timedelta(days=i), 0.0) for i in range(JANELA_DIAS)) / JANELA_DIAS
        media_3 = sum(por_dia.get(ontem - timedelta(days=i), 0.0) for i in range(JANELA_RECENTE_DIAS)) / JANELA_RECENTE_DIAS
        ritmo_real = max(media_7, media_3)
        planejado = sum(b.diario_esperado_meta or 0 for b in marcas)

        # Sem gasto recente (conta pausada ou recém-criada) cai no planejado; sem nenhum dos
        # dois não existe estimativa honesta, e a tela mostra "—" em vez de inventar.
        if ritmo_real > 0:
            ritmo, fonte = ritmo_real, "real"
        elif planejado > 0:
            ritmo, fonte = planejado, "planejado"
        else:
            ritmo, fonte = 0.0, None

        cache = saldos.get(conta_id)
        saldo = cache.saldo if cache else 0.0
        dias = saldo / ritmo if ritmo > 0 else None
        falta = max(0.0, ritmo * dias_ate_fim_do_mes - saldo) if ritmo > 0 else 0.0
        resultado[conta_id] = {
            "marcas": marcas,
            "saldo": saldo,
            "moeda": cache.moeda if cache else "BRL",
            "ritmo": ritmo,
            "ritmoFonte": fonte,
            "planejado": planejado,
            "dias": dias,
            "faltaAteFimDoMes": falta,
            "diasAteFimDoMes": dias_ate_fim_do_mes,
        }
    return resultado
