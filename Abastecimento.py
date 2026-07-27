import calendar
from datetime import datetime
import os
from dotenv import load_dotenv
import numpy as np
import pandas as pd
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Mantenha suas funções de extração originais intocadas abaixo:
def extrair_gobrax(data_inicio, data_fim):
    pass # <-- NÃO APAGUE A SUA FUNÇÃO, mantenha o código original da Gobrax aqui

def extrair_ticketlog(data_inicio, data_fim):
    pass # <-- NÃO APAGUE A SUA FUNÇÃO, mantenha o código original da Ticketlog aqui

def processar_relatorio(ano, mes):
    print(f"\n🚀 Iniciando processamento para {mes:02d}/{ano}...")

    # 1. LIMITA A DATA ATÉ O DIA DE HOJE (Evita o erro da API da Ticketlog)
    primeiro_dia = datetime(ano, mes, 1)
    ultimo_dia_mes = datetime(ano, mes, calendar.monthrange(ano, mes)[1], 23, 59, 59)
    ultimo_dia = min(ultimo_dia_mes, datetime.now()) # <-- Trava no dia atual!

    print(f"📅 Período: {primeiro_dia.strftime('%d/%m/%Y')} até {ultimo_dia.strftime('%d/%m/%Y %H:%M:%S')}")

    print("[1/4] 📡 Buscando dados na Gobrax...")
    df_gobrax = extrair_gobrax(
        primeiro_dia.strftime("%Y-%m-%d %H:%M:%S"), 
        ultimo_dia.strftime("%Y-%m-%d %H:%M:%S")
    )

    print("[2/4] ⛽ Buscando dados na Ticketlog...")
    df_ticket_completo = extrair_ticketlog(primeiro_dia, ultimo_dia)

    # 2. SE A TICKETLOG MANDAR VAZIO, ELE PULA EM VEZ DE DAR CRASH E PARAR TUDO
    if df_ticket_completo is None or df_ticket_completo.empty:
        print(f"⚠️ A API não retornou dados para {mes:02d}/{ano}. Pulando...")
        return False

    print("\n[3/4] 🧠 Processando inteligência de dados...")

    # --- ABASTECIMENTOS ---
    df_abast = df_ticket_completo.copy()
    dados_abast_dict = df_abast.where(pd.notnull(df_abast), None).to_dict(orient="records")

    try:
        supabase.table("fato_abastecimento").upsert(
            dados_abast_dict, on_conflict="codigo_transacao"
        ).execute()
        print(f"  ↳ ✅ {len(dados_abast_dict)} registros gravados na 'fato_abastecimento'.")
    except Exception as e:
        print(f"  ↳ ❌ Erro ao salvar na fato_abastecimento: {e}")

    # --- FECHAMENTO DE FROTA ---
    df_ticket = df_ticket_completo.copy()
    df_ticket = df_ticket.rename(columns={
        'placa': 'PLACA', 'litros': 'Litros', 'valor_total': 'Valor Total',
        'produto': 'Combustível', 'km_rodado': 'KM Rodado'
    })

    def cat_rs(tipo):
        tipo = str(tipo).upper()
        if "ARLA" in tipo: return "ARLA 32"
        if "GNV" in tipo or "GAS NATURAL" in tipo: return "GNV"
        return "DIESEL"

    def cat_l(tipo):
        tipo = str(tipo).upper()
        if "GNV" in tipo or "GAS NATURAL" in tipo: return "GNV"
        return "DIESEL"

    df_ticket["Cat_RS"] = df_ticket["Combustível"].apply(cat_rs)
    df_ticket["Cat_L"] = df_ticket["Combustível"].apply(cat_l)

    df_ticket_geral = df_ticket.groupby("PLACA").agg(
        Km_Ticket=("KM Rodado", "sum"),
        Consumo_Ticket=("Litros", "sum"),
        Custo_Total_Ticket=("Valor Total", "sum")
    ).reset_index()

    df_vol = df_ticket.groupby(["PLACA", "Cat_L"])["Litros"].sum().unstack(fill_value=0)
    df_vol.columns = [f"{c} (L)" for c in df_vol.columns]

    df_fin = df_ticket.groupby(["PLACA", "Cat_RS"])["Valor Total"].sum().unstack(fill_value=0)
    df_fin.columns = [f"{c} (R$)" for c in df_fin.columns]

    df_comb = df_vol.join(df_fin).reset_index()

    df_final = pd.merge(df_gobrax, df_ticket_geral, on="PLACA", how="outer").fillna(0)
    df_final = pd.merge(df_final, df_comb, on="PLACA", how="left").fillna(0)

    colunas_combustivel = ['DIESEL (L)', 'DIESEL (R$)', 'GNV (L)', 'GNV (R$)', 'ARLA 32 (R$)']
    for c in colunas_combustivel:
        if c not in df_final.columns:
            df_final[c] = 0.0

    PLACAS_100_GNV = ['TBJ5C78','TBJ5C79','TBJ5C80','TBJ5C81','TBJ5C82','TBJ5C83','TBJ5C84','TBJ5C85','TBJ5C86']
    df_final["Veiculo_100_GNV"] = df_final["PLACA"].isin(PLACAS_100_GNV)

    df_final["KM Rodado Real"] = np.where(
        df_final["Veiculo_100_GNV"], df_final["Km_Ticket"],
        np.where(df_final["KM_RODADO"] > 0, df_final["KM_RODADO"], df_final["Km_Ticket"])
    )

    df_final["Consumo Real (L)"] = np.where(
        df_final["Veiculo_100_GNV"], df_final["Consumo_Ticket"],
        np.where(df_final["CONSUMO_TOTAL"] > 0, df_final["CONSUMO_TOTAL"], df_final["Consumo_Ticket"])
    )

    df_final["Média KM/L"] = np.where(df_final["Consumo Real (L)"] > 0, df_final["KM Rodado Real"] / df_final["Consumo Real (L)"], 0)
    df_final["Preço Médio Litro (R$)"] = np.where(df_final["Consumo_Ticket"] > 0, df_final["Custo_Total_Ticket"] / df_final["Consumo_Ticket"], 0)
    
    df_final["Fonte do Dado"] = np.where(
        df_final["Veiculo_100_GNV"], "Ticketlog (Lista VIP 100% GNV)",
        np.where(df_final["CONSUMO_TOTAL"] > 0, "Gobrax", "Ticketlog (Faltou Gobrax)")
    )

    df_final["AGRUPAMENTO"] = df_final["AGRUPAMENTO"].replace(0, "NÃO INFORMADO")
    df_final["ANO"] = ano
    df_final["MES"] = mes
    df_final["id"] = df_final["ANO"].astype(str) + "_" + df_final["MES"].astype(str) + "_" + df_final["PLACA"]

    df_fechamento_supabase = pd.DataFrame({
        'id': df_final["id"],
        'ano': df_final["ANO"],
        'mes': df_final["MES"],
        'placa': df_final["PLACA"],
        'agrupamento': df_final["AGRUPAMENTO"],
        'km_rodado_real': df_final["KM Rodado Real"].round(2),
        'consumo_real_l': df_final["Consumo Real (L)"].round(2),
        'media_km_l': df_final["Média KM/L"].round(2),
        'preco_medio_litro_rs': df_final["Preço Médio Litro (R$)"].round(2),
        'fonte_do_dado': df_final["Fonte do Dado"],
        'diesel_l': df_final["DIESEL (L)"].round(2),
        'diesel_rs': df_final["DIESEL (R$)"].round(2),
        'gnv_l': df_final["GNV (L)"].round(2),
        'gnv_rs': df_final["GNV (R$)"].round(2),
        'arla_32_rs': df_final["ARLA 32 (R$)"].round(2)
    })

    print("[4/4] 📤 Gravando fechamento consolidado no Supabase...")
    dados_fech_dict = df_fechamento_supabase.where(pd.notnull(df_fechamento_supabase), None).to_dict(orient="records")

    try:
        supabase.table("fato_fechamento_frota").upsert(
            dados_fech_dict, on_conflict="id"
        ).execute()
        print(f"  ↳ ✅ {len(dados_fech_dict)} veículos consolidados na 'fato_fechamento_frota'!")
        return True
    except Exception as e:
        print(f"  ↳ ❌ Erro ao salvar na fato_fechamento_frota: {e}")
        return False
