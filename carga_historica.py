import time
from Abastecimento import processar_relatorio

# ---------------------------------------------------------------------------
# 📅 CONFIGURAÇÃO DE EXECUÇÃO
# ---------------------------------------------------------------------------
ANOS_E_MESES = [
    (2026, [7]),  # Processa o mês de Julho/2026
]

if __name__ == "__main__":
    print("🚀 INICIANDO CARGA DO MÊS ATUAL NO SUPABASE...\n")

    total_meses = sum(len(meses) for _, meses in ANOS_E_MESES)
    contador = 0

    for ano, meses in ANOS_E_MESES:
        for mes in meses:
            contador += 1
            print("=" * 60)
            print(f"📌 [{contador}/{total_meses}] Processando Mês: {mes:02d}/{ano}")
            print("=" * 60)

            try:
                sucesso = processar_relatorio(ano, mes)
                if sucesso:
                    print(
                        f"✅ Mês {mes:02d}/{ano} enviado com sucesso para o Supabase!"
                    )
                else:
                    print(
                        f"⚠️ Mês {mes:02d}/{ano} cancelado ou sem dados válidos."
                    )
            except Exception as e:
                print(f"❌ Erro ao processar {mes:02d}/{ano}: {e}")

            time.sleep(2)

    print("\n🎉 EXECUÇÃO CONCLUÍDA COM SUCESSO!")
