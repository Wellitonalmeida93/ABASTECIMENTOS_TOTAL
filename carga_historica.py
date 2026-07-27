import time
# Importa a função principal do seu script que já envia tudo pro Supabase
from Abastecimento import processar_relatorio

# ---------------------------------------------------------------------------
# 📅 DEFINA AQUI QUAIS ANOS E MESES DESEJA CARREGAR
# ---------------------------------------------------------------------------
ANOS_E_MESES = [
    (2024, range(1, 13)),  # 2024: Todos os meses (de 1 a 12)
    (2025, range(1, 13)),  # 2025: Todos os meses (de 1 a 12)
    (2026, range(1, 7)),   # 2026: De Janeiro a Junho (já que Julho acabou de rodar)
]

if __name__ == "__main__":
    print("🚀 INICIANDO CARGA HISTÓRICA COMPLETA NO SUPABASE...\n")

    total_meses = sum(len(meses) for _, meses in ANOS_E_MESES)
    contador = 0

    for ano, meses in ANOS_E_MESES:
        for mes in meses:
            contador += 1
            print("=" * 60)
            print(f"📌 [{contador}/{total_meses}] Processando Histórico: {mes:02d}/{ano}")
            print("=" * 60)

            try:
                sucesso = processar_relatorio(ano, mes)
                if sucesso:
                    print(f"✅ Mês {mes:02d}/{ano} enviado com sucesso para ambas as tabelas!")
                else:
                    print(f"⚠️ Sem dados registrados para {mes:02d}/{ano}.")
            except Exception as e:
                print(f"❌ Erro ao processar {mes:02d}/{ano}: {e}")

            # Pausa de 2 segundos para não sobrecarregar as APIs
            time.sleep(2)

    print("\n🎉 CARGA HISTÓRICA CONCLUÍDA COM SUCESSO!")
    print("As duas tabelas (fato_abastecimento e fato_fechamento_frota) estão 100% atualizadas no Supabase!")
