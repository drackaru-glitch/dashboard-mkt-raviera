# Documentação do Raviera Cockpit

> No controle de cada decisão.
>
> Plataforma de marketing, funil de vendas e performance (Meta Ads, Google Ads, Funil de Vendas, Notícias do Setor) do Grupo Raviera.
> Última atualização: 31/07/2026.

---

## 1. Visão Geral

O Raviera Cockpit é uma plataforma web interna que reúne, num só lugar, os dados de marketing pago do grupo:

- **Meta Ads** (Facebook/Instagram): investimento, leads, saldo das contas, melhores criativos e alcance orgânico do Instagram.
- **Google Ads**: investimento, cliques, conversões, orçamento/saldo mensal por marca, palavras-chave com melhor desempenho e mapa de calor de localidades.
- **Funil de Vendas**: Lead → Proposta → Venda, com taxa de conversão geral e por marca.
- **Assistente do Painel**: um chat flutuante que responde dúvidas comuns sobre como usar o próprio painel.

O painel é usado pela equipe de marketing (analistas Felipe e Renã) e pela administração do Grupo Raviera, cobrindo hoje 24 marcas/lojas do grupo (BMW, Toyota, Nissan, BYD, Hyundai, Jeep, MINI, entre outras).

---

## 2. Arquitetura Técnica

| Camada | Tecnologia |
|---|---|
| Backend | Python + FastAPI |
| Banco de dados | SQLite (`backend/raviera.db`) via SQLAlchemy |
| Autenticação | JWT (login por e-mail/senha) |
| Frontend | React 18 + Babel standalone (via CDN), **um único arquivo** `frontend/index.html`, sem processo de build |
| Estilo | Tailwind CSS (via CDN) |
| Servidor | Uvicorn (ASGI), roda em `http://127.0.0.1:8000` |

**Por que um arquivo só no frontend?** O projeto não usa Node/npm/Webpack — o `index.html` carrega React, Babel e Tailwind direto de CDNs e escreve JSX que o Babel compila no próprio navegador. Isso simplifica a hospedagem (o FastAPI serve esse arquivo estático diretamente), ao custo de não ser a prática recomendada para produção em larga escala (o próprio navegador avisa isso no console).

### Estrutura de pastas

```
dashboard-mkt-raviera/
├── backend/
│   ├── app/
│   │   ├── main.py              # cria o app FastAPI, monta rotas e serve o frontend
│   │   ├── config.py             # variáveis de ambiente e flags de "modo demo"
│   │   ├── database.py           # engine SQLAlchemy / sessão
│   │   ├── models.py             # todas as tabelas do banco
│   │   ├── schemas.py            # schemas Pydantic (entrada/saída da API)
│   │   ├── auth.py                # login, JWT, permissões por marca
│   │   ├── sync.py                # sincronização de dados (real ou demo)
│   │   ├── meta_client.py         # cliente da Graph API do Meta
│   │   ├── google_ads_client.py   # cliente REST/GAQL da Google Ads API
│   │   ├── instagram_client.py    # cliente da Graph API (Instagram orgânico)
│   │   ├── geocoding.py           # geocodificação de cidades (Nominatim/OSM)
│   │   └── routers/
│   │       ├── auth_router.py     # /auth/login, /auth/me
│   │       ├── admin_router.py    # CRUD de contas, marcas e usuários
│   │       └── dashboard_router.py # /api/dashboard, /api/dashboard/google, /api/dashboard/funil, /api/sync
│   ├── seed.py                    # cria o primeiro usuário admin
│   ├── requirements.txt
│   └── .env                       # credenciais (nunca commitado)
└── frontend/
    └── index.html                 # todo o frontend (React + Babel + Tailwind)
```

### Como rodar localmente

```bash
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Depois é só acessar `http://127.0.0.1:8000` no navegador. Na primeira vez, rode `python seed.py` pra criar o usuário admin inicial (usa `ADMIN_EMAIL`/`ADMIN_PASSWORD` do `.env`).

---

## 3. Autenticação e Permissões

- Login por e-mail/senha (`POST /auth/login`), retorna um token JWT válido por 12 horas.
- Dois papéis de usuário:
  - **admin**: vê todas as marcas, tem acesso à área de Configuração (contas, marcas, usuários).
  - **gerente**: vê só as marcas que foram vinculadas à sua conta (tabela `user_brands`); não vê o botão "Configuração".
- Não existe recuperação de senha por e-mail — um admin precisa redefinir a senha manualmente em Configuração → Usuários.
- O token fica salvo no `localStorage` do navegador; a sessão persiste entre recarregamentos até o usuário clicar em "Sair" ou o token expirar.

---

## 4. Dashboard Meta Ads (tela inicial)

Reúne o desempenho do tráfego pago no Meta (Facebook/Instagram Ads):

- **Filtro de equipe**: ver todas as campanhas, ou só as de um analista específico (Felipe/Renã) — a lista de analistas é montada automaticamente a partir do campo "Analista" de cada marca.
- **Filtro de período**: Mês Atual, Mês Anterior, **Comparativo** (ver seção 8) ou Customizado (escolher datas manualmente).
- **Filtro de marca**: todas ou uma marca específica.
- **KPIs**: Investimento, Leads Gerados, CPL Médio (custo por lead) e Contas em Alerta de Saldo — cada um com badge de variação (seta + %) comparando com o período anterior.
- **Gráfico "Investimento Diário vs. Leads Gerados"**: evolução no período, investimento em área/linha e leads em linha tracejada.
- **Gráfico "Desempenho por Marca"**: ranking de leads e investimento por marca, ordenado por leads.
- **Top 3 Melhores Anúncios por Marca**: os 3 criativos com mais leads de cada marca (thumbnail, investimento, leads, CPL). Lista expansível ("Ver todas").
- **Alcance Orgânico no Instagram**: por conta do Instagram vinculada, mostra seguidores atuais, seguidores ganhos no período e o post com mais curtidas. Não usa verba paga — é conteúdo orgânico.
- **Tabela "Saldo das Contas de Anúncios (Meta Ads)"**: para cada marca, mostra investimento, leads, CPL, saldo atual da conta, diário esperado de gasto e o **runway** (quantos dias o saldo ainda cobre = saldo ÷ diário esperado). Colunas são clicáveis pra ordenar. Status:
  - 🔴 **Crítico**: runway ≤ 3 dias
  - 🟡 **Atenção**: runway ≤ 7 dias
  - 🟢 **Saldo OK**: acima disso

---

## 5. Dashboard Google Ads

Acessível pelo menu cascata no topo ("Meta Ads" → clique → "Google Ads"). Estrutura parecida com o dashboard Meta, mas com métricas próprias do Google Ads:

- **KPIs**: Investimento, Cliques, Conversões e Saldo Total.
- **Impacto das Palavras-Chave por Marca**: palavras-chave com mais conversões de cada marca (sem imagens de anúncio — o Google Ads não expõe isso da mesma forma que o Meta). Lista expansível.
- **Mapa de Calor por Localidade**: cidades onde as pessoas estavam ao clicar/converter (não é um mapa geográfico visual — é uma lista ordenada por intensidade de conversão, com barra de calor). Lista expansível.
- **Tabela "Orçamento das Contas (Google Ads)"**: investimento, cliques, conversões, **Orçamento Mensal** (configurado manualmente por marca em Configuração), **gasto do mês atual** e **Saldo Atual** (= orçamento mensal − gasto do mês). Fica vermelho quando negativo.

### Particularidades técnicas do Google Ads

- A API do Google Ads **não tem** um conceito de "saldo restante" pra esse tipo de conta (billing por "budget order" sem data de término) — por isso o saldo aqui é calculado a partir de um orçamento mensal definido manualmente pela equipe, e não vem direto da API do Google.
- Por uma restrição da própria API do Google (`keyword_view` não pode ser cruzado com segmentação geográfica na mesma consulta), palavras-chave e localidades são buscadas em consultas separadas — não dá pra saber "qual palavra-chave converteu em qual cidade" ao mesmo tempo.

---

## 6. Funil de Vendas

Terceira opção do menu cascata. Mostra o funil **Lead → Proposta → Venda**, cobrindo só o tráfego pago do Meta Ads (não o funil completo da loja, que inclui outras origens).

- **Leads**: dado real, vem das mesmas métricas do Meta Ads.
- **Propostas** e **Vendas**: viriam do sistema NBS (CRM da concessionária) via integração por API — **ainda não integrado**, então esses números estão em **modo demonstração** (gerados a partir da quantidade real de leads, com uma taxa de conversão simulada por marca).
- **Cards de KPI**: Leads, Propostas, Vendas e Taxa de Conversão geral.
- **Desenho do funil**: três blocos afunilando (gradiente azul → roxo → verde), com o valor e a porcentagem do total dentro de cada etapa, e a taxa de conversão entre as etapas escrita ao lado, fora do desenho.
- **Tabela por marca**: mesmas métricas e taxas (Lead→Proposta, Proposta→Venda, Taxa Geral) quebradas por marca.

Quando a API do NBS for integrada, é só substituir a geração demo de propostas/vendas pela chamada real — toda a estrutura (endpoint, filtros de período/marca, visual do funil) já está pronta.

---

## 7. Comparativo de Período

Disponível nos três dashboards (Meta, Google Ads, Funil), na barra de período:

- **Mês Atual**, **Mês Anterior**, **Customizado**: funcionam como o nome diz.
- **Comparativo**: mostra o mês atual (dia 1 até hoje) e permite escolher contra o que comparar:
  - **Mês Anterior**: mesmo intervalo de dias no mês anterior (ex: dia 1 a 28 de julho vs dia 1 a 28 de junho).
  - **Mesmo Mês Ano Passado**: mesmo intervalo de dias, mas no mesmo mês do ano anterior (ex: julho/2026 vs julho/2025).

O motivo de usar o **mesmo recorte de dias** nos dois períodos (em vez do mês anterior inteiro) é evitar distorção: comparar um mês parcial com um mês completo infla artificialmente a diferença percentual.

Cada KPI mostra, além do valor atual, um chip com o valor do período comparado (ex: "mês anterior: R$ 6.118") e o badge de variação percentual (seta + %).

---

## 8. Painel de Configuração (só admin)

Acessível pelo botão "Configuração" no topo (só aparece pra usuários com papel `admin`).

| Seção | O que gerencia |
|---|---|
| Contas de Anúncio (Meta) | `meta_account_id` (ex: `act_123456789`) + nome de cada conta de anúncio do Meta |
| Contas do Instagram | ID da conta (Graph API), usuário, nome e o **access token** daquele Business Manager (nunca é reexibido depois de salvo) |
| Contas do Google Ads | Customer ID (sem hífen) + nome |
| Marcas | Vincula cada marca a uma conta Meta, conta Instagram e conta Google Ads; define tier, analista responsável, filtro de campanha (contas compartilhadas), diário esperado de gasto (Meta) e orçamento mensal (Google) |
| Usuários | Cria logins, define papel (admin/gerente) e, pra gerentes, quais marcas cada um pode ver |

**Filtro de campanha**: usado quando duas marcas dividem a mesma conta de anúncio do Meta — só campanhas cujo nome começa com o prefixo definido contam pra aquela marca.

---

## 9. Sincronização de Dados

O botão de atualizar (ícone circular, ao lado de "Atualizado às...") dispara `POST /api/sync`, que:

1. Busca saldo, gasto diário e leads de cada conta do **Meta Ads** (ou gera dados demo, se `META_ACCESS_TOKEN` não estiver configurado).
2. Busca os 3 melhores criativos por marca.
3. Sincroniza seguidores e o post em destaque de cada conta do **Instagram**.
4. Busca orçamento, métricas diárias, palavras-chave e localidades do **Google Ads** (ou gera dados demo, se as credenciais não estiverem configuradas).
5. Gera os números do **Funil de Vendas** (sempre em modo demo até o NBS ser integrado).

Só uma sincronização roda por vez (lock interno) — se tentar iniciar outra enquanto uma está rodando, a API responde com erro 409.

### Modo demonstração

Cada integração externa (Meta, Google Ads, NBS) tem uma flag de "modo demo" que liga automaticamente quando as credenciais correspondentes não estão configuradas no `.env` do servidor. Nesses casos, o painel mostra um aviso amarelo no topo da tela avisando que os dados ali são fictícios (gerados de forma determinística, só pra preencher o layout).

---

## 10. Assistente do Painel (chat flutuante)

Botão azul flutuante no canto inferior direito, presente em todas as telas. Ao clicar, abre uma janela de chat.

**Como funciona hoje**: não é uma IA — é uma base de perguntas frequentes com gatilhos por palavra-chave. Cada pergunta cadastrada tem uma lista de palavras/frases-gatilho; quando a pessoa digita algo, o sistema procura qual gatilho tem **todas as suas palavras** presentes na pergunta (em qualquer ordem) e responde com o texto pronto correspondente. Se nada bater, ele sugere exemplos do que pode perguntar.

Temas cobertos hoje: comparativo de período, filtro por marca/equipe, sincronização, saldo (Meta e Google Ads), funil de vendas, palavras-chave, mapa de calor, troca de dashboard, tema claro/escuro, cadastro de marcas/usuários, recuperação de senha, modo demonstração, logo da empresa, top criativos, alcance orgânico, CPL/CPA e ordenação de tabelas.

**Evolução futura planejada**: conectar a um modelo de IA de verdade (API da Anthropic/Claude) pra responder perguntas abertas usando os dados reais do dashboard como contexto — ainda não configurado, pois depende de uma chave de API paga.

---

## 11. Integrações Externas

| Integração | Status | Observação |
|---|---|---|
| Meta Graph API (Ads Insights + Instagram) | ✅ Ativa | Usa `META_ACCESS_TOKEN`; token do Instagram é por conta (Business Manager) |
| Google Ads API (GAQL, REST) | ✅ Ativa | v23, autenticação OAuth2 com refresh token; MCC "Grupo Raviera" gerencia as 14 contas filhas |
| Nominatim/OpenStreetMap (geocodificação) | ✅ Ativa (uso interno) | Usada para tentar geocodificar cidades do mapa de calor; atualmente não há mais nenhuma visualização de mapa geográfico consumindo essas coordenadas — é um resquício de uma versão anterior do mapa de calor (ver seção 12) |
| NBS (CRM da concessionária) | ⛔ Não integrado | Propostas/Vendas do Funil de Vendas em modo demo até isso ser feito |
| Anthropic (Claude API) | ⛔ Não configurado | Planejado pro Assistente do Painel responder perguntas abertas no futuro |

---

## 12. Variáveis de Ambiente (`backend/.env`)

| Variável | Uso |
|---|---|
| `JWT_SECRET` | Chave de assinatura dos tokens de login |
| `DATABASE_URL` | String de conexão do banco (padrão: SQLite local) |
| `META_ACCESS_TOKEN` | Token de acesso à Graph API do Meta (vazio = modo demo) |
| `GOOGLE_ADS_CLIENT_ID` / `GOOGLE_ADS_CLIENT_SECRET` | Credenciais OAuth do app no Google Cloud |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Token de desenvolvedor da Google Ads API |
| `GOOGLE_ADS_REFRESH_TOKEN` | Token de atualização OAuth (gerado uma vez, de forma interativa) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | ID da conta gerenciadora (MCC) — 3807692474 |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Usados só pelo `seed.py`, pra criar o primeiro usuário admin |

**Nunca** commitar o `.env` — ele fica só no servidor e nenhuma dessas chaves é reexibida na interface depois de configurada.

---

## 13. Pendências e Próximos Passos Conhecidos

- **Integração real com o NBS**: falta documentação/credenciais da API deles. Quando disponível, a implementação é: puxar contagem de propostas e vendas por marca, filtradas por origem = Meta Ads, e substituir a geração demo em `sync.py` (`_sync_funil`).
- **IA de verdade no Assistente do Painel**: falta a chave da API da Anthropic (`console.anthropic.com` → Billing → API Keys). Quando configurada, o chat passa a responder perguntas abertas usando os dados reais do dashboard como contexto, além do banco de perguntas frequentes que já existe.
- **Limpeza técnica pendente**: a geocodificação de cidades (`geocoding.py`, tabela `GeocodeCache`, colunas de latitude/longitude em `GoogleLocationCache`) ainda roda a cada sincronização, mas não alimenta mais nenhuma visualização desde que o mapa geográfico visual foi removido a pedido do usuário — pode ser removida com segurança quando for conveniente.
- **Servidor em ambiente Windows**: `uvicorn --reload` não é estável nesta máquina; o padrão adotado é rodar sem `--reload` e reiniciar manualmente o processo a cada alteração no backend (o frontend, por ser um único HTML estático, não precisa de reinício — só recarregar a página).
