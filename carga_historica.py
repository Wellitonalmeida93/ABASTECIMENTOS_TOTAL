import time
from Abastecimento import processar_relatorio

ANOS_E_MESES = [
    (2026, [7]),
]

if __name__ == "__main__":
    print("🚀 INICIANDO CARGA NO SUPABASE (VERSÃO ORIGINAL)...\n")

    for ano, meses in ANOS_E_MESES:
        for mes in meses:
            print("=" * 60)
            print(f"📌 Processando Mês: {mes:02d}/{ano}")
            print("=" * 60)
            
            processar_relatorio(ano, mes)
            time.sleep(2)

    print("\n🎉 EXECUÇÃO CONCLUÍDA!")
