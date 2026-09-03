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

    brands = relationship("UserBrand", back_populates="user", cascade="all, delete-orphan")


class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id = Column(Integer, primary_key=True)
    meta_account_id = Column(String, unique=True, nullable=False)  # ex: act_123456789
    nome = Column(String, nullable=False)

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
    tier = Column(String, default="")
    ad_account_id = Column(Integer, ForeignKey("ad_accounts.id"), nullable=True)
    campaign_filter = Column(String, nullable=True)  # prefixo do nome da campanha, quando a conta é compartilhada
    diario_esperado_meta = Column(Float, default=0)
    analista = Column(String, default="")
    instagram_account_id = Column(Integer, ForeignKey("instagram_accounts.id"), nullable=True)
    google_ads_account_id = Column(Integer, ForeignKey("google_ads_accounts.id"), nullable=True)
    google_orcamento_mensal = Column(Float, default=0)  # verba mensal definida manualmente pra essa marca no Google Ads

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
    __tablename__ = "instagram_stats_cache"

    id = Column(Integer, primary_key=True)
    instagram_account_id = Column(Integer, ForeignKey("instagram_accounts.id"), unique=True, nullable=False)
    seguidores_atual = Column(Integer, default=0)
    seguidores_ganhos_periodo = Column(Integer, default=0)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    instagram_account = relationship("InstagramAccount")


class InstagramTopPostCache(Base):
    __tablename__ = "instagram_top_post_cache"

    id = Column(Integer, primary_key=True)
    instagram_account_id = Column(Integer, ForeignKey("instagram_accounts.id"), unique=True, nullable=False)
    post_id = Column(String, nullable=True)
    legenda = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    permalink = Column(String, nullable=True)
    curtidas = Column(Integer, default=0)
    comentarios = Column(Integer, default=0)
    publicado_em = Column(DateTime, nullable=True)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

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


class GoogleAccountCache(Base):
    __tablename__ = "google_account_cache"

    id = Column(Integer, primary_key=True)
    google_ads_account_id = Column(Integer, ForeignKey("google_ads_accounts.id"), unique=True, nullable=False)
    orcamento_diario = Column(Float, default=0)
    status = Column(String, default="")  # ENABLED, PAUSED, etc.
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
