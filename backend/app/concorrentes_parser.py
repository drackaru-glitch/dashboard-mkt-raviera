"""Lê o texto copiado da Biblioteca de Anúncios do Meta e transforma em anúncios estruturados.

Por que colar texto em vez de raspar automaticamente: a API oficial da Biblioteca só devolve
anúncios comerciais quando o alvo é União Europeia ou Reino Unido — no Brasil ela retorna
apenas anúncios políticos e de causas sociais, então os anúncios de concessionária não existem
lá. Raspar o site com robô violaria os termos de uso do Meta e é frágil (o layout muda, e sem
login a listagem trava em ~30 resultados). Uma pessoa abrir a página pública, selecionar tudo,
copiar e colar aqui não tem nenhum desses problemas.

O interpretador trabalha em cima do TEXTO visível (não do HTML): as classes CSS do Facebook são
ofuscadas e mudam toda hora, enquanto os rótulos em português ("Identificação da biblioteca",
"Veiculação iniciada em", "Patrocinado") são estáveis porque são conteúdo, não estrutura.
"""

import re
from datetime import date, datetime

MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

MARCADOR_ID = "Identificação da biblioteca"
MARCADOR_INICIO = "Veiculação iniciada"
MARCADOR_PATROCINADO = "Patrocinado"

# Linhas de interface que aparecem no meio do anúncio e não fazem parte do texto dele.
# O rodapé da Biblioteca ("Status do sistema API...Privacidade Termos Cookies...") e o
# cronômetro de vídeo ("0:00 / 0:20") entraram aqui depois de aparecerem grudados no fim do
# último anúncio de uma coleta real — o navegador cola esses textos de interface sem espaço
# entre eles, então o padrão casa só com o início da linha e descarta o resto junto.
LIXO = re.compile(
    r"^(Menu|Ver detalhes|Ver mais|Ver resumo|Plataformas|Ativo|Inativo|Patrocinado|"
    r"Esse anúncio tem|Identificação da biblioteca|Veiculação iniciada|"
    r"Status do sistema API|\d:\d\d\s*/\s*\d:\d\d)"
)

CTAS = [
    "Cadastre-se", "Saiba mais", "Enviar mensagem", "Comprar agora", "Converse conosco",
    "Ligar", "Sign Up", "Learn More", "Send Message", "Shop Now", "Contact Us",
]

# Fallback só pra o módulo funcionar sozinho (testes, uso avulso) se ninguém passar uma lista
# de verdade. Em produção, app.concorrentes carrega ModeloVeiculo do banco e passa pra
# parse_biblioteca — é lá que mora a lista de verdade, editável sem mexer em código (ver
# app.models.ModeloVeiculo).
MODELOS_FALLBACK = ["Corolla Cross", "Corolla", "Hilux Chassi", "Hilux", "SW4"]


# Dobra de acento: tanto a letra pura quanto qualquer variante acentuada apontam pra mesma
# classe — é o que permite "Série" no cadastro casar com "Serie" digitado sem acento no
# anúncio, e vice-versa, nos dois sentidos.
_GRUPOS_ACENTO = [("a", "áàãâ"), ("e", "éê"), ("i", "í"), ("o", "óôõ"), ("u", "ú"), ("c", "ç")]
_DOBRA_ACENTO: dict[str, str] = {}
for _base, _variantes in _GRUPOS_ACENTO:
    _classe = f"[{_base}{_variantes}]"
    _DOBRA_ACENTO[_base] = _classe
    for _v in _variantes:
        _DOBRA_ACENTO[_v] = _classe


def construir_padrao(modelo: str) -> str:
    """Monta o regex de um modelo automaticamente a partir do nome puro (ex.: "T-Cross",
    "Hilux Chassi") — não precisa mais escrever regex à mão pra cada modelo novo.

    Espaço e hífen entre as palavras viram opcionais e intercambiáveis (o mesmo padrão que já
    usávamos manualmente pra "T-Cross"/"HR-V"), porque o mesmo anúncio aparece grafado como
    "T-Cross", "T Cross" ou "TCross" dependendo de quem escreveu. Vogal acentuada também casa
    sem acento (e o contrário) — útil pra "Série" também achar "Serie".
    """
    partes = []
    for palavra in re.split(r"[\s-]+", modelo.strip()):
        if not palavra:
            continue
        letras = [_DOBRA_ACENTO.get(ch.lower(), re.escape(ch)) for ch in palavra]
        partes.append("".join(letras))
    return r"\b" + r"\s?-?\s?".join(partes) + r"\b"


def _modelos_ordenados(modelos: list[str]) -> list[tuple[str, str]]:
    # O mais comprido primeiro: "Corolla Cross" precisa ser tentado antes de "Corolla", senão
    # o genérico vence e o composto nunca é alcançado. Generaliza a ordem que antes tinha que
    # ser mantida à mão na lista.
    unicos = sorted(set(modelos), key=len, reverse=True)
    return [(nome, construir_padrao(nome)) for nome in unicos]


# Tipos de campanha, na ordem de prioridade: um anúncio "PcD" que também cita consórcio é,
# antes de tudo, PcD — o público é o que define a campanha.
TIPOS_CAMPANHA = [
    ("PcD", r"\bpcd\b|pessoa com defici|isen[çc][ãa]o de ipi"),
    ("Taxista", r"\btaxista"),
    ("Produtor rural / CNPJ", r"produtor rural|\bcnpj\b|frotista|pessoa jur[íi]dica"),
    ("Consórcio", r"cons[óo]rcio|carta de cr[ée]dito"),
    # Seminovos vem ANTES de Pós-venda: anúncio de seminovo costuma citar "revisões em dia",
    # e sem essa ordem ele seria classificado como oficina.
    # "semi novos" (com espaço) e "semi-novos" aparecem tanto quanto "seminovos" nos anúncios.
    ("Seminovos", r"semi\s?-?\s?novo|usado[s]? [àa] venda"),
    ("Pós-venda", r"troca de [óo]leo|revis[ãa]o|checkup|check-up|pe[çc]as|oficina"),
    ("Bônus na troca", r"b[ôo]nus.*troca|troca do seu usado"),
    ("Taxa 0%", r"taxa.*\b0%|taxa zero"),
]


def _texto_para_data(texto: str) -> date | None:
    m = re.search(r"(\d{1,2}) de (\w{3})\w*\.? de (\d{4})", texto, re.I)
    if not m:
        return None
    mes = MESES.get(m.group(2)[:3].lower())
    if not mes:
        return None
    try:
        return date(int(m.group(3)), mes, int(m.group(1)))
    except ValueError:
        return None


def _valores_reais(texto: str) -> list[float]:
    """Extrai valores em reais do texto, no formato brasileiro (1.234,56)."""
    achados = []
    for bruto in re.findall(r"R\$\s*([\d][\d.]*(?:,\d{2})?)", texto):
        try:
            achados.append(float(bruto.replace(".", "").replace(",", ".")))
        except ValueError:
            pass
    return achados


def _preco_de_por(texto: str) -> tuple[float | None, float | None]:
    """Acha o par "de X por Y". Sem o par explícito não arrisca: dois valores soltos no texto
    podem ser preço e bônus, e inventar um desconto que não existe é pior que não ter o dado."""
    m = re.search(
        r"de\s*R\$\s*([\d][\d.]*(?:,\d{2})?).{0,40}?por\s*(?:apenas\s*)?R\$\s*([\d][\d.]*(?:,\d{2})?)",
        texto, re.I | re.S,
    )
    if m:
        try:
            de = float(m.group(1).replace(".", "").replace(",", "."))
            por = float(m.group(2).replace(".", "").replace(",", "."))
            if por < de:  # só aceita se realmente for um desconto
                return de, por
        except ValueError:
            pass
    return None, None


def _preco_unico(texto: str) -> float | None:
    """Preço anunciado sozinho, sem par "de/por" — oferta direta tipo "por apenas R$ X".

    Só roda quando _preco_de_por não achou par nenhum. A Kampai sempre anuncia com desconto
    ("de X por Y"); a Recol (Ford) anuncia com preço único ("Territory por apenas R$ 199.900"),
    categoria que o parser não tratava — 5 de 7 anúncios de uma coleta real ficavam sem preço
    nenhum por causa disso.

    Quando há mais de uma menção de "por R$" no mesmo anúncio, fica com a ÚLTIMA: o texto
    costuma citar um valor de contexto primeiro ("entregava tudo que você procura por
    R$149.990") e o preço de verdade da oferta por último ("agora por apenas R$139.990!") —
    pegar o primeiro valor anunciaria um preço que não é mais o da oferta.
    """
    valores = re.findall(r"\bpor\s+(?:apenas\s+)?R\$\s*([\d][\d.]*(?:,\d{2})?)", texto, re.I)
    if not valores:
        return None
    try:
        return float(valores[-1].replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _bonus(texto: str) -> float | None:
    """Bônus na troca do usado.

    Os anúncios escrevem isso de vários jeitos, e em duas ordens diferentes — "bônus de 40 MIL"
    mas também "40 MIL DE BÔNUS" — às vezes com palavras no meio ("bônus IMPERDÍVEL! 40 MIL").
    Por isso são três tentativas, da mais específica pra mais solta, e todas exigem a palavra
    "bônus" por perto: um "R$ 10.000" solto no texto costuma ser desconto, não bônus.
    """
    # 1) número ANTES: "40 MIL DE BÔNUS"
    m = re.search(r"(\d{1,3})\s*mil\s*(?:de\s*)?b[ôo]nus", texto, re.I)
    if m:
        return float(m.group(1)) * 1000
    # 2) número DEPOIS, em mil: "bônus de até 10 MIL", "bônus IMPERDÍVEL! 40 MIL".
    # Aqui o "!" é permitido no meio porque anúncio usa exclamação a todo momento, e "N mil"
    # não se confunde com preço — preço vem sempre escrito por extenso ("R$ 149.990").
    m = re.search(r"b[ôo]nus[^.?]{0,40}?(?:R\$\s*)?(\d{1,3})\s*mil", texto, re.I)
    if m:
        return float(m.group(1)) * 1000
    # 3) número DEPOIS, em reais: "bônus de R$ 40.000". Aqui a janela é estreita de propósito:
    # é onde mora o risco de capturar o preço ("bônus! De R$ 149.990 por...") como se fosse bônus.
    m = re.search(r"b[ôo]nus[^.!?]{0,40}?R\$\s*([\d][\d.]*(?:,\d{2})?)", texto, re.I)
    if m:
        try:
            return float(m.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            pass
    return None


def _eh_seminovo(texto: str) -> bool:
    """Anúncio de seminovo quase nunca escreve a palavra "seminovo" — ele se identifica pelo
    par de anos do veículo ("Hilux SR 23/24", "RAV4 22/23"), que é como o mercado anuncia usado.
    Numa coleta real, 4 de 7 anúncios de seminovo passavam batidos só procurando a palavra.

    O par precisa ser de anos plausíveis e consecutivos (ou iguais) pra não confundir com data:
    "31/08" tem 08 != 32, então é descartado; "24/25" passa.
    """
    if re.search(r"\b0\s?km\b", texto, re.I):
        return False  # zero km declarado: não é seminovo, por mais anos que apareçam
    for a, b in re.findall(r"\b(\d{2})/(\d{2})\b(?!/)", texto):
        ia, ib = int(a), int(b)
        if 10 <= ia <= 35 and ib in (ia, ia + 1):
            return True
    return False


def _classificar(texto: str, padroes: list[tuple[str, str]]) -> str | None:
    for rotulo, padrao in padroes:
        if re.search(padrao, texto, re.I):
            return rotulo
    return None


def _tipo_campanha(texto: str) -> str | None:
    tipo = _classificar(texto, TIPOS_CAMPANHA)
    # Público (PcD, taxista, produtor rural) manda sobre tudo: é o que define a campanha.
    # Fora esses, um par de anos no texto significa seminovo, mesmo sem a palavra.
    if tipo in ("PcD", "Taxista", "Produtor rural / CNPJ", "Consórcio", "Seminovos"):
        return tipo
    if _eh_seminovo(texto):
        return "Seminovos"
    return tipo


def _modelo(texto: str, modelos_ordenados: list[tuple[str, str]]) -> str | None:
    """Escolhe o modelo certo quando mais de um nome bate no mesmo texto.

    Não usa _classificar aqui (que devolve o primeiro da LISTA que casar, ordem arbitrária
    pra nomes do mesmo tamanho) — em vez disso, testa todos os candidatos e fica com o que
    começa mais CEDO no texto. Isso resolve dois casos ao mesmo tempo:

    - "Corolla Cross" x "Corolla": os dois começam no MESMO ponto do texto (Corolla é prefixo
      de Corolla Cross), e aí desempata pelo nome mais comprido — o composto vence, do jeito
      que já era antes.
    - "Etios e Yaris" (mesmo anúncio cita os dois, tamanho de nome igual): antes a escolha
      dependia da ordem de iteração de um `set` em Python, que não é garantida — rodar a
      mesma coleta duas vezes podia devolver modelos diferentes. Agora fica sempre com o que
      é citado primeiro no texto, que é determinístico e é o que uma pessoa lendo diria.
    """
    melhor: tuple[int, int] | None = None
    nome_escolhido: str | None = None
    for nome, padrao in modelos_ordenados:
        m = re.search(padrao, texto, re.I)
        if not m:
            continue
        chave = (m.start(), -len(nome))
        if melhor is None or chave < melhor:
            melhor, nome_escolhido = chave, nome
    return nome_escolhido


def parse_biblioteca(texto_colado: str, modelos: list[str] | None = None) -> list[dict]:
    """Recebe o texto bruto copiado da página da Biblioteca e devolve a lista de anúncios.

    `modelos` é a lista de nomes puros (ex.: ["Corolla Cross", "Hilux", ...]) vinda do banco
    (app.models.ModeloVeiculo) — quem chama isso normalmente é app.concorrentes, que já busca
    essa lista antes de importar. Sem ela, usa MODELOS_FALLBACK (só o suficiente pra este
    módulo funcionar sozinho fora do fluxo normal)."""
    modelos_ordenados = _modelos_ordenados(modelos if modelos is not None else MODELOS_FALLBACK)
    linhas = [l.strip() for l in (texto_colado or "").split("\n")]
    inicios = [i for i, l in enumerate(linhas) if l.startswith(MARCADOR_ID)]

    anuncios = []
    for k, ini in enumerate(inicios):
        fim = inicios[k + 1] if k + 1 < len(inicios) else len(linhas)
        bloco = [l for l in linhas[ini:fim] if l]

        m_id = re.search(r"(\d+)", bloco[0])
        if not m_id:
            continue
        ad_id = m_id.group(1)

        linha_inicio = next((l for l in bloco if l.startswith(MARCADOR_INICIO)), "")
        inicio = _texto_para_data(linha_inicio)

        if MARCADOR_PATROCINADO not in bloco:
            continue
        i_pat = bloco.index(MARCADOR_PATROCINADO)
        if i_pat == 0:
            continue
        pagina = bloco[i_pat - 1]

        corpo = [l for l in bloco[i_pat + 1:] if not LIXO.match(l)]
        dominio = next((l for l in corpo if re.fullmatch(r"[A-Z0-9.\-]+\.(COM|BR|COM\.BR)", l)), None)
        cta = next((c for c in CTAS if c in corpo), None)

        texto = " ".join(l for l in corpo if l != dominio and l != cta)
        texto = re.sub(r"\s+", " ", texto).strip()

        de, por = _preco_de_por(texto)
        if de is None and por is None:
            por = _preco_unico(texto)
        anuncios.append({
            "ad_library_id": ad_id,
            "pagina": pagina,
            "inicio": inicio,
            "modelo": _modelo(texto, modelos_ordenados),
            "tipo_campanha": _tipo_campanha(texto),
            "preco_de": de,
            "preco_por": por,
            "bonus": _bonus(texto),
            "cta": cta,
            "dominio": dominio,
            "varias_versoes": any("várias versões" in l for l in bloco),
            "texto": texto[:1200],
        })
    return anuncios
