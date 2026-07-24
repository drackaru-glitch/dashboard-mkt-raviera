# Dashboard MKT Raviera

Dashboard de tráfego pago do Grupo Raviera. Página única em HTML + React (via CDN), sem etapa de build.

## O que ele mostra

- Investimento, leads gerados e CPL médio por período (mês atual, mês anterior, últimos 7 dias ou intervalo customizado)
- Evolução diária de investimento vs. leads
- Ranking de desempenho por marca
- Saldo e runway estimado das contas de anúncio (Meta Ads), com alertas de crítico/atenção

## Como usar

Abra `raviera_dashboard_5.html` diretamente no navegador. Não há dependências para instalar nem servidor para rodar.

## Fonte de dados

Os dados vêm de uma planilha do Google Sheets publicada como Web App do Google Apps Script (URL configurada em `APPS_SCRIPT_URL` no início do script). Se a busca falhar ou a URL não estiver configurada, o dashboard exibe dados de demonstração automaticamente.

## Escopo futuro

Vendas e test-drives serão integrados em uma etapa futura, por outro sistema.
