from datetime import datetime, date

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    nome = Column(String, default="")
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="gerente")  # "admin" | "gerente"
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    # Permissões por seção do painel (só valem pra role="gerente" — admin sempre vê tudo).
    ver_saldo = Column(Boolean, nullable=False, default=True)
    ver_investimento = Column(Boolean, nullable=False, default=True)
    ver_funil = Column(Boolean, nullable=False, default=True)
    ver_noticias = Column(Boolean, nullable=False, default=True)

    # Ordem em que a pessoa arrastou os quadros do painel principal (JSON de uma lista de
    # ids, ex.: '["saldo","evolucao","topCriativos",...]'). None = nunca mexeu, usa a ordem
    # padrão do frontend. Fica ligado à conta (não ao navegador) pra não precisar reorganizar
    # de novo a cada login em outro computador.
    ordem_blocos = Column(String, nullable=True)

    # Até quando a pessoa já viu as notificações do sino — não é lida por notificação (o
    # sistema é pequeno demais pra precisar disso); só um cursor. Notificação com criado_em
    # depois disso conta como não lida.
    notificacoes_lidas_ate = Column(DateTime, nullable=True)

    brands = relationship("UserBrand", back_populates="user", cascade="all, delete-orphan")


class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id = Column(Integer, primary_key=True)
    meta_account_id = Column(String, unique=True, nullable=False)  # ex: act_123456789
    nome = Column(String, nullable=False)

    # Preenchidos à mão uma vez, copiados da URL da tela "Cobrança e pagamentos" do Gerenciador
    # de Negócios (business.facebook.com) — não existe campo nenhum na Graph API que devolva
    # esses 3 IDs (confirmado testando: nenhum aparece em /act_X com qualquer combinação de
    # campos), então não tem como buscar isso sozinho na sincronização. Só preenchidos quando
    # a marca tem nota fiscal de verdade disponível (contas pré-pagas do grupo têm; o endpoint
    # público business_invoices da Graph API não expõe esse dado, só a tela mesmo).
    billing_business_id = Column(String, nullable=True)
    billing_asset_id = Column(String, nullable=True)
    billing_payment_account_id = Column(String, nullable=True)

    brands = relationship("Brand", back_populates="ad_account")


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"

    id = Column(Integer, primary_key=True)
    instagram_business_id = Column(String, unique=True, nullable=False)  # ID da conta no Graph API
    username = Column(String, nullable=False)
    nome = Column(String, nullable=False)  # nome de exibição (ex: "BMW Motorrad")
    access_token = Column(String, nullable=True)  # token do Business Manager dono dessa conta (cada portfólio tem o seu)

    brands = relationship("Brand", back_populates="instagram_account")


class GoogleAdsAccount(Base):
    __tablename__ = "google_ads_accounts"

    id = Column(Integer, primary_key=True)
    customer_id = Column(String, unique=True, nullable=False)  # ex: 1234567890 (sem hífens)
    nome = Column(String, nullable=False)

    brands = relationship("Brand", back_populates="google_ads_account")


class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, nullable=False)
    ad_account_id = Column(Integer, ForeignKey("ad_accounts.id"), nullable=True)
    campaign_filter = Column(String, nullable=True)  # prefixo do nome da campanha, quando a conta é compartilhada
    diario_esperado_meta = Column(Float, default=0)  # PLANEJADO: quanto a pessoa decidiu gastar por dia
    analista = Column(String, default="")
    instagram_account_id = Column(Integer, ForeignKey("instagram_accounts.id"), nullable=True)
    google_ads_account_id = Column(Integer, ForeignKey("google_ads_accounts.id"), nullable=True)
    google_orcamento_mensal = Column(Float, default=0)  # verba mensal definida manualmente pra essa marca no Google Ads

    # REAL: soma do daily_budget das campanhas ativas dessa marca, lido do Meta a cada
    # sincronização — não depende de ninguém digitar nada, por isso pode divergir do
    # planejado acima (alguém reajustou orçamento na campanha e esqueceu de atualizar aqui).
    orcamento_diario_real = Column(Float, nullable=True)
    orcamento_atualizado_em = Column(DateTime, nullable=True)

    # Status do runway de saldo (ok/atencao/critico/semDados) na última sincronização — só
    # serve pra saber se PIOROU desde a última vez, e assim não notificar de novo a cada
    # sincronização enquanto a marca continuar crítica.
    ultimo_status_saldo = Column(String, nullable=True)

    # Mesma ideia acima, pro alerta de "orçamento real zerado" (todas as campanhas pausadas).
    ultimo_orcamento_real_zerado = Column(Boolean, nullable=True)

    ad_account = relationship("AdAccount", back_populates="brands")
    instagram_account = relationship("InstagramAccount", back_populates="brands")
    google_ads_account = relationship("GoogleAdsAccount", back_populates="brands")
    user_links = relationship("UserBrand", back_populates="brand", cascade="all, delete-orphan")


class UserBrand(Base):
    __tablename__ = "user_brands"
    __table_args__ = (UniqueConstraint("user_id", "brand_id", name="uq_user_brand"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)

    user = relationship("User", back_populates="brands")
    brand = relationship("Brand", back_populates="user_links")


class MetaRegionCache(Base):
    """Gasto/leads por estado (breakdown `region` do Meta) — o Meta não tem quebra por
    cidade nos insights (só region/país/dma, e dma é só EUA), diferente do Google Ads. Ver
    MetaCidadeCache para a lista de cidades que as campanhas têm CONFIGURADAS por estado —
    isso é intenção de segmentação, não resultado real, os dois não devem ser confundidos."""

    __tablename__ = "meta_region_cache"
    __table_args__ = (UniqueConstraint("brand_id", "regiao", name="uq_meta_region_brand"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    regiao = Column(String, nullable=False)
    gasto = Column(Float, default=0)
    leads = Column(Integer, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)


class MetaCidadeCache(Base):
    """Cidades que os conjuntos de anúncio ATIVOS de cada marca têm configuradas como
    segmentação, agrupadas por estado — é o que abre quando alguém clica num estado no mapa
    de calor. Vem do `targeting.geo_locations` dos conjuntos, não é dado de resultado."""

    __tablename__ = "meta_cidade_cache"
    __table_args__ = (UniqueConstraint("brand_id", "regiao", "cidade", name="uq_meta_cidade"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    regiao = Column(String, nullable=False)
    cidade = Column(String, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow)


class MetaAprendizadoAlertado(Base):
    """Lembra qual ciclo de aprendizado (`last_sig_edit_ts` do Meta) de cada conjunto já gerou
    o alerta de "aprendizado travado" — pra não repetir a notificação todo dia enquanto o MESMO
    ciclo continuar preso, mas avisar de novo se a pessoa editar o conjunto (o que reinicia o
    aprendizado) e ele travar de novo depois. Não guarda "desde quando" por conta própria: o
    Meta já manda isso pronto em `last_sig_edit_ts`, ver app.notificacoes.gerar_alertas_aprendizado_travado."""

    __tablename__ = "meta_aprendizado_alertado"

    id = Column(Integer, primary_key=True)
    adset_id = Column(String, unique=True, nullable=False)
    last_sig_edit_ts = Column(Integer, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow)


class MetaPublicoSobrepostoAlertado(Base):
    """Lembra qual GRUPO de conjuntos (por adset_id, ordenados) já gerou o alerta de "público
    sobreposto" pra uma assinatura de público de uma marca — pra não repetir a notificação
    todo dia enquanto o MESMO grupo continuar sobreposto, mas avisar de novo se o grupo mudar
    (um conjunto novo entrou nele, ou um saiu). Ver app.notificacoes.gerar_alertas_publico_sobreposto."""

    __tablename__ = "meta_publico_sobreposto_alertado"

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    assinatura_hash = Column(String, unique=True, nullable=False)
    adset_ids = Column(String, nullable=False)  # ids ordenados, separados por vírgula
    atualizado_em = Column(DateTime, default=datetime.utcnow)


class DailyMetric(Base):
    __tablename__ = "daily_metrics"
    __table_args__ = (UniqueConstraint("brand_id", "data", name="uq_brand_date"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    data = Column(Date, nullable=False, default=date.today)
    gasto = Column(Float, default=0)
    leads_formulario = Column(Integer, default=0)
    leads_whatsapp = Column(Integer, default=0)
    fonte = Column(String, default="meta")  # "meta" | "manual"

    brand = relationship("Brand")


class SaldoCache(Base):
    __tablename__ = "saldo_cache"

    id = Column(Integer, primary_key=True)
    ad_account_id = Column(Integer, ForeignKey("ad_accounts.id"), unique=True, nullable=False)
    saldo = Column(Float, default=0)
    moeda = Column(String, default="BRL")
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    ad_account = relationship("AdAccount")


class InstagramStatsCache(Base):
    """Retrato do momento da conta. O ganho de seguidores NÃO mora mais aqui: virou
    InstagramFollowerDaily, porque um total só não conseguia responder a troca de período."""

    __tablename__ = "instagram_stats_cache"

    id = Column(Integer, primary_key=True)
    instagram_account_id = Column(Integer, ForeignKey("instagram_accounts.id"), unique=True, nullable=False)
    seguidores_atual = Column(Integer, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    instagram_account = relationship("InstagramAccount")


class InstagramPostMetric(Base):
    """Cada post do Instagram, com data e engajamento — não só o vencedor do momento.

    Substitui o InstagramTopPostCache, que guardava um post só, escolhido entre os últimos 50
    publicados e SEM olhar a data. O efeito era que o card de conteúdo orgânico mostrava, em
    setembro, post de maio: bastava ele ter mais curtidas e nada tê-lo superado desde então.
    Como cada conta publica em ritmo diferente, "os últimos 50" cobriam períodos diferentes
    em cada marca, e nenhuma delas correspondia ao período escolhido no painel.

    Guardando post a post, o melhor post passa a ser calculado para o período que estiver na
    tela — mesma solução já usada no AdDailyMetric para o top 3 de criativos do Meta.
    """

    __tablename__ = "instagram_post_metrics"
    __table_args__ = (UniqueConstraint("instagram_account_id", "post_id", name="uq_ig_conta_post"),)

    id = Column(Integer, primary_key=True)
    instagram_account_id = Column(Integer, ForeignKey("instagram_accounts.id"), nullable=False)
    post_id = Column(String, nullable=False)
    legenda = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    permalink = Column(String, nullable=True)
    curtidas = Column(Integer, default=0)
    comentarios = Column(Integer, default=0)
    publicado_em = Column(DateTime, nullable=True)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    instagram_account = relationship("InstagramAccount")


class InstagramFollowerDaily(Base):
    """Ganho líquido de seguidores por dia.

    Antes guardávamos só o total dos últimos 30 dias corridos, somado no momento da
    sincronização — um número que não mudava quando se trocava o período no painel, e que
    hoje (4 de setembro) representava 5 de agosto a 4 de setembro, atravessando dois meses.
    A API do Instagram já entrega dia a dia; éramos nós que somávamos antes de salvar.
    """

    __tablename__ = "instagram_follower_daily"
    __table_args__ = (UniqueConstraint("instagram_account_id", "data", name="uq_ig_conta_dia"),)

    id = Column(Integer, primary_key=True)
    instagram_account_id = Column(Integer, ForeignKey("instagram_accounts.id"), nullable=False)
    data = Column(Date, nullable=False)
    ganho = Column(Integer, default=0)

    instagram_account = relationship("InstagramAccount")


class GoogleDailyMetric(Base):
    __tablename__ = "google_daily_metrics"
    __table_args__ = (UniqueConstraint("brand_id", "data", name="uq_google_brand_date"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    data = Column(Date, nullable=False, default=date.today)
    gasto = Column(Float, default=0)
    cliques = Column(Integer, default=0)
    conversoes = Column(Float, default=0)  # Google conta conversões fracionadas (ex: 2.3)

    brand = relationship("Brand")


class FunilDailyMetric(Base):
    """Etapas do funil de vendas (Lead vem do Meta Ads, Proposta/Venda viriam do Followize/Dealer
    Space via webhook) por marca/dia. Enquanto o webhook não está recebendo eventos reais,
    propostas/vendas são geradas em modo demo (ver NBS_DEMO_MODE) — exceto linhas com
    fonte="manual", que foram corrigidas à mão e a sincronização não deve sobrescrever."""

    __tablename__ = "funil_daily_metrics"
    __table_args__ = (UniqueConstraint("brand_id", "data", name="uq_funil_brand_date"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    data = Column(Date, nullable=False, default=date.today)
    propostas = Column(Integer, default=0)
    vendas = Column(Integer, default=0)
    fonte = Column(String, default="demo")  # "demo" | "manual" | "followize"

    brand = relationship("Brand")


class FollowizeLeadEvent(Base):
    """Captura bruta dos webhooks do Followize/Dealer Space (lead criado, proposta submetida,
    finalização/venda). brand_id fica nulo até mapearmos o time/marca do Followize pra uma marca
    nossa (ver FollowizeTeamMap) — o objetivo inicial é só validar o formato real dos payloads."""

    __tablename__ = "followize_lead_events"

    id = Column(Integer, primary_key=True)
    evento = Column(String, nullable=False)  # "lead_criado" | "proposta_submetida" | "finalizacao"
    followize_lead_id = Column(String, nullable=True)
    team_nome = Column(String, nullable=True)
    interesse_marca = Column(String, nullable=True)
    interesse_local = Column(String, nullable=True)
    tracking_source = Column(String, nullable=True)
    tracking_medium = Column(String, nullable=True)
    status = Column(String, nullable=True)
    venda_realizada = Column(Boolean, nullable=True)
    valor_venda = Column(Float, nullable=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    payload_bruto = Column(String, nullable=True)
    recebido_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class FollowizeTeamMap(Base):
    """Mapeia o nome do 'time' do Followize/Dealer Space (ex: 'Raviera Nissan') pra uma marca
    nossa — preenchido manualmente em Configuração assim que virmos os nomes reais chegando."""

    __tablename__ = "followize_team_map"

    id = Column(Integer, primary_key=True)
    team_nome = Column(String, unique=True, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)

    brand = relationship("Brand")


class LetalkLeadEvent(Base):
    """Captura bruta dos webhooks da Letalk — disparados quando o contato do WhatsApp recebe
    uma tag ("Lead", "Não Responderam" ou "Test Drive"). A Letalk manda o estado ATUAL de
    TODAS as tags do contato a cada disparo (não um "diff" de qual tag mudou agora), por isso
    `tags` aqui é uma foto do momento, não um evento isolado — a leitura de qual tag é nova
    fica pra quando formos agregar isso de verdade, comparando com o evento anterior do mesmo
    telefone. brand_id fica nulo até o campo personalizado "marca" (ver LetalkMarcaMap) estar
    configurado no fluxo da Letalk que gerou esse evento."""

    __tablename__ = "letalk_lead_events"

    id = Column(Integer, primary_key=True)
    telefone = Column(String, nullable=True)
    nome = Column(String, nullable=True)
    email = Column(String, nullable=True)
    marca_nome = Column(String, nullable=True)
    tags = Column(String, nullable=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    payload_bruto = Column(String, nullable=True)
    recebido_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class LetalkMarcaMap(Base):
    """Mapeia o valor do campo personalizado "marca" (configurado à mão no fluxo de
    automação da Letalk de cada loja) pra uma marca nossa — preenchido manualmente em
    Configuração assim que cada fluxo tiver esse campo."""

    __tablename__ = "letalk_marca_map"

    id = Column(Integer, primary_key=True)
    marca_nome = Column(String, unique=True, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)

    brand = relationship("Brand")


class GoogleAccountCache(Base):
    __tablename__ = "google_account_cache"

    id = Column(Integer, primary_key=True)
    google_ads_account_id = Column(Integer, ForeignKey("google_ads_accounts.id"), unique=True, nullable=False)
    orcamento_diario = Column(Float, default=0)
    status = Column(String, default="")  # ENABLED, PAUSED, etc.
    # Saldo vindo do próprio Google (limite de gasto aprovado − já gasto; ver
    # google_ads_client.get_account_balance). None = a conta não tem esse conceito ou a
    # consulta falhou — a tela mostra "—", nunca um número calculado a partir de outra coisa.
    saldo = Column(Float, nullable=True)
    # Já avisamos que o saldo zerou? Garante UM aviso por vez que a conta zera (não um a cada
    # sincronização); volta a False quando entra dinheiro, pra avisar de novo na próxima vez.
    saldo_zerado_alertado = Column(Boolean, nullable=True)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    google_ads_account = relationship("GoogleAdsAccount")


class GoogleTopAdCache(Base):
    __tablename__ = "google_top_ad_cache"
    __table_args__ = (UniqueConstraint("brand_id", "rank", name="uq_google_brand_rank"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    rank = Column(Integer, nullable=False, default=1)
    ad_id = Column(String, nullable=True)
    titulo = Column(String, nullable=True)  # primeiro headline do anúncio
    campanha = Column(String, nullable=True)
    gasto = Column(Float, default=0)
    cliques = Column(Integer, default=0)
    conversoes = Column(Float, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class GoogleKeywordCache(Base):
    __tablename__ = "google_keyword_cache"
    __table_args__ = (UniqueConstraint("brand_id", "rank", name="uq_google_kw_brand_rank"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    rank = Column(Integer, nullable=False, default=1)
    texto = Column(String, nullable=True)
    gasto = Column(Float, default=0)
    cliques = Column(Integer, default=0)
    conversoes = Column(Float, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class GoogleLocationCache(Base):
    __tablename__ = "google_location_cache"
    __table_args__ = (UniqueConstraint("brand_id", "cidade", name="uq_google_loc_brand_cidade"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    cidade = Column(String, nullable=False)
    estado = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    gasto = Column(Float, default=0)
    cliques = Column(Integer, default=0)
    conversoes = Column(Float, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class GeocodeCache(Base):
    """Cache compartilhado de coordenadas por cidade — evita geocodificar a mesma
    cidade de novo pra cada marca/sincronização."""

    __tablename__ = "geocode_cache"
    __table_args__ = (UniqueConstraint("cidade", "estado", name="uq_geocode_cidade_estado"),)

    id = Column(Integer, primary_key=True)
    cidade = Column(String, nullable=False)
    estado = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    encontrado = Column(Boolean, default=True)  # False = já tentamos e não achamos (não tenta de novo)


class AdDailyMetric(Base):
    """Desempenho dia a dia de cada anúncio (não só um total fixo do mês) — permite recalcular
    o top 3 de criativos pra QUALQUER período selecionado no dashboard (mês atual, mês
    anterior, etc.), do mesmo jeito que já fazemos com o tráfego diário por marca."""

    __tablename__ = "ad_daily_metrics"
    __table_args__ = (UniqueConstraint("brand_id", "ad_id", "data", name="uq_brand_ad_data"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    ad_id = Column(String, nullable=False)
    nome_anuncio = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    data = Column(Date, nullable=False)
    gasto = Column(Float, default=0)
    leads = Column(Integer, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class BrandNewsItem(Base):
    """Manchetes recentes de portais automotivos (Quatro Rodas, Motor1 Brasil, AutoPapo),
    filtradas por marca (ver app.news_client) — v1 sem IA, só lista o título e o link, sem
    resumo/análise. marca_chave é o nome do fabricante (ex: "Toyota"), usado pra casar com
    qualquer Brand cujo nome contenha essa palavra (ex: "Xapuri Motors Toyota")."""

    __tablename__ = "brand_news_items"
    __table_args__ = (UniqueConstraint("marca_chave", "link", name="uq_news_marca_link"),)

    id = Column(Integer, primary_key=True)
    marca_chave = Column(String, nullable=False)
    titulo = Column(String, nullable=False)
    link = Column(String, nullable=False)
    fonte = Column(String, nullable=True)
    publicado_em = Column(DateTime, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    coletado_em = Column(DateTime, default=datetime.utcnow)


class ComentarioSocial(Base):
    """Um comentário público que chegou numa rede social da marca.

    Hoje só existe um canal preenchido: comentário em post do Instagram. Mensagem direta —
    Instagram Direct e Messenger — depende de App Review do Meta, que ainda não foi aprovado
    (ver app.social_client); o campo `canal` existe pra receber "instagram_direct"/"messenger"
    depois sem migração nenhuma.

    `respondido` vem da própria API (uma resposta da nossa conta ao comentário), não de um
    controle manual — é o que permite mostrar pendência de atendimento sem ninguém marcar nada.
    """

    __tablename__ = "comentarios_sociais"

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    canal = Column(String, nullable=False, default="instagram_comentario")
    comentario_id = Column(String, unique=True, nullable=False, index=True)
    media_id = Column(String, nullable=True)
    autor = Column(String, nullable=True)
    texto = Column(String, nullable=True)
    permalink = Column(String, nullable=True)  # link do post onde o comentário está
    legenda_post = Column(String, nullable=True)  # ajuda a saber de qual post/oferta se trata
    criado_em = Column(DateTime, nullable=True)  # quando a pessoa comentou
    respondido = Column(Boolean, nullable=False, default=False)
    respondido_em = Column(DateTime, nullable=True)
    coletado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class TokenSaude(Base):
    """Prazo de validade de cada token do Meta que o sistema usa.

    Existe porque um token do Meta morre em silêncio: o painel simplesmente zera e ninguém
    sabe por quê. O que mata não costuma ser `expires_at` (quase sempre 0), e sim
    `data_access_expires_at` — o prazo de 90 dias que o Meta impõe a token de USUÁRIO. Token
    de usuário do SISTEMA não tem esse prazo.

    Detalhe que já custou caro: token de página herda o prazo do token de usuário que o
    gerou. Os 23 tokens de Instagram saíram do token pessoal do Felipe, então venciam todos
    no mesmo dia sem que nada no código dissesse isso.
    """

    __tablename__ = "token_saude"

    id = Column(Integer, primary_key=True)
    chave = Column(String, unique=True, nullable=False)  # "sistema" | "pessoal" | "instagram:<id>"
    rotulo = Column(String, nullable=False)  # nome legível pra tela
    tipo = Column(String, nullable=True)  # SYSTEM_USER | USER | PAGE
    valido = Column(Boolean, nullable=False, default=True)
    expira_em = Column(DateTime, nullable=True)
    acesso_expira_em = Column(DateTime, nullable=True)
    erro = Column(String, nullable=True)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    # Já avisou no sino que esse token está precisando de atenção? Evita notificar de novo a
    # cada sincronização enquanto ninguém troca o token — reseta sozinho quando ele volta a
    # ficar saudável (ver app.notificacoes.gerar_alertas_token).
    alertado = Column(Boolean, nullable=False, default=False)


class MediaComentarioCache(Base):
    """Última contagem de comentários vista em cada mídia.

    É o que mantém a sincronização barata: se o número de comentários de um post não mudou
    desde a última coleta, não há motivo pra buscar os comentários dele de novo. Sem isso,
    toda sincronização releria todos os posts de todas as marcas.
    """

    __tablename__ = "media_comentario_cache"

    id = Column(Integer, primary_key=True)
    media_id = Column(String, unique=True, nullable=False, index=True)
    comentarios_count = Column(Integer, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)


class ModeloVeiculo(Base):
    """Modelos de veículo que o interpretador de anúncios de concorrentes
    (app.concorrentes_parser) sabe reconhecer no texto.

    Fica no banco em vez de fixo no código de propósito: a lista sempre cresce por reação, não
    por antecipação. Começou só com Toyota (a Kampai, primeiro concorrente cadastrado), ganhou
    Jeep/Honda/Hyundai quando o pátio de seminovos deles apareceu multimarca, e só ganhou Ford
    inteira quando a Recol (concessionária Ford) foi cadastrada e nenhum dos 7 anúncios dela
    reconheceu modelo. Com a lista editável aqui, um concorrente de marca nova não exige mais
    alterar código — só adicionar a linha.

    `modelo` é o nome puro (ex.: "Corolla Cross", "T-Cross") — o padrão de busca é construído
    sozinho a partir dele (ver concorrentes_parser.construir_padrao), então não tem regex pra
    escrever à mão. Nomes compostos não precisam vir em ordem especial na tabela: o parser
    sempre tenta o nome mais comprido primeiro, senão "Corolla Cross" seria lido como "Corolla".
    """

    __tablename__ = "modelos_veiculo"
    __table_args__ = (UniqueConstraint("marca", "modelo", name="uq_marca_modelo"),)

    id = Column(Integer, primary_key=True)
    marca = Column(String, nullable=False)
    modelo = Column(String, nullable=False)
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)


class Concorrente(Base):
    """Uma loja concorrente que a gente acompanha na Biblioteca de Anúncios do Meta.

    `pagina` é o nome EXATO como aparece no anúncio (ex.: "Kampai Concessionária Toyota") —
    é por ele que os anúncios importados são casados com o concorrente, já que a Biblioteca
    pública não expõe o ID numérico da página.
    """

    __tablename__ = "concorrentes"

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)  # nome de exibição (ex: "Kampai Toyota Campo Grande")
    pagina = Column(String, unique=True, nullable=False)  # nome do anunciante na Biblioteca
    cidade = Column(String, nullable=True)
    marca_chave = Column(String, nullable=True)  # fabricante que ele representa (ex: "Toyota")
    # Marca nossa que compete com ele nessa praça — permite comparar oferta lado a lado.
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")
    anuncios = relationship("ConcorrenteAnuncio", back_populates="concorrente", cascade="all, delete-orphan")


class ConcorrenteAnuncio(Base):
    """Um criativo do concorrente visto na Biblioteca de Anúncios.

    `visto_primeiro` / `visto_ultimo` são o coração da tabela: como a Biblioteca só mostra o
    que está ativo AGORA (não dá pra recuperar histórico), a longevidade de um criativo só
    aparece acumulando coletas. Anúncio que sobrevive semanas é anúncio que converte pra eles
    — é a informação mais útil daqui, e a única que exige paciência pra existir.

    `ativo=False` marca o criativo que sumiu da Biblioteca desde a última importação.
    """

    __tablename__ = "concorrente_anuncios"
    __table_args__ = (UniqueConstraint("concorrente_id", "ad_library_id", name="uq_conc_ad"),)

    id = Column(Integer, primary_key=True)
    concorrente_id = Column(Integer, ForeignKey("concorrentes.id"), nullable=False)
    ad_library_id = Column(String, nullable=False)  # "Identificação da biblioteca" do anúncio
    inicio = Column(Date, nullable=True)  # "Veiculação iniciada em"
    modelo = Column(String, nullable=True)  # Hilux, Corolla Cross, SW4...
    tipo_campanha = Column(String, nullable=True)  # PcD, Taxista, Produtor rural/CNPJ, Consórcio...
    preco_de = Column(Float, nullable=True)
    preco_por = Column(Float, nullable=True)
    bonus = Column(Float, nullable=True)  # bônus na troca do usado
    cta = Column(String, nullable=True)
    dominio = Column(String, nullable=True)
    texto = Column(String, nullable=True)
    varias_versoes = Column(Boolean, default=False)

    visto_primeiro = Column(Date, nullable=False)
    visto_ultimo = Column(Date, nullable=False)
    ativo = Column(Boolean, nullable=False, default=True)

    concorrente = relationship("Concorrente", back_populates="anuncios")


class ConcorrenteColeta(Base):
    """Registro de cada importação — serve pra mostrar "última coleta em X" na tela e pra
    saber quais anúncios marcar como inativos (os que não vieram na coleta mais recente)."""

    __tablename__ = "concorrente_coletas"

    id = Column(Integer, primary_key=True)
    concorrente_id = Column(Integer, ForeignKey("concorrentes.id"), nullable=True)
    data = Column(Date, nullable=False)
    anuncios_encontrados = Column(Integer, default=0)
    anuncios_novos = Column(Integer, default=0)
    anuncios_encerrados = Column(Integer, default=0)
    criado_em = Column(DateTime, default=datetime.utcnow)


class Notificacao(Base):
    """Item do sino de notificações. `brand_id` nulo = notificação geral (nova versão do
    sistema) — todo mundo vê. Com marca = alerta específico (saldo crítico, gasto fora da
    curva) — só quem tem acesso a essa marca vê."""

    __tablename__ = "notificacoes"

    id = Column(Integer, primary_key=True)
    tipo = Column(String, nullable=False)  # "saldo_critico" | "gasto_anomalo" | "versao"
    titulo = Column(String, nullable=False)
    mensagem = Column(String, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class SistemaEstado(Base):
    """Uma linha só (singleton) com estado geral do backend que precisa sobreviver a
    reinícios — hoje só a última versão notificada no sino, pra saber quando uma versão nova
    subiu e ninguém ainda foi avisado."""

    __tablename__ = "sistema_estado"

    id = Column(Integer, primary_key=True)
    ultima_versao_notificada = Column(String, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)


class SnapshotDiario(Base):
    """Foto do que cada marca fez em UM dia, gravada uma vez e nunca reescrita. `DailyMetric`
    é dado vivo (o Meta reatribui lead por alguns dias, então o número de ontem muda depois);
    aqui fica o que se enxergava na manhã seguinte, pra comparar e pra ter registro histórico.
    `fonte`: "congelado" = gravado pelo sistema na manhã seguinte; "reconstruido" = montado
    depois a partir de `DailyMetric` (dias anteriores à existência desta tabela)."""

    __tablename__ = "snapshot_diario"
    __table_args__ = (UniqueConstraint("brand_id", "data", name="uq_snapshot_brand_data"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    data = Column(Date, nullable=False)
    gasto = Column(Float, default=0)
    leads_formulario = Column(Integer, default=0)
    leads_whatsapp = Column(Integer, default=0)
    saldo_conta = Column(Float, nullable=True)
    orcamento_planejado = Column(Float, nullable=True)
    orcamento_real = Column(Float, nullable=True)
    fonte = Column(String, default="congelado")
    criado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class RelatorioDiario(Base):
    """Texto do relatório consolidado de um dia (tabela por marca), congelado junto com os
    `SnapshotDiario` desse dia."""

    __tablename__ = "relatorios_diarios"

    id = Column(Integer, primary_key=True)
    data = Column(Date, nullable=False, unique=True)
    texto = Column(String, nullable=False)
    total_gasto = Column(Float, default=0)
    total_leads = Column(Integer, default=0)
    fonte = Column(String, default="congelado")
    criado_em = Column(DateTime, default=datetime.utcnow)


class CampanhaDailyMetric(Base):
    """Gasto e leads por CAMPANHA por dia. Sem isto, qualquer análise abaixo do nível de marca
    (qual campanha piorou, a troca de estrutura da Nissan) precisava ir ao vivo na API do Meta.
    `orcamento_diario` é o orçamento configurado NO MOMENTO da sincronização, só gravado pros dias
    correntes (ontem/hoje) — o Meta não devolve o orçamento de dias passados, então nos demais
    fica nulo em vez de gravar um valor que não era o da época.

    Ressalva sobre o que veio da carga retroativa (24/06 a 25/09/2026, `backfill_campanhas.py`):
    campanhas apagadas depois não existem mais na API do Meta (não entraram), e o canal WhatsApp
    das antigas foi classificado pelo destino que o conjunto tem HOJE. Pro total por marca de um
    período passado, `DailyMetric` continua sendo a fonte de verdade; esta tabela é o detalhamento.
    O que é gravado pela sincronização a partir de 25/09/2026 segue a mesma regra do `DailyMetric`."""

    __tablename__ = "campanha_daily_metrics"
    __table_args__ = (UniqueConstraint("brand_id", "campanha", "data", name="uq_campanha_brand_data"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    campaign_id = Column(String, nullable=True)
    campanha = Column(String, nullable=False)
    data = Column(Date, nullable=False)
    gasto = Column(Float, default=0)
    leads_formulario = Column(Integer, default=0)
    leads_whatsapp = Column(Integer, default=0)
    destino = Column(String, nullable=True)  # "WHATSAPP" | "MESSENGER" | nulo (formulário/outros)
    orcamento_diario = Column(Float, nullable=True)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")


class SaldoHistorico(Base):
    """Saldo de cada conta de anúncio, um valor por dia (a última leitura do dia). `SaldoCache`
    só guarda o valor atual e sobrescreve."""

    __tablename__ = "saldo_historico"
    __table_args__ = (UniqueConstraint("ad_account_id", "data", name="uq_saldo_conta_data"),)

    id = Column(Integer, primary_key=True)
    ad_account_id = Column(Integer, ForeignKey("ad_accounts.id"), nullable=False)
    data = Column(Date, nullable=False)
    saldo = Column(Float, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    ad_account = relationship("AdAccount")
