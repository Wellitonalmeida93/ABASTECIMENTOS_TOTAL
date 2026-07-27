import calendar
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import time
import warnings

from dotenv import load_dotenv
import numpy as np
import pandas as pd
import psycopg2
import requests
from supabase import Client, create_client
from tqdm import tqdm

# Desativa avisos de compatibilidade do Pandas no log do GitHub Actions
warnings.filterwarnings("ignore", category=UserWarning)

# Carrega variáveis de ambiente (Secrets no GitHub)
load_dotenv()

# =====================================================================
# 0. LISTA DE PLACAS BLOQUEADAS COMO 100% GNV (REGRA VIP)
# =====================================================================
PLACAS_100_GNV = [
    "TBJ5C78", "TBJ5C79", "TBJ5C80", "TBJ5C81", "TBJ5C82", 
    "TBJ5C83", "TBJ5C84", "TBJ5C85", "TBJ5C86",
]

# =====================================================================
# 1. CONFIGURAÇÕES E CREDENCIAIS
# =====================================================================
TOKEN_GOBRAX = os.getenv("TOKEN_GOBRAX")
URL_GOBRAX = "https://gateway-v3.gobrax.com.br:8889/api/v1/vehicle-statistics"
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

AUTHORIZATION_TICKET = os.getenv("AUTHORIZATION_TICKET")
CODIGO_CLIENTE = os.getenv("CODIGO_CLIENTE")
URL_TICKET = "https://srv1.ticketlog.com.br/ticketlog-servicos/ebs/transacaoVeiculo/search"

# Credenciais Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Inicializa conexão com Supabase se as credenciais existirem
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"⚠️ Erro ao inicializar cliente Supabase: {e}")

# =====================================================================
# 1.1 FUNÇÕES DE GRAVAÇÃO NO SUPABASE
# =====================================================================
def salvar_ticketlog_supabase(df_ticket):
    if df_ticket.empty or not supabase:
        return

    df = df_ticket.copy()
    df_banco = pd.DataFrame({
        "codigo_transacao": df["Transação"].astype(str),
        "data_abastecimento": df["Data"],
        "hora_abastecimento": df["Hora"],
        "placa": df["PLACA"],
        "km_odometro": pd.to_numeric(df["Hodômetro Atual"], errors="coerce").fillna(0),
        "km_rodado": pd.to_numeric(df["KM Rodado"], errors="coerce").fillna(0),
        "posto": df["Posto"],
        "cidade": df["Cidade"],
        "uf": df["UF"],
        "produto": df["Combustível"],
        "litros": pd.to_numeric(df["Litros"], errors="coerce").fillna(0),
        "valor_total": pd.to_numeric(df["Valor Total"], errors="coerce").fillna(0),
        "valor_unitario": pd.to_numeric(df["Valor/Litro"], errors="coerce").fillna(0),
        "cartao": df["Cartão"].astype(str),
        "tipo_consideracao": df["Status"],
        "fonte_api": "Ticket Log",
    })

    df_banco = df_banco.where(pd.notnull(df_banco), None)
    registros = df_banco.to_dict(orient="records")

    try:
        supabase.table("fato_abastecimento").upsert(registros, on_conflict="codigo_transacao").execute()
        print(f"✅ Supabase: {len(registros)} abastecimentos salvos com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao salvar abastecimentos no Supabase: {e}")


def salvar_fechamento_supabase(df_relatorio):
    if df_relatorio.empty or not supabase:
        return

    df = df_relatorio.copy()
    df["id"] = df["ANO"].astype(str) + "_" + df["MES"].astype(str) + "_" + df["PLACA"].astype(str)

    df_banco = pd.DataFrame({
        "id": df["id"],
        "ano": df["ANO"],
        "mes": df["MES"],
        "placa": df["PLACA"],
        "agrupamento": df["AGRUPAMENTO"],
        "km_rodado_real": df["KM Rodado Real"],
        "consumo_real_l": df["Consumo Real (L)"],
        "media_km_l": df["Média KM/L"],
        "preco_medio_litro_rs": df["Preço Médio Litro (R$)"],
        "fonte_do_dado": df["Fonte do Dado"],
        "diesel_l": df["DIESEL (L)"],
        "diesel_rs": df["DIESEL (R$)"],
        "gnv_l": df["GNV (L)"],
        "gnv_rs": df["GNV (R$)"],
        "arla_32_rs": df["ARLA 32 (R$)"],
    })

    df_banco = df_banco.where(pd.notnull(df_banco), None)
    registros = df_banco.to_dict(orient="records")

    try:
        supabase.table("fato_fechamento_frota").upsert(registros, on_conflict="id").execute()
        print(f"✅ Supabase: {len(registros)} registros de fechamento salvos com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao salvar fechamento no Supabase: {e}")

# =====================================================================
# 2. FUNÇÕES DE EXTRAÇÃO DE DADOS
# =====================================================================
def extrair_gobrax(data_inicio_str, data_fim_str):
    print("\n[1/4] 📡 A extrair telemetria (Gobrax) em modo TURBO...")
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    sql_placas = """
    SELECT DISTINCT ON (v."PLACA") v."PLACA", va2."DESCRICAO" AS agrupamento
    FROM veiculo.veiculo v
    JOIN veiculo.veiculo_tipo_carroceria vtc ON vtc."TIPO_CARROCERIA_ID" = vtc."TIPO_CARROCERIA_ID"
    JOIN veiculo.veiculo_agrupamento va2 ON va2."AGRUPAMENTO_ID" = vtc."AGRUPAMENTO_ID"
    JOIN veiculo.veiculo_modalidade_atual vma ON v."PLACA" = vma."PLACA"
    WHERE vma."MODALIDADE" = 'FROTA' AND v."PLACA" IS NOT NULL
      AND va2."DESCRICAO" NOT LIKE '%SEMI REBOQUE%' AND va2."DESCRICAO" NOT LIKE '%TERCEIRO%'
    ORDER BY v."PLACA", v."VEICULO_ID" DESC
    """
    df_placas = pd.read_sql_query(sql_placas, conn)
    conn.close()

    def consultar_placa(placa, agrupamento):
        headers = {"Authorization": f"Bearer {TOKEN_GOBRAX}", "Accept": "application/json"}
        params = {"startDate": data_inicio_str, "endDate": data_fim_str, "vehicleIdentification": placa}
        for _ in range(3):
            try:
                r = requests.get(URL_GOBRAX, headers=headers, params=params, timeout=30)
                if r.status_code == 200:
                    records = r.json().get("records", [])
                    if records:
                        item = records[0]
                        return {
                            "PLACA": placa, "AGRUPAMENTO": agrupamento,
                            "KM_RODADO": item.get("totalMileage", 0),
                            "CONSUMO_TOTAL": item.get("totalConsumption", 0),
                        }
                    break
            except:
                time.sleep(1)
        return {"PLACA": placa, "AGRUPAMENTO": agrupamento, "KM_RODADO": 0, "CONSUMO_TOTAL": 0}

    resultado = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(consultar_placa, row.PLACA, row.agrupamento) for row in df_placas.itertuples()]
        for f in tqdm(as_completed(futures), total=len(futures), desc="Gobrax (Placas)"):
            resultado.append(f.result())

    df = pd.DataFrame(resultado)
    df["PLACA"] = df["PLACA"].str.upper().str.strip()
    return df

def extrair_ticketlog(data_inicio, data_fim):
    print("\n[2/4] 💳 A extrair abastecimentos (Ticketlog) em PARALELO...")
    dados = {}
    data_inicio_memoria = data_inicio - timedelta(days=90)
    dias_consulta = []
    data_atual = data_inicio_memoria
    
    while data_atual <= data_fim:
        dias_consulta.append(data_atual)
        data_atual += timedelta(days=1)

    def consultar_dia_ticket(dia):
        registros_dia = []
        inicio_dia = dia.strftime("%Y-%m-%dT00:00:00")
        fim_dia = dia.strftime("%Y-%m-%dT23:59:59")
        for considerar in ["V", "T"]:
            payload = {
                "codigoCliente": CODIGO_CLIENTE, "codigoTipoCartao": 4,
                "dataTransacaoInicial": inicio_dia, "dataTransacaoFinal": fim_dia,
                "considerarTransacao": considerar, "ordem": "S", "validacao": "S",
            }
            headers = {"Content-Type": "application/json", "Authorization": AUTHORIZATION_TICKET}
            for _ in range(3):
                try:
                    r = requests.post(URL_TICKET, json=payload, headers=headers, timeout=30)
                    if r.status_code == 200 and r.json().get("sucesso"):
                        for t in r.json().get("transacoes", []):
                            t["considerarTransacao"] = considerar
                            registros_dia.append(t)
                        break
                except:
                    time.sleep(1)
        return registros_dia

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(consultar_dia_ticket, d) for d in dias_consulta]
        for f in tqdm(as_completed(futures), total=len(futures), desc="Ticketlog (Dias)"):
            for t in f.result():
                key = str(t.get("codigoTransacao"))
                if key not in dados:
                    dados[key] = t

    if not dados:
        print("\n⚠️ Nenhuma transação encontrada na Ticketlog para este período!")
        return pd.DataFrame(columns=["Transação", "Data", "Hora", "PLACA", "Hodômetro Anterior", "Hodômetro Atual", "KM Rodado", "Posto", "Cidade", "UF", "Combustível", "Litros", "Valor Total", "Valor/Litro", "Cartão", "Status"])

    df = pd.DataFrame(list(dados.values()))
    df["placa"] = df["placa"].str.upper().str.strip()
    df["dataTransacao"] = pd.to_datetime(df["dataTransacao"])
    df["quilometragem"] = pd.to_numeric(df["quilometragem"], errors="coerce").fillna(0)
    df = df.sort_values(by=["placa", "dataTransacao"])

    is_arla = df["tipoCombustivel"].fillna("").str.upper().str.contains("ARLA")
    df["Hodometro_Valido"] = df["quilometragem"]
    df.loc[is_arla | (df["quilometragem"] <= 0), "Hodometro_Valido"] = np.nan

    df["Memoria_Hodometro"] = df.groupby("placa")["Hodometro_Valido"].ffill()
    df["Hodômetro Anterior"] = df.groupby("placa")["Memoria_Hodometro"].shift(1).fillna(0)
    
    df["KM Rodado"] = 0.0
    mask_valid = df["Hodometro_Valido"].notna()
    df.loc[mask_valid, "KM Rodado"] = df["Hodometro_Valido"] - df["Hodômetro Anterior"]
    df["KM Rodado"] = np.where(df["KM Rodado"] < 0, 0, df["KM Rodado"])
    df["KM Rodado"] = np.where(df["KM Rodado"] > 3000, 0, df["KM Rodado"])

    df = df[df["dataTransacao"] >= data_inicio]
    df["Data"] = df["dataTransacao"].dt.strftime("%Y-%m-%d")
    df["Hora"] = df["dataTransacao"].dt.strftime("%H:%M:%S")

    colunas_completas = [
        "codigoTransacao", "Data", "Hora", "placa", "Hodômetro Anterior", "quilometragem", 
        "KM Rodado", "nomeReduzidoEstabelecimento", "nomeCidade", "uf", "tipoCombustivel", 
        "litros", "valorTransacao", "valorLitro", "numeroCartao", "considerarTransacao",
    ]
    colunas_presentes = [c for c in colunas_completas if c in df.columns]

    df_formatado = df[colunas_presentes].rename(columns={
        "codigoTransacao": "Transação", "placa": "PLACA", "quilometragem": "Hodômetro Atual",
        "nomeReduzidoEstabelecimento": "Posto", "nomeCidade": "Cidade", "uf": "UF",
        "tipoCombustivel": "Combustível", "litros": "Litros", "valorTransacao": "Valor Total",
        "valorLitro": "Valor/Litro", "numeroCartao": "Cartão", "considerarTransacao": "Status",
    })
    return df_formatado

# =====================================================================
# 3. REGRAS DE NEGÓCIO E CONSOLIDAÇÃO
# =====================================================================
def processar_relatorio(ano, mes):
    primeiro_dia = datetime(ano, mes, 1)
    ultimo_dia = datetime(ano, mes, calendar.monthrange(ano, mes)[1], 23, 59, 59)

    df_gobrax = extrair_gobrax(primeiro_dia.strftime("%Y-%m-%d %H:%M:%S"), ultimo_dia.strftime("%Y-%m-%d %H:%M:%S"))
    df_ticket_completo = extrair_ticketlog(primeiro_dia, ultimo_dia)

    if df_ticket_completo.empty and df_gobrax.empty:
        print("⚠️ Sem dados para processar.")
        return False

    print("\n[3/4] 🧠 A processar inteligência de dados e regras de negócio...")
    df_ticket = df_ticket_completo.copy()

    if not df_ticket.empty and "Litros" in df_ticket.columns:
        df_ticket["Litros"] = pd.to_numeric(df_ticket["Litros"], errors="coerce").fillna(0)
        df_ticket["Valor Total"] = pd.to_numeric(df_ticket["Valor Total"], errors="coerce").fillna(0)

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
            Km_Ticket=("KM Rodado", "sum"), Consumo_Ticket=("Litros", "sum"), Custo_Total_Ticket=("Valor Total", "sum")
        ).reset_index()

        df_vol = df_ticket.groupby(["PLACA", "Cat_L"])["Litros"].sum().unstack(fill_value=0)
        df_vol.columns = [f"{c} (L)" for c in df_vol.columns]

        df_fin = df_ticket.groupby(["PLACA", "Cat_RS"])["Valor Total"].sum().unstack(fill_value=0)
        df_fin.columns = [f"{c} (R$)" for c in df_fin.columns]

        df_comb = df_vol.join(df_fin).reset_index()
    else:
        df_ticket_geral = pd.DataFrame(columns=["PLACA", "Km_Ticket", "Consumo_Ticket", "Custo_Total_Ticket"])
        df_comb = pd.DataFrame(columns=["PLACA"])

    df_final = pd.merge(df_gobrax, df_ticket_geral, on="PLACA", how="outer").fillna(0)
    df_final = pd.merge(df_final, df_comb, on="PLACA", how="left").fillna(0)

    for c in ["GNV (L)", "GNV (R$)", "DIESEL (L)", "DIESEL (R$)", "ARLA 32 (R$)"]:
        if c not in df_final.columns:
            df_final[c] = 0.0

    df_final["Veiculo_100_GNV"] = df_final["PLACA"].isin(PLACAS_100_GNV)

    df_final["KM Rodado Real"] = np.where(df_final["Veiculo_100_GNV"], df_final["Km_Ticket"], np.where(df_final["KM_RODADO"] > 0, df_final["KM_RODADO"], df_final["Km_Ticket"]))
    df_final["Consumo Real (L)"] = np.where(df_final["Veiculo_100_GNV"], df_final["Consumo_Ticket"], np.where(df_final["CONSUMO_TOTAL"] > 0, df_final["CONSUMO_TOTAL"], df_final["Consumo_Ticket"]))
    df_final["Média KM/L"] = np.where(df_final["Consumo Real (L)"] > 0, df_final["KM Rodado Real"] / df_final["Consumo Real (L)"], 0)
    df_final["Preço Médio Litro (R$)"] = np.where(df_final["Consumo_Ticket"] > 0, df_final["Custo_Total_Ticket"] / df_final["Consumo_Ticket"], 0)
    df_final["Fonte do Dado"] = np.where(df_final["Veiculo_100_GNV"], "Ticketlog (Lista VIP 100% GNV)", np.where(df_final["CONSUMO_TOTAL"] > 0, "Gobrax", "Ticketlog (Faltou Gobrax)"))
    
    df_final["AGRUPAMENTO"] = df_final["AGRUPAMENTO"].replace(0, "NÃO INFORMADO")
    df_final.insert(0, "ANO", ano)
    df_final.insert(1, "MES", mes)

    cols_principais = ["ANO", "MES", "PLACA", "AGRUPAMENTO", "KM Rodado Real", "Consumo Real (L)", "Média KM/L", "Preço Médio Litro (R$)", "Fonte do Dado"]
    lista_combustiveis = ["DIESEL (L)", "DIESEL (R$)", "GNV (L)", "GNV (R$)", "ARLA 32 (R$)"]
    cols_finais = cols_principais + lista_combustiveis
    df_relatorio = df_final[cols_finais].sort_values(by="PLACA").round(2)

    # ☁️ GRAVAÇÃO AUTOMÁTICA NO SUPABASE (BANCO NA NUVEM)
    print("\n[4/4] ☁️ Enviando dados para o Supabase (Power BI)...")
    salvar_ticketlog_supabase(df_ticket_completo)
    salvar_fechamento_supabase(df_relatorio)
    
    return True

# =====================================================================
# 4. EXECUÇÃO PRINCIPAL
# =====================================================================
if __name__ == "__main__":
    hoje = datetime.now() - timedelta(days=5)
    ano_alvo = hoje.year
    mes_alvo = hoje.month

    print(f"🚀 A INICIAR AUTOMAÇÃO NO GITHUB ACTIONS | Mês de Ref: {mes_alvo:02d}/{ano_alvo}")
    sucesso = processar_relatorio(ano_alvo, mes_alvo)

    if sucesso:
        print("\n🎉 Processamento concluído e dados atualizados na Nuvem com sucesso!")
