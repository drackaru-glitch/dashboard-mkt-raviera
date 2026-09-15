"""Texto curto pra notificação do sino quando uma versão nova sobe — separado do
CHANGELOG.md de propósito: o changelog é técnico e detalhado (pra quem mexe no código), a
notificação é uma ou duas frases pra quem só usa o painel. Escrito à mão ao fechar cada
versão; versão sem entrada aqui usa o texto genérico (ver `app.main._checar_nova_versao`).
"""

NOTAS_VERSAO: dict[str, str] = {
    "0.58": (
        "Novo: diagnóstico de campanha (lupa ao lado de cada marca) e 4 notificações novas — "
        "alta performance, aprendizado travado, públicos sobrepostos e orçamento subutilizado. "
        "Corrigido: BMW Auto Dourados e Motorrad Dourados estavam com dados zerados há semanas "
        "por um filtro desatualizado."
    ),
}
