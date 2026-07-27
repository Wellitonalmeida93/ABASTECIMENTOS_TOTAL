import time
from Abastecimento import processar_relatorio

# 📅 Coloque aqui os meses que você quer forçar a carga
ANOS_E_MESES = [
    (2026, [7]), 
]

if __name__ == "__main__":
    print("🚀 INICIANDO CARGA HISTÓRICA NO SUPABASE...\n")

    for ano, meses in ANOS_E_MESES:
        for mes in meses:
            print("=" * 60)
            print(f"📌 Processando Mês: {mes:02d}/{ano}")
            print("=" * 60)
            
            # Chama a sua função original que agora também salva no banco!
            processar_relatorio(ano, mes)
            
            # Pausa de 2 segundos para não sobrecarregar a API
            time.sleep(2)

    print("\n🎉 CARGA HISTÓRICA CONCLUÍDA!")
