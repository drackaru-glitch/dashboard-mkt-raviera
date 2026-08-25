# Changelog — FEFO

Registro de versões do projeto. Uma versão nova é criada a cada conjunto de alterações relevantes, ou ao final de cada dia de trabalho.

---

## 0.51 — 2026-08-24

### Linhas neon nos cards
- Traço fino na borda de cima dos cards, no estilo minimalista/futurista: aparece nos KPIs, no Top 3 Melhores Anúncios, no Alcance Orgânico e nas Notícias do Setor.
- Cada linha usa a **cor de acento que o card já tinha** (a mesma do ícone) — nenhuma paleta nova foi criada, então o visual continua coeso.
- A linha é curta e centralizada, com o degradê apagando nas pontas: de ponta a ponta num card largo ela viraria um borrão em vez de um detalhe.
- O brilho existe só no tema escuro. No claro ele sumiria no fundo branco e deixaria a linha com aspecto sujo, então lá fica só o traço, um pouco mais opaco pra não desaparecer.
- O botão de período selecionado também ganhou brilho no tema escuro.
- Incluídos também os cards **Desempenho por Marca** (azul, a mesma cor das barras do gráfico) e **Saldo das Contas de Anúncios** (verde, a mesma do KPI "Saldo Total").
- No card de Saldo, a rolagem horizontal saiu do card e passou a valer só para a tabela: como está no topo da borda, a linha era recortada pelo `overflow` — e, de quebra, o título e a descrição não rolam mais junto com a tabela.

---

## 0.50 — 2026-08-24

### Velocímetro dos KPIs virou gráfico de evolução
- Os cards de Investimento, Leads Gerados e CPL Médio trocaram o velocímetro por um **sparkline** com a evolução dia a dia da métrica no período selecionado.
- Motivo: o velocímetro desenhava a variação percentual num arco de −100% a +100%, uma escala sem sentido real (variação não tem teto) — crescer 150% e crescer 2000% deixavam o ponteiro no mesmo lugar, e +5% quase não mexia nele. Além disso ele repetia em desenho o número que já aparecia escrito logo abaixo, e no card de CPL o ponteiro era invertido de propósito, então a mesma posição significava coisas opostas em cards diferentes.
- O sparkline usa o mesmo espaço para mostrar o que o número sozinho não conta: se o resultado veio de uma evolução constante ou de um pico isolado.
- Sem custo de backend — reaproveita a série diária que o dashboard já recebia, e acompanha o filtro de período automaticamente.
- Dias sem lead não entram na linha do CPL (seriam divisão por zero); períodos com menos de dois dias mostram "sem dados no período"; e quando todos os dias têm o mesmo valor a linha fica no meio do quadro, lendo como "estável".

---

## 0.49 — 2026-08-24

### Instagram: 24 contas no lugar de 6, todas no alcance orgânico
- Cadastradas as 18 contas de Instagram que faltavam, cobrindo todos os portfólios do grupo (BYD, Hyundai, Jeep/Ram, Nissan, Toyota, Multimarcas, JLR, Denza) — antes só existiam as 5 do BMW/Consórcio mais a Acrediesel.
- Cada conta usa um **token de página**, que não expira — diferente do token pessoal das contas Jeep, isso não vira manutenção a cada 60 dias.
- A descoberta foi feita percorrendo os 13 portfólios empresariais; todas as 24 foram testadas lendo dados reais antes de serem gravadas.
- As 17 marcas que ainda não tinham conta vinculada foram ligadas às suas contas — o card de Alcance Orgânico passou de 7 para 23 cards, cobrindo todas as 24 marcas. O vínculo de cada uma foi conferido pelo nome do próprio perfil no Instagram (ex.: @ravieraram é "Raviera Ram Acre", não a de Rondônia).
- O card de Alcance Orgânico ficou recolhível, igual ao "Top 3 Melhores Anúncios": mostra 6 contas (duas linhas) e um botão "Ver todas (N)" / "Ver menos" — com mais de 20 contas, a lista inteira aberta empurrava o resto do dashboard para baixo demais.

### Sincronização segue rápida mesmo com 4x mais contas de Instagram
- Mesma separação de busca (paralela) e gravação (sequencial) que o Meta já usava, agora aplicada ao Instagram: a fase do Instagram ficou em ~30s em vez dos ~90s que seriam sequenciais.
- Tempo total da sincronização completa: ~97s, com 20 contas de anúncio e 24 de Instagram.
- Nova tentativa automática (3x, com espera crescente) para erros de sobrecarga momentânea do Meta — o "Please reduce the amount of data you're asking for", que passou a aparecer de vez em quando nas contas maiores depois que a busca voltou a ser paralela, e some sozinho ao repetir.

---

## 0.48 — 2026-08-24

### Desempenho: "Atualizar dados" ~4x mais rápido
- Sincronização completa caiu de mais de 5 minutos para cerca de 1 minuto e meio.
- Miniaturas de anúncios não são mais rebaixadas a cada sincronização: eram ~700 imagens (100 MB) baixadas de novo toda vez, mesmo já estando salvas no servidor — sozinho, isso respondia por cerca de 60% da demora. Como a chave usada (ID do anúncio, do post ou do link da notícia) identifica conteúdo que não muda, o arquivo já salvo é reaproveitado.
- Busca na API do Meta voltou a rodar em paralelo (5 contas por vez) mesmo no SQLite: agora a busca na rede (que não toca no banco) é separada da gravação, então só a gravação é feita uma conta de cada vez. Antes as duas andavam juntas, e no SQLite cada conta esperava a anterior inteira, tempo de rede incluído.

### Contas Jeep (Acrediesel) voltaram a sincronizar
- As 4 contas Jeep (Vilhena, Porto Velho, Ji-Paraná e Rio Branco) pertencem ao portfólio externo "DDM Stellantis", que concede acesso individual ao Felipe mas nunca ao usuário do sistema FEFO Integração — por isso ficaram de fora quando migramos para o token de sistema.
- Passam a usar um token pessoal separado (`META_ACCESS_TOKEN_PESSOAL`), aplicado só a essas 4 contas; as outras 16 continuam no token de sistema, que não expira.
- Atenção: esse token pessoal expira a cada ~60 dias e precisa ser gerado de novo pelo Explorador da API Graph. As demais contas não são afetadas quando isso acontece.

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
