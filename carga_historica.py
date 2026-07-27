import time
from Abastecimento import processar_relatorio

# ---------------------------------------------------------------------------
# 📅 CONFIGURAÇÃO PARA OS ANOS DE 2025 E 2026 (19 MESES NO TOTAL)
# ---------------------------------------------------------------------------
ANOS_E_MESES = [
    (2025, range(1, 13)),  # 2025: Janeiro a Dezembro (1 a 12)
    (2026, range(1, 8)),   # 2026: Janeiro a Julho (1 a 7)
]

if __name__ == "__main__":
    print("🚀 INICIANDO CARGA HISTÓRICA (2025 e 2026) NO SUPABASE...\n")

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
                    print(f"✅ Mês {mes:02d}/{ano} enviado com sucesso para o Supabase!")
                else:
                    print(f"⚠️ Mês {mes:02d}/{ano} não foi gravado (sem dados da API).")
            except Exception as e:
                print(f"❌ Erro ao processar {mes:02d}/{ano}: {e}")

            # Pausa de 3 segundos entre os meses para evitar bloqueios de API
            time.sleep(3)

    print("\n🎉 CARGA HISTÓRICA (2025 + 2026) CONCLUÍDA COM SUCESSO!")
