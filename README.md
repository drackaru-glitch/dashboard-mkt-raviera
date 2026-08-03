# Raviera Cockpit

> No controle de cada decisão.

Plataforma de marketing, funil de vendas e performance do Grupo Raviera. Backend próprio (FastAPI + SQLite) com login e integração com a API do Meta; frontend em HTML + React via CDN, sem etapa de build.

## O que ele mostra

- Investimento, leads gerados e CPL médio por período (mês atual, mês anterior, últimos 7 dias ou intervalo customizado)
- Evolução diária de investimento vs. leads
- Ranking de desempenho por marca
- Saldo e runway estimado das contas de anúncio (Meta Ads), com alertas de crítico/atenção
- Melhor criativo por marca (miniatura + métrica)

## Login e níveis de acesso

- **Admin**: acesso total, inclusive a área de Configuração (marcas, contas de anúncio, usuários).
- **Gerente**: só vê as marcas atribuídas a ele.

## Como rodar localmente

```
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt      # Linux/Mac
copy .env.example .env                          # preencha os valores (ou deixe META_ACCESS_TOKEN em branco p/ modo demo)
.venv\Scripts\python seed.py                     # cria o primeiro usuário admin
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Acesse `http://127.0.0.1:8000`. Sem `META_ACCESS_TOKEN` configurado, o Raviera Cockpit funciona normalmente com dados de demonstração.

## Marcas com conta de anúncio compartilhada

Quando várias marcas usam a mesma conta do Meta (ex: BMW/MINI/MOTORRAD), configure um "filtro de campanha" por marca na área de Configuração — o prefixo do nome das campanhas daquela marca no Meta. O saldo da conta é o mesmo para todas as marcas que a compartilham (é uma verba única); gasto e leads são atribuídos por campanha.

## Fonte de dados

Saldo, gasto e leads (formulário do Meta + conversas iniciadas no WhatsApp) vêm da API do Meta, sincronizados sob demanda (botão "Atualizar" no dashboard) e armazenados no banco local (`backend/raviera.db`). O token de acesso fica só no servidor (`backend/.env`), nunca no código do cliente.

## Escopo futuro

Vendas e test-drives serão integrados em uma etapa futura, por outro sistema. Deploy no VPS da empresa: mesmo código, banco pode migrar de SQLite para Postgres trocando `DATABASE_URL`.
