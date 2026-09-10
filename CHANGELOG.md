# Changelog — FEFO

Registro de versões do projeto. Uma versão nova é criada a cada conjunto de alterações relevantes, ou ao final de cada dia de trabalho.

---

## 0.56 — 2026-09-10

### Correção crítica: lead de conversa (WhatsApp/Messenger) zerando em campanha de Geração de Cadastro
- O sistema só contava "conversa iniciada" como lead quando o OBJETIVO da campanha era Engajamento (`OUTCOME_ENGAGEMENT`) — regra criada quando só campanha de engajamento gerava conversa de verdade; em campanha de Geração de Cadastro (`OUTCOME_LEADS`), conversa era tratada como efeito colateral (comentário no post) e descartada.
- Isso quebrou quando o grupo passou a rodar **Click-to-WhatsApp dentro de campanhas com objetivo Geração de Cadastro** — configuração hoje comum no Meta. A campanha continua rotulada "Leads", mas manda a pessoa pra uma conversa de verdade.
- Efeito real, medido: em 09/09/2026, **9 das 13 marcas do Felipe apareceram com ZERO lead no dia**, incluindo contas que convertem bem historicamente — Nissan Campo Grande gastou R$ 163 sem "gerar nenhum lead". A conversa estava acontecendo (6 a 27 conversas/dia por campanha), só não estava sendo contada.
- **Correção:** o sinal usado agora é `destination_type` do CONJUNTO de anúncios (WHATSAPP/MESSENGER), não o objetivo da campanha — é a propriedade que o Meta usa pra saber pra onde o anúncio manda a pessoa de verdade, e não muda com o rótulo da campanha.
- Impacto depois de corrigir: 09/09 saltou de 11 leads (CPL R$ 89,82) para **98 leads (CPL R$ 10,09)**. A tendência da semana, que vinha caindo suavemente até quase zero conforme se aproximava do dia atual, passou a ficar estável — confirmando que era bug de contagem, não atraso normal de atribuição do Meta.
- Custo de sincronização: praticamente nenhum (+1 chamada leve por conta, ~1 a 3s mesmo numa conta com quase 400 conjuntos — sincronização completa foi de 113s pra 110s).
- Afeta três lugares que dependiam dessa contagem: os KPIs do dashboard, o ranking de melhor anúncio por marca, e qualquer projeção de saldo que use CPL — todos corrigidos com esta sincronização.

### Conteúdo orgânico mostrava post de outro mês
- O card "Alcance Orgânico" escolhia o melhor post entre os últimos 50 publicados, SEM olhar a data — post antigo com muita curtida vencia pra sempre. Uma conta chegou a mostrar, em setembro, o post de 28 de maio.
- O ganho de seguidores também ignorava o filtro de período: guardava só o total dos últimos 30 dias corridos no momento da sincronização, que não mudava ao trocar o filtro na tela.
- Correção: agora guardamos cada post com sua data e o ganho de seguidores dia a dia (`InstagramPostMetric`, `InstagramFollowerDaily`) — o "melhor post" e o "ganho no período" são calculados na tela, pro período selecionado, igual já era feito pro top 3 de criativos do Meta.

### Post do Alcance Orgânico agora abre no Instagram
- Clicar em qualquer parte do post em destaque (miniatura, legenda ou curtidas) abre o post de verdade no Instagram, em nova aba — sem precisar procurar ele à mão.

---

## 0.55 — 2026-09-01

### Monitor de tokens do Meta
- A sincronização agora confere o prazo de todos os tokens do Meta e o painel avisa **30 dias antes** de algum vencer. Antes, o jeito de descobrir que um token morreu era o painel zerar.
- Custa 2,8s e uma requisição: os 25 tokens vão num lote só do `debug_token`.
- O aviso só aparece quando há o que avisar. Com tudo em usuário do sistema, some da tela.

### O que o monitor revelou no primeiro uso
- **Os 23 tokens do Instagram vencem todos no mesmo dia: 22/11/2026.** Não é coincidência — 18 deles foram derivados do token pessoal do Felipe, e token de página herda o prazo de quem o gerou.
- O comentário no `config.py` dizia que o token pessoal expira em 60 dias e que os de página nunca expiram. Os dois estavam errados: o que vence não é o `expires_at` (vem 0 em todos), é o `data_access_expires_at` — o prazo de 90 dias que o Meta impõe a token de usuário. Token de usuário do sistema não tem esse prazo.
- Os 5 tokens que já vêm do usuário do sistema (@ravieramotors, @ravieramotorsdourados, @raviera_bmwmotorrad, @ravieramini, @consorciogruporaviera) não vencem nunca. São a prova de que a correção funciona.

### Falso alarme corrigido antes de entrar
- Na primeira versão o monitor acusou o @acrediesel de token inválido. Era erro meu: o `debug_token` só inspeciona token do mesmo app, e falha de inspeção não é token morto — a conta funcionava normalmente.
- Agora, quando a inspeção falha, o sistema pergunta direto ao token se ele responde. Só acusa problema se ele de fato não responder.

### Script pra regerar os tokens do Instagram
- `backend/regerar_tokens_instagram.py` rederiva os tokens de página a partir de um token de usuário, em vez de colar 23 à mão como foi feito da primeira vez.
- Roda em simulação por padrão; só grava com `--gravar`. Avisa se o token de origem não é de usuário do sistema ou se está sem algum escopo — porque os tokens gerados herdam as duas coisas.
- Serve também de medidor: hoje ele reporta "5 a atualizar · 18 fora do alcance". Quando as páginas forem atribuídas ao usuário do sistema, esse 18 vira 0.

---

## 0.54 — 2026-09-01

### Nova área: Redes Sociais
- Aba nova no menu. Mostra os comentários que chegaram nos posts do Instagram de cada marca e, principalmente, **o que ainda não foi respondido** — com o analista responsável ao lado.
- Quatro indicadores no topo: comentários recebidos, sem resposta, taxa de resposta e tempo médio de resposta.
- Tabela por marca ordenada por pendência (a marca mais abandonada aparece primeiro). Clicar numa marca filtra a lista.
- Lista de comentários com o texto, há quanto tempo está esperando e link direto pro post. Vem filtrada em "só sem resposta", que é o uso do dia a dia.
- A tela fala em **comentário**, não em "mensagem": mensagem é o que vem de Direct e Messenger, e isso ainda não entra aqui.
- Começa nas 13 marcas do Felipe (`ANALISTAS_REDES_SOCIAIS` em config.py). Coletar as 23 custaria tempo de sincronização sem retorno hoje.

### O primeiro número que a aba deu
- 219 comentários em 30 dias, **209 sem resposta**: taxa de resposta de 4,6%. Nissan Campo Grande sozinha tem 86 pendentes.
- Entre os pendentes havia reclamação formal de cliente sobre devolução de dinheiro, parada num post público. Era exatamente esse tipo de coisa que ninguém via.

### Comentário de anúncio (dark post) ficou de fora
- Chegou a funcionar e foi removido a pedido: só trazia 16 comentários em 30 dias e custava uma chamada a mais por conta de anúncio.
- Sem ele, a coleta caiu de ~8s para **4s**, e a etapa passou a ser 1 requisição por marca em vez de 2.

### Mensagem direta ainda não entra
- Instagram Direct e Messenger **não** estão aqui, e a tela diz isso em vez de fingir que é a caixa de entrada inteira.
- Não é problema de token: o token do sistema já tem o escopo `instagram_manage_messages`, e a Graph API mesmo assim responde `(#3) Application does not have the capability`. É capacidade do app, liberada só por App Review (Advanced Access) do Meta.
- No Messenger falta ainda o escopo `pages_messaging`, e o token do sistema só enxerga 5 páginas do Facebook.
- O campo `canal` do modelo já existe pra receber "instagram_direct" e "messenger" sem migração, quando/se a aprovação sair.

### Autor do comentário
- Aparece "perfil não identificado" na maioria: `username` só volta quando o token tem `instagram_manage_comments`, e os tokens de página guardados hoje foram gerados antes desta aba existir.
- Medido: 0 de 126 comentários com autor pelo token de página; 18 de 18 pelo token do sistema, que tem o escopo. Regerar os tokens de página com `instagram_manage_comments` resolve — **não** depende de App Review, o app já tem essa permissão concedida.
- Sem o autor, "respondido" cai pra "existe resposta no fio" em vez de "existe resposta nossa". Em post de loja é quase sempre a loja, mas é aproximação.

### Sem custo relevante de sincronização
- A coleta soma **4s** a uma sincronização de ~95s, e não faz uma requisição por post: usa requisição **em lote** da Graph API. Medido: 28 posts levavam ~30s um a um e levam 1,8s num lote só.
- A listagem de posts não custa requisição nenhuma — reaproveita o `/media` que a sincronização do Instagram já fazia (contagem, permalink e legenda já vinham nela). Sobra 1 requisição por marca.
- Só relê os comentários de um post quando a contagem mudou, ou quando ele ainda tem pendência de até 21 dias — responder um comentário nem sempre mexe na contagem do post, e sem essa segunda regra o item ficaria pendente pra sempre na tela.
- Comentário coletado é guardado por 90 dias.

---

## 0.53 — 2026-08-27

### Números dos KPIs contam até o valor
- Investimento, Leads Gerados, CPL Médio e Contas em Alerta de Saldo agora sobem contando, ao abrir a página e ao trocar o período.
- Na primeira aparição sobe do zero; ao trocar o filtro parte do valor anterior, o que deixa visível se subiu ou desceu em vez de sempre encher de novo.
- A contagem desacelera no fim (curva cúbica) e termina exatamente no valor real — o número "assenta" no lugar em vez de travar como cronômetro.
- Respeita a configuração de "reduzir animações" do sistema operacional: nesse caso o valor entra direto.
- Em aba de segundo plano o valor também entra direto. O navegador não executa animação em aba oculta, e sem essa proteção os cards ficariam mostrando "R$ 0,00" no lugar do investimento real até alguém olhar para a aba.

---

## 0.52 — 2026-08-27

### Nova área: Insights da Concorrência
- Aba nova no menu, ao lado de Meta Ads, Google Ads e Informações Gerais.
- Acompanha os criativos que os concorrentes estão rodando na Biblioteca de Anúncios do Meta: por modelo, por **tipo de campanha** (PcD, taxista, produtor rural/CNPJ, consórcio, seminovos, pós-venda, bônus na troca, taxa 0%), com preço "de/por", bônus na troca e destino do clique.
- Guarda **desde quando cada criativo está no ar** e marca os que saíram. Criativo que sobrevive semanas é criativo que converte pro concorrente — é a informação mais útil daqui, e a única que só existe acumulando coletas.
- Os públicos com isenção (PcD, taxista, produtor rural) ganham destaque de cor porque são venda de margem garantida: dá pra ver rápido se o concorrente explora um nicho que a gente não.

### Como os dados entram
- Por **colagem**: alguém abre a Biblioteca de Anúncios (pública, sem login), copia a página e cola na tela de importação. O sistema lê e organiza.
- Não é raspagem automática de propósito: robô coletando do Facebook viola os termos de uso deles, e o risco recairia sobre o grupo.
- A API oficial da Biblioteca **não resolve** este caso: fora da União Europeia ela só devolve anúncios políticos e de causas sociais — anúncio de concessionária não existe lá. Por isso a colagem.
- A importação tem prévia: mostra quais anunciantes vieram no texto e deixa escolher. Busca por palavra-chave na Biblioteca traz gente sem relação (buscando "kampai" apareceram restaurante e floricultura), e sem esse filtro isso entraria como concorrente.

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
