"""Texto curto pra notificação do sino quando uma versão nova sobe — separado do
CHANGELOG.md de propósito: o changelog é técnico e detalhado (pra quem mexe no código), a
notificação é uma ou duas frases pra quem só usa o painel. Escrito à mão ao fechar cada
versão; versão sem entrada aqui usa o texto genérico (ver `app.main._checar_nova_versao`).
"""

NOTAS_VERSAO: dict[str, str] = {
    "0.59": (
        "Novo: tabela Comparativo por Marca (aparece ao escolher Comparativo), relatório do dia "
        "anterior guardado no histórico, edição de marcas em Configuração e alerta de saldo por conta. "
        "Corrigido: os dias de saldo das contas que várias marcas dividem (BMW, Ram) estavam maiores "
        "que o real, e o total de saldo somava a mesma conta mais de uma vez."
    ),
    "0.58": (
        "Novo: diagnóstico de campanha (lupa ao lado de cada marca) e 4 notificações novas — "
        "alta performance, aprendizado travado, públicos sobrepostos e orçamento subutilizado. "
        "Corrigido: BMW Auto Dourados e Motorrad Dourados estavam com dados zerados há semanas "
        "por um filtro desatualizado."
    ),
}
