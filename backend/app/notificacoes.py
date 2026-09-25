"""Gera os itens que aparecem no sino de notificações do painel. Roda no fim de cada
sincronização (ver sync.py) — nunca é chamado direto pelo usuário.

Todos os tipos abaixo são derivados de dados que a sincronização já buscou de qualquer forma
(nenhuma chamada extra ao Meta só pra gerar alerta):
  - saldo_critico: runway (saldo ÷ orçamento planejado) entrou na faixa crítica.
  - gasto_anomalo: gasto de ontem muito acima do que a marca costuma gastar.
  - queda_leads: leads de ontem muito abaixo do normal apesar do gasto normal — é o sintoma do
    incidente de 09/09/2026 (ver meta_client.DESTINOS_CONVERSA); um alerta assim teria
    acusado aquele bug no mesmo dia.
  - alta_performance: o espelho "do bem" de queda_leads — CPL de ontem caiu muito em relação à
    média recente (mais lead por menos dinheiro). Pedido do Felipe em 15/09/2026: até aqui só
    existia alerta de problema, nada avisava quando uma campanha ia melhor que o esperado.
  - aprendizado_travado: um conjunto ATIVO preso em fase de aprendizado do Meta (LEARNING ou
    LEARNING_LIMITED) por dias demais sem sair — diferente dos outros tipos, não vem de dado já
    sincronizado (DailyMetric etc.): precisa do `learning_stage_info` do conjunto, buscado ao
    vivo por account em sync.py (ver meta_client.get_learning_status) — por isso é gerado
    dentro de _sync_meta_account, por conta/marca, não no lote de alertas do fim da sincronização
    geral como os outros.
  - publico_sobreposto: dois ou mais conjuntos ATIVOS da mesma marca com público (idade,
    gênero, geo, interesse, público customizado) e otimização idênticos — competem entre si
    pelo mesmo leilão. Igual aprendizado_travado, não vem de dado já sincronizado: precisa do
    `targeting` completo do conjunto (ver meta_client.get_targeting_signatures), buscado ao
    vivo por conta em sync.py.
  - orcamento_subutilizado: orçamento configurado no Meta bem maior que o gasto real médio dos
    últimos dias — dinheiro configurado mas não sendo usado (fase de aprendizado, público
    estreito, lance travando entrega). Diferente de orcamento_zerado: aqui o orçamento existe
    e roda, só não é gasto por completo.
  - orcamento_zerado: nenhuma campanha ativa com orçamento no Meta, mas a marca tem um
    orçamento planejado — sinal de que tudo foi pausado.
  - token_saude: token do Meta/Instagram inválido ou perto de vencer.
  - falha_sync: alguma parte da sincronização (Meta, Google, Instagram...) falhou.

O tipo "versao" é gerado à parte por app.notas_versao/_checar_nova_versao no startup do
backend, não aqui — não depende de sincronização nenhuma.
"""

import hashlib
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    Brand,
    DailyMetric,
    MetaAprendizadoAlertado,
    MetaPublicoSobrepostoAlertado,
    Notificacao,
    SaldoCache,
    TokenSaude,
)

# Mesmos limiares do runway mostrado na tela (RUNWAY_CRITICO/RUNWAY_ATENCAO em index.html).
# Duplicado de propósito — um é regra de front (cor do status), outro decide quando GERAR uma
# notificação nova; manter os dois com o mesmo número é o que importa, não compartilhar código
# entre um app React servido estático e um backend Python.
RUNWAY_CRITICO_DIAS = 3

# Gasto de ontem 50% acima da média dos últimos dias já é fora da curva pra uma conta que roda
# campanha de geração de lead (que tende a ter gasto diário estável, dentro do orçamento
# configurado) — mudança assim geralmente é orçamento reajustado, campanha nova entrando, ou
# CBO redistribuindo verba de um conjunto pausado pros outros.
LIMIAR_GASTO_ANOMALO = 1.5
# Piso em R$: sem isso, uma conta pequena (R$20/dia) dispara alerta por variação de R$12 que na
# prática não muda nada — 50% de um valor pequeno ainda é um valor pequeno.
GASTO_ANOMALO_MINIMO_ABS = 50.0
# Quantos dias de histórico ANTES de ontem formam a "curva normal" da marca.
JANELA_MEDIA_DIAS = 7

# Leads de ontem <= 50% da média já é queda relevante — o espelho do LIMIAR_GASTO_ANOMALO acima.
LIMIAR_QUEDA_LEADS = 0.5
# Só alerta se a marca gastou de verdade ontem: sem isso, toda marca com campanha pausada
# "cairia" de lead e duplicaria o alerta de orcamento_zerado com a mesma causa.
QUEDA_LEADS_GASTO_MINIMO = 30.0

# CPL de ontem <= 50% da média recente já é uma melhora de eficiência clara o suficiente pra
# avisar — o espelho "do bem" de LIMIAR_QUEDA_LEADS (mesmo número, direção oposta: lá é queda de
# LEAD, aqui é queda de CUSTO por lead, o que é bom).
LIMIAR_ALTA_PERFORMANCE = 0.5
# Sem piso de volume, 1 lead baratíssimo por sorte (ex.: R$5 num dia em que a média é R$40)
# dispararia o alerta por ruído, não por performance de verdade. Espelha o
# QUEDA_LEADS_GASTO_MINIMO, só que em quantidade de leads em vez de gasto.
ALTA_PERFORMANCE_LEADS_MINIMO = 3

# O próprio Meta considera ~7 dias (ou ~50 conversões na semana) o prazo normal pra um conjunto
# sair da fase de aprendizado — passar disso sem sair já é sinal de público/orçamento/criativo
# mal ajustado, não só "está demorando um pouco mais".
LIMIAR_DIAS_APRENDIZADO_TRAVADO = 7

# Gasto médio recente <= 60% do orçamento configurado já é subutilização clara o suficiente
# pra avisar (dinheiro parado por fase de aprendizado, público estreito ou lance travando).
LIMIAR_ORCAMENTO_SUBUTILIZADO = 0.6
# Quantos dias de gasto FECHADO (nunca hoje parcial) formam a média — exige esse tanto de
# histórico antes de avaliar, senão toda campanha recém-criada (que naturalmente ainda não usa
# o orçamento cheio nos primeiros dias, ver BMW Auto Campo Grande em 15/09/2026) dispararia à
# toa. Mesmo espírito de JANELA_MEDIA_DIAS acima, só que mais curto: aqui é sempre a média
# INTEIRA da janela (não "ontem vs. média de antes"), então uma janela menor já é estável.
ORCAMENTO_SUBUTILIZADO_DIAS = 5
# Piso em R$ do orçamento configurado — mesmo raciocínio do GASTO_ANOMALO_MINIMO_ABS: conta
# pequena (R$20/dia) sofre variação proporcional grande sem significar nada na prática.
ORCAMENTO_SUBUTILIZADO_MINIMO = 20.0

# Mesmo prazo de aviso do card de token no painel (AVISO_TOKEN_DIAS em dashboard_router.py) —
# duplicado de propósito, como o runway acima: o que importa é os dois ficarem com o mesmo
# número, não compartilhar constante entre router e módulo de notificação.
AVISO_TOKEN_DIAS = 30


def _fmt_brl(valor: float) -> str:
    texto = f"{valor:,.2f}"  # sai em formato "1,234.56" (inglês)
    return "R$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


def gerar_alertas_saldo(db: Session) -> int:
    """Notifica só na TRANSIÇÃO pra crítico — sincronizar de novo com a conta continuando crítica
    não deve gerar uma notificação nova a cada vez (rodaria 2x por dia sem parar).

    É por CONTA, não por marca: o saldo é da conta, então BMW Auto + Motorrad + MINI viravam três
    avisos iguais, e o dia de fôlego era calculado só com o orçamento de uma delas (ver
    app.runway). Sai uma notificação por conta, presa à primeira marca dela."""
    from app import runway  # import tardio: runway importa models, e este módulo é importado cedo

    criadas = 0
    for conta in runway.calcular_por_conta(db).values():
        marcas = sorted(conta["marcas"], key=lambda b: b.id)
        dias = conta["dias"]
        critico = dias is not None and dias <= RUNWAY_CRITICO_DIAS
        ja_estava_critico = all(b.ultimo_status_saldo == "critico" for b in marcas)
        if critico and not ja_estava_critico:
            nome_conta = marcas[0].ad_account.nome if marcas[0].ad_account else marcas[0].nome
            titulo = f"Saldo crítico: {nome_conta}" if len(marcas) > 1 else f"Saldo crítico: {marcas[0].nome}"
            mensagem = (
                f"Saldo de {_fmt_brl(conta['saldo'])} dura ~{round(dias, 1)} dia(s) no ritmo "
                f"{'real' if conta['ritmoFonte'] == 'real' else 'planejado'} de gasto "
                f"({_fmt_brl(conta['ritmo'])}/dia)."
            )
            if conta["faltaAteFimDoMes"] > 0:
                mensagem += f" Faltam {_fmt_brl(conta['faltaAteFimDoMes'])} pra cobrir até o fim do mês."
            if len(marcas) > 1:
                mensagem += " Conta compartilhada por: " + ", ".join(b.nome for b in marcas) + "."
            db.add(Notificacao(tipo="saldo_critico", titulo=titulo, mensagem=mensagem, brand_id=marcas[0].id))
            criadas += 1
        for b in marcas:
            b.ultimo_status_saldo = "critico" if critico else ("ok" if dias is not None else "semDados")
    return criadas


# Gasto numa campanha que NENHUMA marca da conta reivindica (o filtro de campanha de cada marca
# não casa com o nome dela) some do painel sem erro nenhum. Foi assim com a Motorrad Dourados: o
# filtro "BMW MOTORRAD DOURADOS" não casava com "LEAD | MOTORRAD | AGOSTO", e R$ 233 de setembro
# ficaram de fora. Abaixo disso é ruído (campanha de teste, centavos de alcance).
GASTO_SEM_MARCA_MINIMO = 30.0


def gerar_alerta_gasto_sem_marca(db: Session, conta, marcas: list, gasto_por_campanha_sem_marca: dict[str, float]) -> int:
    """`gasto_por_campanha_sem_marca`: campanha -> gasto na janela de sincronização, só das que
    nenhuma marca da conta reivindicou. Uma notificação por conta por dia."""
    total = sum(gasto_por_campanha_sem_marca.values())
    if total < GASTO_SEM_MARCA_MINIMO or not marcas:
        return 0
    titulo = f"Gasto sem marca: {conta.nome}"
    inicio_hoje = datetime.combine(date.today(), datetime.min.time())
    if (
        db.query(Notificacao)
        .filter(Notificacao.tipo == "gasto_sem_marca", Notificacao.titulo == titulo, Notificacao.criado_em >= inicio_hoje)
        .first()
    ):
        return 0
    maiores = sorted(gasto_por_campanha_sem_marca.items(), key=lambda kv: -kv[1])[:3]
    exemplos = "; ".join(f"\"{nome}\" ({_fmt_brl(g)})" for nome, g in maiores)
    db.add(
        Notificacao(
            tipo="gasto_sem_marca",
            titulo=titulo,
            mensagem=(
                f"{_fmt_brl(total)} gastos nos últimos dias em campanha que nenhuma marca da conta reivindica "
                f"e por isso não aparecem no painel: {exemplos}. Ajuste o filtro de campanha da marca certa em Configuração."
            ),
            brand_id=min(marcas, key=lambda b: b.id).id,
        )
    )
    return 1


def gerar_alertas_gasto_anomalo(db: Session) -> int:
    """Compara o gasto de ONTEM (dia fechado, sem risco de comparar dia parcial) com a média
    dos `JANELA_MEDIA_DIAS` dias anteriores a ontem."""
    criadas = 0
    ontem = date.today() - timedelta(days=1)
    inicio_janela = ontem - timedelta(days=JANELA_MEDIA_DIAS)
    inicio_hoje = datetime.combine(date.today(), datetime.min.time())
    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all()
    for brand in brands:
        # já alertou pra esse dia? não repete (uma marca pode ser sincronizada mais de uma vez
        # no mesmo dia se alguém clicar em "Atualizar" depois da sincronização agendada).
        ja_existe = (
            db.query(Notificacao)
            .filter(
                Notificacao.brand_id == brand.id,
                Notificacao.tipo == "gasto_anomalo",
                Notificacao.criado_em >= inicio_hoje,
            )
            .first()
        )
        if ja_existe:
            continue

        metricas = (
            db.query(DailyMetric)
            .filter(DailyMetric.brand_id == brand.id, DailyMetric.data >= inicio_janela, DailyMetric.data <= ontem)
            .all()
        )
        gasto_ontem = next((m.gasto for m in metricas if m.data == ontem), None)
        anteriores = [m.gasto for m in metricas if m.data < ontem]
        if gasto_ontem is None or len(anteriores) < 3:
            continue  # marca nova ou sem dado de ontem ainda — não dá pra comparar direito

        media = sum(anteriores) / len(anteriores)
        if media <= 0:
            continue
        aumento = gasto_ontem / media
        if aumento >= LIMIAR_GASTO_ANOMALO and (gasto_ontem - media) >= GASTO_ANOMALO_MINIMO_ABS:
            pct = round((aumento - 1) * 100)
            db.add(
                Notificacao(
                    tipo="gasto_anomalo",
                    titulo=f"Gasto acima da curva: {brand.nome}",
                    mensagem=(
                        f"Gastou {_fmt_brl(gasto_ontem)} ontem, {pct}% acima da média dos "
                        f"últimos {len(anteriores)} dias ({_fmt_brl(media)})."
                    ),
                    brand_id=brand.id,
                )
            )
            criadas += 1
    return criadas


def gerar_alertas_queda_leads(db: Session) -> int:
    """O espelho de gerar_alertas_gasto_anomalo: gasto normal (ou maior) mas os leads
    despencaram. Só alerta se a marca gastou de verdade ontem — se o gasto também caiu, a causa
    provável é outra (orçamento zerado, já coberto por gerar_alertas_orcamento_zerado)."""
    criadas = 0
    ontem = date.today() - timedelta(days=1)
    inicio_janela = ontem - timedelta(days=JANELA_MEDIA_DIAS)
    inicio_hoje = datetime.combine(date.today(), datetime.min.time())
    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all()
    for brand in brands:
        ja_existe = (
            db.query(Notificacao)
            .filter(
                Notificacao.brand_id == brand.id,
                Notificacao.tipo == "queda_leads",
                Notificacao.criado_em >= inicio_hoje,
            )
            .first()
        )
        if ja_existe:
            continue

        metricas = (
            db.query(DailyMetric)
            .filter(DailyMetric.brand_id == brand.id, DailyMetric.data >= inicio_janela, DailyMetric.data <= ontem)
            .all()
        )
        m_ontem = next((m for m in metricas if m.data == ontem), None)
        anteriores = [m for m in metricas if m.data < ontem]
        if m_ontem is None or len(anteriores) < 3:
            continue
        if m_ontem.gasto < QUEDA_LEADS_GASTO_MINIMO:
            continue  # gastou pouco ou nada ontem — queda de lead não é a história aqui

        leads_ontem = m_ontem.leads_formulario + m_ontem.leads_whatsapp
        leads_anteriores = [m.leads_formulario + m.leads_whatsapp for m in anteriores]
        media = sum(leads_anteriores) / len(leads_anteriores)
        if media < 1:
            continue  # marca que já não gerava lead nenhum — não existe "queda" pra medir

        if leads_ontem <= media * LIMIAR_QUEDA_LEADS:
            pct = round((1 - leads_ontem / media) * 100)
            db.add(
                Notificacao(
                    tipo="queda_leads",
                    titulo=f"Queda de leads: {brand.nome}",
                    mensagem=(
                        f"Só {leads_ontem} lead(s) ontem gastando {_fmt_brl(m_ontem.gasto)} — "
                        f"{pct}% abaixo da média dos últimos {len(anteriores)} dias "
                        f"({media:.1f} leads/dia)."
                    ),
                    brand_id=brand.id,
                )
            )
            criadas += 1
    return criadas


def gerar_alertas_alta_performance(db: Session) -> int:
    """O espelho "do bem" de gerar_alertas_queda_leads: CPL de ontem caiu muito em relação à
    média recente dos dias anteriores — sinal de que algo está indo melhor que o esperado
    (criativo bombando, sazonalidade, ajuste de público) e pode valer reforçar orçamento
    enquanto dura. Usa CPL (não só volume de lead) de propósito: mais lead só por ter gastado
    mais não é "performar melhor", é só ter investido mais — CPL caindo é o sinal de eficiência
    de verdade, do jeito que gasto_anomalo/queda_leads já olham pra ineficiência."""
    criadas = 0
    ontem = date.today() - timedelta(days=1)
    inicio_janela = ontem - timedelta(days=JANELA_MEDIA_DIAS)
    inicio_hoje = datetime.combine(date.today(), datetime.min.time())
    brands = db.query(Brand).filter(Brand.ad_account_id.isnot(None)).all()
    for brand in brands:
        ja_existe = (
            db.query(Notificacao)
            .filter(
                Notificacao.brand_id == brand.id,
                Notificacao.tipo == "alta_performance",
                Notificacao.criado_em >= inicio_hoje,
            )
            .first()
        )
        if ja_existe:
            continue

        metricas = (
            db.query(DailyMetric)
            .filter(DailyMetric.brand_id == brand.id, DailyMetric.data >= inicio_janela, DailyMetric.data <= ontem)
            .all()
        )
        m_ontem = next((m for m in metricas if m.data == ontem), None)
        anteriores = [m for m in metricas if m.data < ontem]
        if m_ontem is None or len(anteriores) < 3:
            continue

        leads_ontem = m_ontem.leads_formulario + m_ontem.leads_whatsapp
        if leads_ontem < ALTA_PERFORMANCE_LEADS_MINIMO or m_ontem.gasto <= 0:
            continue  # sem volume/gasto suficiente pro CPL de ontem significar algo

        cpl_ontem = m_ontem.gasto / leads_ontem
        cpls_anteriores = [
            m.gasto / (m.leads_formulario + m.leads_whatsapp)
            for m in anteriores
            if (m.leads_formulario + m.leads_whatsapp) > 0 and m.gasto > 0
        ]
        if len(cpls_anteriores) < 3:
            continue  # maioria dos dias anteriores sem lead nenhum — não dá pra comparar CPL

        media_cpl = sum(cpls_anteriores) / len(cpls_anteriores)
        if media_cpl <= 0:
            continue

        if cpl_ontem <= media_cpl * LIMIAR_ALTA_PERFORMANCE:
            pct = round((1 - cpl_ontem / media_cpl) * 100)
            db.add(
                Notificacao(
                    tipo="alta_performance",
                    titulo=f"Performando acima do esperado: {brand.nome}",
                    mensagem=(
                        f"{leads_ontem} lead(s) ontem a {_fmt_brl(cpl_ontem)} de CPL — {pct}% mais "
                        f"barato que a média dos últimos {len(cpls_anteriores)} dias com lead "
                        f"({_fmt_brl(media_cpl)}). Pode valer reforçar orçamento enquanto dura."
                    ),
                    brand_id=brand.id,
                )
            )
            criadas += 1
    return criadas


def gerar_alertas_aprendizado_travado(db: Session, brand: Brand, itens_aprendizado: list[dict]) -> int:
    """Um conjunto ativo preso em LEARNING/LEARNING_LIMITED por mais de
    LIMIAR_DIAS_APRENDIZADO_TRAVADO dias — o Meta entrega de forma conservadora enquanto isso,
    então CPL tende a ficar mais caro do que quando o aprendizado termina.

    Chamada por conta/marca de dentro de sync._sync_meta_account (não no lote de alertas do fim
    da sincronização, como os outros tipos) porque `itens_aprendizado` vem de uma busca ao vivo
    no Meta (meta_client.get_learning_status), não de tabela já sincronizada.

    `last_sig_edit_ts` do Meta já marca quando o ciclo de aprendizado ATUAL começou (reseta a
    cada edição significativa no conjunto) — por isso não guardamos "desde quando" por conta
    própria, só qual ciclo (esse timestamp) já foi avisado, em MetaAprendizadoAlertado. Se a
    pessoa editar o conjunto depois de avisado, o ciclo muda e o alerta pode disparar de novo.
    """
    criadas = 0
    agora = datetime.utcnow()
    for item in itens_aprendizado:
        dias_preso = (agora - datetime.utcfromtimestamp(item["last_sig_edit_ts"])).days
        if dias_preso < LIMIAR_DIAS_APRENDIZADO_TRAVADO:
            continue

        cache = (
            db.query(MetaAprendizadoAlertado)
            .filter(MetaAprendizadoAlertado.adset_id == item["adset_id"])
            .first()
        )
        if cache and cache.last_sig_edit_ts == item["last_sig_edit_ts"]:
            continue  # já avisou desse mesmo ciclo de aprendizado, não repete
        if not cache:
            cache = MetaAprendizadoAlertado(adset_id=item["adset_id"], last_sig_edit_ts=item["last_sig_edit_ts"])
            db.add(cache)
        else:
            cache.last_sig_edit_ts = item["last_sig_edit_ts"]
        cache.atualizado_em = agora

        rotulo_status = "aprendizado limitado" if item["status"] == "LEARNING_LIMITED" else "aprendizado"
        db.add(
            Notificacao(
                tipo="aprendizado_travado",
                titulo=f"Aprendizado travado: {brand.nome}",
                mensagem=(
                    f'"{item["adset_nome"]}" (campanha "{item["campanha_nome"]}") está preso em '
                    f"{rotulo_status} há {dias_preso} dias — considere ajustar público, orçamento "
                    f"ou criativo pra ajudar o Meta a aprender mais rápido."
                ),
                brand_id=brand.id,
            )
        )
        criadas += 1
    return criadas


def gerar_alertas_publico_sobreposto(db: Session, brand: Brand, itens_targeting: list[dict]) -> int:
    """Dois ou mais conjuntos ATIVOS da mesma marca com público e otimização IDÊNTICOS —
    competem entre si pelo mesmo leilão do Meta, o que costuma atrasar a saída de fase de
    aprendizado dos dois (caso real: BMW Auto Campo Grande, "LINHA X" e "LINHA M", 15/09/2026).

    Mesmo espírito de gerar_alertas_aprendizado_travado: chamada por conta/marca de dentro de
    sync._sync_meta_account (dado vem de busca ao vivo, meta_client.get_targeting_signatures),
    não do lote de alertas do fim da sincronização geral. Só avisa de novo se o GRUPO de
    conjuntos envolvidos mudar (ver MetaPublicoSobrepostoAlertado) — não repete todo dia
    enquanto a mesma sobreposição continuar.
    """
    criadas = 0
    por_assinatura: dict[tuple, list[dict]] = {}
    for item in itens_targeting:
        chave = (item["assinatura"], item["otimizacao"])
        por_assinatura.setdefault(chave, []).append(item)

    for (assinatura, _otimizacao), grupo in por_assinatura.items():
        if len(grupo) < 2:
            continue
        ids_ordenados = sorted(g["adset_id"] for g in grupo)
        ids_chave = ",".join(ids_ordenados)
        assinatura_hash = hashlib.sha1(f"{brand.id}:{assinatura}:{_otimizacao}".encode()).hexdigest()[:16]

        cache = (
            db.query(MetaPublicoSobrepostoAlertado)
            .filter(MetaPublicoSobrepostoAlertado.assinatura_hash == assinatura_hash)
            .first()
        )
        if cache and cache.adset_ids == ids_chave:
            continue  # já avisou desse MESMO grupo, não repete
        if not cache:
            cache = MetaPublicoSobrepostoAlertado(
                brand_id=brand.id, assinatura_hash=assinatura_hash, adset_ids=ids_chave
            )
            db.add(cache)
        else:
            cache.adset_ids = ids_chave
        cache.atualizado_em = datetime.utcnow()

        nomes = ", ".join(f'"{g["adset_nome"]}"' for g in grupo)
        db.add(
            Notificacao(
                tipo="publico_sobreposto",
                titulo=f"Públicos sobrepostos: {brand.nome}",
                mensagem=(
                    f"{nomes} miram o mesmo público e competem entre si — considere consolidar "
                    f"num conjunto só pra não dividir o aprendizado."
                ),
                brand_id=brand.id,
            )
        )
        criadas += 1
    return criadas


def gerar_alertas_orcamento_subutilizado(db: Session) -> int:
    """Orçamento configurado no Meta (Brand.orcamento_diario_real) bem maior que o gasto REAL
    médio dos últimos dias fechados — dinheiro configurado mas não sendo gasto de verdade
    (fase de aprendizado, público estreito, lance travando entrega). Diferente de
    orcamento_zerado (tudo pausado): aqui o orçamento existe e roda, só não é usado por
    completo — caso real: BMW Auto Campo Grande gastando ~36% do R$50/dia configurado.

    Só avisa uma vez por dia (mesmo padrão de gasto_anomalo/queda_leads) e exige
    ORCAMENTO_SUBUTILIZADO_DIAS de histórico — sem isso, toda campanha recém-criada dispararia
    o alerta à toa nos primeiros dias, quando subgastar é normal.
    """
    criadas = 0
    ontem = date.today() - timedelta(days=1)
    inicio_janela = ontem - timedelta(days=ORCAMENTO_SUBUTILIZADO_DIAS - 1)
    inicio_hoje = datetime.combine(date.today(), datetime.min.time())
    brands = (
        db.query(Brand)
        .filter(Brand.ad_account_id.isnot(None), Brand.orcamento_diario_real >= ORCAMENTO_SUBUTILIZADO_MINIMO)
        .all()
    )
    for brand in brands:
        ja_existe = (
            db.query(Notificacao)
            .filter(
                Notificacao.brand_id == brand.id,
                Notificacao.tipo == "orcamento_subutilizado",
                Notificacao.criado_em >= inicio_hoje,
            )
            .first()
        )
        if ja_existe:
            continue

        metricas = (
            db.query(DailyMetric)
            .filter(DailyMetric.brand_id == brand.id, DailyMetric.data >= inicio_janela, DailyMetric.data <= ontem)
            .all()
        )
        if len(metricas) < ORCAMENTO_SUBUTILIZADO_DIAS:
            continue  # marca nova ou sem histórico suficiente ainda

        media_gasto = sum(m.gasto for m in metricas) / len(metricas)
        proporcao = media_gasto / brand.orcamento_diario_real
        if proporcao <= LIMIAR_ORCAMENTO_SUBUTILIZADO:
            pct = round(proporcao * 100)
            db.add(
                Notificacao(
                    tipo="orcamento_subutilizado",
                    titulo=f"Orçamento subutilizado: {brand.nome}",
                    mensagem=(
                        f"Configurado {_fmt_brl(brand.orcamento_diario_real)}/dia, mas gastando em média "
                        f"{_fmt_brl(media_gasto)}/dia ({pct}%) nos últimos {len(metricas)} dias — pode ser "
                        f"fase de aprendizado ou público pequeno demais."
                    ),
                    brand_id=brand.id,
                )
            )
            criadas += 1
    return criadas


def gerar_alertas_orcamento_zerado(db: Session) -> int:
    """Nenhuma campanha ativa com orçamento configurado no Meta agora, mas a marca tem um
    orçamento planejado > 0 — sinal de que tudo foi pausado (pelo Meta ou por alguém)."""
    criadas = 0
    brands = (
        db.query(Brand)
        .filter(Brand.ad_account_id.isnot(None), Brand.diario_esperado_meta > 0)
        .all()
    )
    for brand in brands:
        # None = ainda não sincronizado com a versão que lê orçamento real; não é "zerado".
        zerado = brand.orcamento_diario_real is not None and brand.orcamento_diario_real <= 0
        if zerado and not brand.ultimo_orcamento_real_zerado:
            db.add(
                Notificacao(
                    tipo="orcamento_zerado",
                    titulo=f"Orçamento real zerado: {brand.nome}",
                    mensagem=(
                        f"Nenhuma campanha ativa com orçamento no Meta agora, mas o planejado "
                        f"é {_fmt_brl(brand.diario_esperado_meta)}/dia — confira se foram pausadas."
                    ),
                    brand_id=brand.id,
                )
            )
            criadas += 1
        brand.ultimo_orcamento_real_zerado = zerado
    return criadas


def gerar_alertas_token(db: Session) -> int:
    """Mesma regra do card de saúde de token do painel (`_saude_tokens` em
    dashboard_router.py): token inválido, ou válido mas a `AVISO_TOKEN_DIAS` dias (ou menos)
    de vencer. Só notifica na transição pra esse estado — não a cada sincronização."""
    criadas = 0
    agora = datetime.utcnow()
    for t in db.query(TokenSaude).all():
        prazo = t.acesso_expira_em or t.expira_em
        dias = (prazo - agora).days if prazo else None
        precisa_atencao = not t.valido or (dias is not None and dias <= AVISO_TOKEN_DIAS)
        if precisa_atencao and not t.alertado:
            if not t.valido:
                mensagem = f"Token inválido: {t.erro or 'sem detalhes do erro'}."
            else:
                mensagem = f"Vence em {dias} dia(s) — programe a renovação."
            db.add(Notificacao(tipo="token_saude", titulo=f"Token precisa de atenção: {t.rotulo}", mensagem=mensagem))
            criadas += 1
        t.alertado = precisa_atencao
    return criadas


def gerar_alerta_falha_sync(db: Session, falhas: list[str]) -> int:
    """Uma notificação só por dia resumindo as falhas da sincronização — não uma por falha
    (rodaria 2x/dia + cada "Atualizar" clicado, e a mesma causa costuma persistir por dias)."""
    if not falhas:
        return 0
    inicio_hoje = datetime.combine(date.today(), datetime.min.time())
    ja_existe = (
        db.query(Notificacao)
        .filter(Notificacao.tipo == "falha_sync", Notificacao.criado_em >= inicio_hoje)
        .first()
    )
    if ja_existe:
        return 0
    resumo = "; ".join(falhas[:5])
    if len(falhas) > 5:
        resumo += f"; e mais {len(falhas) - 5}"
    db.add(
        Notificacao(
            tipo="falha_sync",
            titulo=f"Falha na sincronização ({len(falhas)})",
            mensagem=resumo,
        )
    )
    return 1
