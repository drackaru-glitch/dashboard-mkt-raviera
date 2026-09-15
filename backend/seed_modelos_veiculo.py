"""Popula app.models.ModeloVeiculo com os modelos que o interpretador de anúncios de
concorrentes (app.concorrentes_parser) deve reconhecer.

Por que isso existe: a lista de modelos vivia fixa no código e só crescia quando um
concorrente novo já tinha quebrado o parser — Toyota (Kampai), depois Jeep/Honda/Hyundai
(pátio de seminovos multimarca), depois Ford inteira (Recol Veículos, nenhum dos 7 anúncios
reconheceu modelo). Em vez de esperar o próximo concorrente expor mais um buraco, esta lista
foi pesquisada a partir do mercado brasileiro atual (setembro/2026) e cobre as marcas mais
prováveis de aparecer como concorrente das marcas do grupo: Toyota, Jeep, Ram, Hyundai,
Nissan, BMW/MINI e BYD, e os rivais diretos de cada praça (Honda, Ford, Chevrolet, VW, Fiat,
Renault, Peugeot, Citroën, Mitsubishi, Kia, Chery/Caoa Chery, GWM).

Deliberadamente FORA da lista: marcas de luxo que não rodam campanha de geração de cadastro
no Facebook (Ferrari, Lamborghini, Rolls-Royce, Bentley) e nomes de modelo curtos demais pra
não colidir com outra coisa no texto (ex.: "208", "A3", "Classe A" — risco de casar um preço,
uma data ou um rótulo qualquer). Sempre que um nome puramente numérico for necessário, ele
entra prefixado com a marca (ex.: "Ram 1500", não "1500" solto).

Uso:
    python seed_modelos_veiculo.py             # mostra o que vai inserir, sem gravar
    python seed_modelos_veiculo.py --gravar    # grava de verdade

Seguro rodar mais de uma vez: só insere o que ainda não existe (marca+modelo já cadastrado é
pulado, nunca duplicado nem sobrescrito — se você editou um modelo manualmente, isso não é
desfeito).
"""

import argparse
import sys

sys.path.insert(0, r"C:\Users\Raviera\dashboard-mkt-raviera\backend")

from app.database import Base, SessionLocal, engine
from app.models import ModeloVeiculo

# (marca, [modelos])
MODELOS_POR_MARCA = {
    "Toyota": [
        "Corolla Cross", "Corolla", "Hilux Chassi", "Hilux SW4", "Hilux", "SW4",
        "Yaris Cross", "Yaris", "RAV4", "Etios", "Camry", "Prius",
    ],
    "Jeep": ["Renegade", "Compass", "Commander", "Wrangler", "Gladiator"],
    "Ram": ["Ram 1500", "Ram 2500", "Ram 3500", "Rampage"],
    "Hyundai": ["HB20S", "HB20", "Creta", "Tucson", "Santa Fe", "ix35"],
    "Nissan": ["Kicks Play", "Kicks", "Versa", "Sentra", "Frontier", "March"],
    "BMW": ["Série 3", "Série 5", "X1", "X3", "X5", "X6"],
    "BYD": [
        "Dolphin Mini", "Dolphin Plus", "Dolphin", "Song Plus", "Yuan Plus", "Atto 3",
        "Seal", "Han", "Tan", "Shark", "King",
    ],
    "Honda": ["City", "Civic", "HR-V", "WR-V", "CR-V", "ZR-V", "Fit"],
    "Ford": ["Territory", "Ranger", "F-150", "Bronco", "Maverick", "EcoSport", "Ka"],
    "Chevrolet": [
        "Onix Plus", "Onix", "Tracker", "Montana", "Spin", "S10", "Trailblazer",
        "Equinox", "Sonic", "Cruze",
    ],
    "Volkswagen": [
        "T-Cross", "Polo", "Virtus", "Nivus", "Taos", "Tera", "Saveiro", "Amarok",
        "Golf", "Jetta", "Gol",
    ],
    "Fiat": ["Fastback", "Argo", "Cronos", "Pulse", "Strada", "Toro", "Mobi", "Uno"],
    "Renault": ["Kardian", "Kwid", "Sandero", "Logan", "Duster", "Oroch", "Captur"],
    "Peugeot": ["Peugeot 208", "Peugeot 2008", "Peugeot 3008", "Peugeot 408", "Partner"],
    "Citroën": ["C3 Aircross", "C4 Cactus", "Basalt", "C3"],
    "Mitsubishi": ["L200 Triton", "L200", "Outlander", "Eclipse Cross", "ASX"],
    "Kia": ["Sportage", "Sorento", "Cerato", "Stonic", "Bongo"],
    "Chery": ["Tiggo 5X", "Tiggo 7", "Tiggo 8", "Arrizo 5", "Arrizo 6"],
    "GWM": ["Haval H6", "Ora 03", "Poer", "Tank 300"],
    # rivais premium — inclusão leve, só nomes distintos o bastante pra não colidir
    "Volvo": ["XC40", "XC60", "XC90"],
    "Land Rover": ["Defender", "Discovery", "Evoque"],
    "Audi": ["Q3", "Q5"],
    "Subaru": ["Forester", "Outback", "Crosstrek"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gravar", action="store_true", help="grava no banco (sem isto, só simula)")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existentes = {(m.marca, m.modelo) for m in db.query(ModeloVeiculo).all()}
        novos = 0
        for marca, modelos in MODELOS_POR_MARCA.items():
            for modelo in modelos:
                if (marca, modelo) in existentes:
                    continue
                print(f"  [novo] {marca:12s} {modelo}")
                novos += 1
                if args.gravar:
                    db.add(ModeloVeiculo(marca=marca, modelo=modelo))
        if args.gravar:
            db.commit()
        total_marcas = len(MODELOS_POR_MARCA)
        total_modelos = sum(len(v) for v in MODELOS_POR_MARCA.values())
        print(f"\n{novos} modelo(s) novo(s) de {total_modelos} pesquisados em {total_marcas} marcas.")
        if not args.gravar:
            print("Simulação — nada foi gravado. Rode de novo com --gravar quando estiver satisfeito.")
        else:
            total_no_banco = db.query(ModeloVeiculo).filter(ModeloVeiculo.ativo.is_(True)).count()
            print(f"Total agora no banco: {total_no_banco} modelos ativos.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
