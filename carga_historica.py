import time
from Abastecimento import processar_relatorio

# Roda APENAS o mês atual que tem dados na API
ANOS_E_MESES = [
    (2026, [7]),
]

if __name__ == "__main__":
    print("🚀 INICIANDO CARGA DO MÊS ATUAL NO SUPABASE...\n")
    for ano, meses in ANOS_E_MESES:
        for mes in meses:
            processar_relatorio(ano, mes)
    print("\n🎉 CARGA CONCLUÍDA COM SUCESSO!")
