"""Importa os anúncios colados da Biblioteca e mantém o histórico de cada criativo.

O fluxo é em duas etapas de propósito: `analisar` mostra quais anunciantes vieram no texto e
quantos anúncios cada um tem, e só então `importar` grava os que a pessoa escolheu. Sem isso,
uma busca por palavra-chave na Biblioteca traria lixo junto — buscando "kampai" apareceram
restaurante, floricultura e casa de eventos —, e esse lixo entraria como concorrente.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.concorrentes_parser import parse_biblioteca
from app.models import Concorrente, ConcorrenteAnuncio, ConcorrenteColeta, ModeloVeiculo


def _modelos_conhecidos(db: Session) -> list[str]:
    """Nomes puros dos modelos ativos, pra passar pro parser. Fica aqui (e não dentro do
    parser) porque só quem tem sessão de banco sabe o que existe hoje — o parser continua sem
    depender de banco nenhum, só de texto."""
    return [m.modelo for m in db.query(ModeloVeiculo).filter(ModeloVeiculo.ativo.is_(True)).all()]


def analisar(db: Session, texto: str) -> dict:
    """Lê o texto colado e devolve o que foi encontrado, sem gravar nada."""
    anuncios = parse_biblioteca(texto, modelos=_modelos_conhecidos(db))
    registrados = {c.pagina: c for c in db.query(Concorrente).all()}

    por_pagina: dict[str, int] = {}
    for a in anuncios:
        por_pagina[a["pagina"]] = por_pagina.get(a["pagina"], 0) + 1

    return {
        "totalAnuncios": len(anuncios),
        "paginas": [
            {
                "pagina": pag,
                "anuncios": qtd,
                "jaCadastrado": pag in registrados,
                "concorrenteNome": registrados[pag].nome if pag in registrados else None,
            }
            for pag, qtd in sorted(por_pagina.items(), key=lambda x: -x[1])
        ],
    }


def importar(db: Session, texto: str, paginas: list[str] | None = None) -> dict:
    """Grava os anúncios das páginas escolhidas (ou de todas as já cadastradas, se `paginas`
    não vier). Devolve o resumo do que entrou, saiu e continua no ar."""
    anuncios = parse_biblioteca(texto, modelos=_modelos_conhecidos(db))
    hoje = date.today()

    registrados = {c.pagina: c for c in db.query(Concorrente).all()}
    alvo = set(paginas) if paginas else set(registrados)

    resumo = []
    for pagina in alvo:
        do_concorrente = [a for a in anuncios if a["pagina"] == pagina]
        if not do_concorrente:
            continue

        conc = registrados.get(pagina)
        if not conc:
            # Página escolhida pela pessoa mas ainda sem cadastro: cria com o nome do anunciante
            # mesmo. Cidade e marca ficam pra ela ajustar depois na tela de administração.
            conc = Concorrente(nome=pagina, pagina=pagina)
            db.add(conc)
            db.flush()
            registrados[pagina] = conc

        existentes = {
            a.ad_library_id: a
            for a in db.query(ConcorrenteAnuncio).filter(ConcorrenteAnuncio.concorrente_id == conc.id).all()
        }
        vistos_agora = set()
        novos = 0

        for a in do_concorrente:
            vistos_agora.add(a["ad_library_id"])
            reg = existentes.get(a["ad_library_id"])
            if reg:
                # Já conhecíamos: só estende a validade. É esse acúmulo que revela quais
                # criativos sobrevivem — o dado que a Biblioteca sozinha não conta.
                reg.visto_ultimo = hoje
                reg.ativo = True
            else:
                novos += 1
                db.add(
                    ConcorrenteAnuncio(
                        concorrente_id=conc.id,
                        ad_library_id=a["ad_library_id"],
                        inicio=a["inicio"],
                        modelo=a["modelo"],
                        tipo_campanha=a["tipo_campanha"],
                        preco_de=a["preco_de"],
                        preco_por=a["preco_por"],
                        bonus=a["bonus"],
                        cta=a["cta"],
                        dominio=a["dominio"],
                        texto=a["texto"],
                        varias_versoes=a["varias_versoes"],
                        visto_primeiro=a["inicio"] or hoje,
                        visto_ultimo=hoje,
                        ativo=True,
                    )
                )

        # O que não veio nesta coleta saiu do ar. Não apaga: o histórico de quanto tempo o
        # criativo ficou rodando é justamente o que interessa depois.
        encerrados = 0
        for ad_id, reg in existentes.items():
            if ad_id not in vistos_agora and reg.ativo:
                reg.ativo = False
                encerrados += 1

        db.add(
            ConcorrenteColeta(
                concorrente_id=conc.id,
                data=hoje,
                anuncios_encontrados=len(do_concorrente),
                anuncios_novos=novos,
                anuncios_encerrados=encerrados,
            )
        )
        resumo.append(
            {
                "concorrente": conc.nome,
                "encontrados": len(do_concorrente),
                "novos": novos,
                "encerrados": encerrados,
            }
        )

    db.commit()
    return {"data": hoje.isoformat(), "resultado": resumo}


def montar_visao(db: Session) -> dict:
    """Monta os dados da aba: por concorrente, os criativos ativos cruzados por modelo e tipo
    de campanha, mais quem entrou, quem saiu e há quanto tempo cada um está no ar."""
    hoje = date.today()
    saida = []

    for conc in db.query(Concorrente).filter(Concorrente.ativo.is_(True)).order_by(Concorrente.nome).all():
        anuncios = db.query(ConcorrenteAnuncio).filter(ConcorrenteAnuncio.concorrente_id == conc.id).all()
        ativos = [a for a in anuncios if a.ativo]

        # Desempata pelo id além da data: duas importações no MESMO dia (refazer a coleta
        # depois de corrigir algo, por exemplo) empatam em `data`, e sem o id o resumo
        # mostrado seria o da primeira, não o da mais recente.
        ultima = (
            db.query(ConcorrenteColeta)
            .filter(ConcorrenteColeta.concorrente_id == conc.id)
            .order_by(ConcorrenteColeta.data.desc(), ConcorrenteColeta.id.desc())
            .first()
        )

        # cruzamento modelo x tipo de campanha
        grade: dict[str, dict[str, int]] = {}
        for a in ativos:
            modelo = a.modelo or "Outros"
            tipo = a.tipo_campanha or "Geral"
            grade.setdefault(modelo, {})
            grade[modelo][tipo] = grade[modelo].get(tipo, 0) + 1

        def item(a: ConcorrenteAnuncio) -> dict:
            return {
                "id": a.ad_library_id,
                "inicio": a.inicio.isoformat() if a.inicio else None,
                "modelo": a.modelo,
                "tipoCampanha": a.tipo_campanha,
                "precoDe": a.preco_de,
                "precoPor": a.preco_por,
                "bonus": a.bonus,
                "cta": a.cta,
                "dominio": a.dominio,
                "texto": a.texto,
                "diasNoAr": (hoje - a.visto_primeiro).days if a.visto_primeiro else None,
                "vistoUltimo": a.visto_ultimo.isoformat() if a.visto_ultimo else None,
            }

        saida.append(
            {
                "id": conc.id,
                "nome": conc.nome,
                "pagina": conc.pagina,
                "cidade": conc.cidade,
                "marcaChave": conc.marca_chave,
                "marcaRival": conc.brand.nome if conc.brand else None,
                "ativos": len(ativos),
                "ultimaColeta": ultima.data.isoformat() if ultima else None,
                "novosUltimaColeta": ultima.anuncios_novos if ultima else 0,
                "encerradosUltimaColeta": ultima.anuncios_encerrados if ultima else 0,
                "grade": grade,
                # os mais antigos primeiro: criativo que sobrevive é o que converte pra eles
                "anuncios": [item(a) for a in sorted(ativos, key=lambda x: x.visto_primeiro or hoje)],
                "encerrados": [
                    item(a) for a in sorted(
                        (x for x in anuncios if not x.ativo), key=lambda x: x.visto_ultimo, reverse=True
                    )
                ][:20],
            }
        )

    return {"concorrentes": saida}
