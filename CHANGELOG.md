# Changelog — FEFO

Registro de versões do projeto. Uma versão nova é criada a cada conjunto de alterações relevantes, ou ao final de cada dia de trabalho.

---

## 0.47 — 2026-08-04

### Novo nome: FEFO
- Renomeado de "Raviera Cockpit" para **FEFO — Fast Executive Follow-up & Overview**, em tudo que é visível pro usuário: tela de login, cabeçalho, rodapés, título da aba, documentação e guia de instalação.
- A expansão do nome ("Fast Executive Follow-up & Overview") passou a aparecer como subtítulo abaixo do nome, tanto na tela de login quanto no cabeçalho do dashboard.
- Nomes técnicos internos (pasta do projeto, repositório no GitHub, serviço no servidor) continuam usando "raviera"/"dashboard-raviera" — não foram alterados, pra não exigir reconfiguração do que o TI já vai instalar.

---

## 0.46 — 2026-08-04

### Suporte a PostgreSQL
- Aplicação agora suporta PostgreSQL além de SQLite — troca só a variável `DATABASE_URL` no `.env`, sem mudança de código (já usava SQLAlchemy, que abstrai isso).
- Driver `psycopg2-binary` adicionado às dependências.
- `pool_pre_ping` ativado na conexão do banco (evita erros de "conexão perdida" comuns em bancos gerenciados).
- Novo script `backend/migrate_to_postgres.py`: migra a estrutura e todos os dados do `raviera.db` atual pra um banco Postgres novo (preserva marcas, usuários e histórico já sincronizado).
- Guia de instalação atualizado com o passo a passo e a lista do que pedir pro time de desenvolvimento (host, banco, usuário/senha, SSL, acesso de rede).
- Pendente: rodar a migração de verdade assim que o time de dev passar os dados de acesso ao Postgres.

---

## 0.45 — 2026-08-03

### Deploy
- Toast de sucesso ao atualizar dados no Meta Ads, mostrando quantas contas foram sincronizadas.
- Script de atualização automática pro servidor (`deploy/auto-update.sh`): verifica a cada 5 minutos se tem versão nova no GitHub e, se tiver, atualiza e reinicia sozinho — o TI não precisa aplicar cada atualização manualmente depois da instalação inicial.
- Guia de instalação atualizado com o passo a passo desse recurso.

---

## 0.44 — 2026-08-03

Primeira versão numerada do projeto. Reúne a integração real com as APIs (Meta Ads, Google Ads, Instagram), o sistema de permissões por usuário e diversas correções de dados e performance.

### Integração de dados
- Sincronização real com Meta Ads (saldo, gasto, leads, criativos), Google Ads (orçamento, palavras-chave, localidades) e Instagram (seguidores, post em destaque).
- Corrigido saldo das contas do Meta aparecendo 100x maior que o real (a API devolve valores em centavos).
- Corrigida contagem duplicada de leads (o Meta reporta o mesmo evento em duas classificações diferentes).
- WhatsApp só conta como lead quando a campanha é de fato de Mensagens (antes contava como efeito colateral de campanhas de Cadastro).
- Corrigido filtro de campanha da conta "Jeep Ji-Paraná", que estava somando por engano uma campanha de "Porto Velho" hospedada na mesma conta.
- "Top 3 Melhores Anúncios por Marca" agora recalcula dinamicamente pelo período selecionado no dashboard (Mês Atual, Mês Anterior etc.), em vez de um valor fixo pré-calculado.
- Sincronização paralela (várias contas ao mesmo tempo) — tempo total caiu de vários minutos para cerca de 3 minutos. Trava de segurança contra sincronização "presa" por mais de 15 minutos.
- Sincronização automática diária às 06h, além do botão manual "Atualizar dados".
- Cache local de imagens (criativos, posts do Instagram, capas de notícias) — as miniaturas não quebram mais quando o link assinado do Meta expira.

### Funcionalidades novas
- Página "Informações Gerais" (funil de vendas, estatísticas de SAC, notícias do setor).
- Card "Notícias do Setor" (Quatro Rodas, Motor1, AutoPapo) — coleta automática 2x/dia e filtra por marca.
- Popup de detalhe do criativo ao clicar no ranking de anúncios.
- Velocímetro visual nos cards de Investimento, Leads Gerados e CPL Médio.

### Permissões e administração
- Sistema de permissões por usuário: Saldo, Investimento, Informações Gerais, Notícias do Setor — cada uma pode ser liberada ou escondida individualmente por usuário (admin sempre vê tudo).
- Painel de administração consolidado dentro do ícone de engrenagem.

### Identidade visual
- Renomeado de "Dashboard Raviera" para "Raviera Cockpit", com slogan "No controle de cada decisão."
- Logo oficial fixa (removida a opção de logo customizada).
- Clique na logo volta para a tela principal.
- Horário de "última atualização" corrigido (estava aparecendo nas horas UTC em vez do horário local) e passou a mostrar data + hora.

### Conhecido / pendente
- Google Ads: token de acesso expirado — as 14 contas não sincronizam até uma nova autorização ser gerada (não afeta Meta Ads, Instagram, funil ou notícias).
- SAC (Instagram/Facebook) ainda em modo demonstração, aguardando permissão do Meta para mensagens.
- Followize/Dealer Space: webhooks implementados, aguardando o domínio público para serem cadastrados no painel deles.
