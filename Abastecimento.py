import os
import time
import calendar
import requests
import smtplib
import psycopg2
import pandas as pd
import numpy as np
import warnings
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from email.message import EmailMessage
from dotenv import load_dotenv

# Desativa avisos de compatibilidade do Pandas no log do GitHub Actions
warnings.filterwarnings("ignore", category=UserWarning)

# Carrega variáveis de ambiente (Secrets no GitHub)
load_dotenv()

# =====================================================================
# 0. LISTA DE PLACAS BLOQUEADAS COMO 100% GNV (REGRA VIP)
# =====================================================================
PLACAS_100_GNV = [
    "TBJ5C78",
    "TBJ5C79",
    "TBJ5C80",
    "TBJ5C81",
    "TBJ5C82",
    "TBJ5C83",
    "TBJ5C84",
    "TBJ5C85",
    "TBJ5C86"
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

# Credenciais de E-mail (Configure nos Secrets do GitHub)
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.office365.com") # Padrão Outlook/Office365
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

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
                            "CONSUMO_TOTAL": item.get("totalConsumption", 0)
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
    
    # ESTRATÉGIA DE MEMÓRIA LONGA: Puxa 90 dias para trás
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
                "considerarTransacao": considerar, "ordem": "S", "validacao": "S"
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
                key = str(t.get('codigoTransacao'))
                if key not in dados:
                    dados[key] = t
        
    if not dados:
        print("\n⚠️ Nenhuma transação encontrada na Ticketlog para este período!")
        return pd.DataFrame(columns=[
            "Transação", "Data", "Hora", "PLACA", "Hodômetro Anterior", "Hodômetro Atual", "KM Rodado",
            "Posto", "Cidade", "UF", "Combustível", "Litros", "Valor Total",
            "Valor/Litro", "Cartão", "Status"
        ])
        
    df = pd.DataFrame(list(dados.values()))
    
    # -------------------------------------------------------------------------
    # TRATAMENTO CRONOLÓGICO DO ODÔMETRO (TÉCNICA FFILL)
    # -------------------------------------------------------------------------
    df["placa"] = df["placa"].str.upper().str.strip()
    df["dataTransacao"] = pd.to_datetime(df["dataTransacao"])
    df["quilometragem"] = pd.to_numeric(df["quilometragem"], errors='coerce').fillna(0)
    
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
    
    # CORTE DE MEMÓRIA (Mantém na tabela apenas o mês alvo)
    df = df[df["dataTransacao"] >= data_inicio]
    
    df["Data"] = df["dataTransacao"].dt.strftime("%Y-%m-%d")
    df["Hora"] = df["dataTransacao"].dt.strftime("%H:%M:%S")
    
    colunas_completas = [
        "codigoTransacao", "Data", "Hora", "placa", "Hodômetro Anterior", "quilometragem", "KM Rodado",
        "nomeReduzidoEstabelecimento", "nomeCidade", "uf",
        "tipoCombustivel", "litros", "valorTransacao",
        "valorLitro", "numeroCartao", "considerarTransacao"
    ]
    colunas_presentes = [c for c in colunas_completas if c in df.columns]
    
    df_formatado = df[colunas_presentes].rename(columns={
        "codigoTransacao": "Transação",
        "placa": "PLACA",
        "quilometragem": "Hodômetro Atual",
        "nomeReduzidoEstabelecimento": "Posto",
        "nomeCidade": "Cidade",
        "uf": "UF",
        "tipoCombustivel": "Combustível",
        "litros": "Litros",
        "valorTransacao": "Valor Total",
        "valorLitro": "Valor/Litro",
        "numeroCartao": "Cartão",
        "considerarTransacao": "Status"
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
        return None
        
    print("\n[3/4] 🧠 A processar inteligência de dados e regras de negócio...")
    
    df_ticket = df_ticket_completo.copy()
    
    if not df_ticket.empty and "Litros" in df_ticket.columns:
        df_ticket["Litros"] = pd.to_numeric(df_ticket["Litros"], errors='coerce').fillna(0)
        df_ticket["Valor Total"] = pd.to_numeric(df_ticket["Valor Total"], errors='coerce').fillna(0)
        
        # CATEGORIZAÇÃO R$
        def cat_rs(tipo):
            tipo = str(tipo).upper()
            if "ARLA" in tipo: return "ARLA 32"
            if "GNV" in tipo or "GAS NATURAL" in tipo: return "GNV"
            return "DIESEL" 
            
        # CATEGORIZAÇÃO LITROS
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
    else:
        df_ticket_geral = pd.DataFrame(columns=["PLACA", "Km_Ticket", "Consumo_Ticket", "Custo_Total_Ticket"])
        df_comb = pd.DataFrame(columns=["PLACA"])

    df_final = pd.merge(df_gobrax, df_ticket_geral, on="PLACA", how="outer").fillna(0)
    df_final = pd.merge(df_final, df_comb, on="PLACA", how="left").fillna(0)
    
    for c in ["GNV (L)", "GNV (R$)", "DIESEL (L)", "DIESEL (R$)", "ARLA 32 (R$)"]:
        if c not in df_final.columns:
            df_final[c] = 0.0

    df_final['Veiculo_100_GNV'] = df_final["PLACA"].isin(PLACAS_100_GNV)

    df_final["KM Rodado Real"] = np.where(
        df_final["Veiculo_100_GNV"], 
        df_final["Km_Ticket"], 
        np.where(df_final["KM_RODADO"] > 0, df_final["KM_RODADO"], df_final["Km_Ticket"]) 
    )
    
    df_final["Consumo Real (L)"] = np.where(
        df_final["Veiculo_100_GNV"], 
        df_final["Consumo_Ticket"], 
        np.where(df_final["CONSUMO_TOTAL"] > 0, df_final["CONSUMO_TOTAL"], df_final["Consumo_Ticket"]) 
    )

    df_final["Média KM/L"] = np.where(df_final["Consumo Real (L)"] > 0, df_final["KM Rodado Real"] / df_final["Consumo Real (L)"], 0)
    df_final["Preço Médio Litro (R$)"] = np.where(df_final["Consumo_Ticket"] > 0, df_final["Custo_Total_Ticket"] / df_final["Consumo_Ticket"], 0)
    
    df_final["Fonte do Dado"] = np.where(
        df_final["Veiculo_100_GNV"], 
        "Ticketlog (Lista VIP 100% GNV)",
        np.where(df_final["CONSUMO_TOTAL"] > 0, "Gobrax", "Ticketlog (Faltou Gobrax)")
    )

    df_final["AGRUPAMENTO"] = df_final["AGRUPAMENTO"].replace(0, "NÃO INFORMADO")
    df_final.insert(0, "ANO", ano)
    df_final.insert(1, "MES", mes)
    
    cols_principais = ["ANO", "MES", "PLACA", "AGRUPAMENTO", "KM Rodado Real", "Consumo Real (L)", "Média KM/L", "Preço Médio Litro (R$)", "Fonte do Dado"]
    lista_combustiveis = ["DIESEL (L)", "DIESEL (R$)", "GNV (L)", "GNV (R$)", "ARLA 32 (R$)"]
    
    cols_finais = cols_principais + lista_combustiveis
    df_relatorio = df_final[cols_finais].sort_values(by="PLACA").round(2)
    
    # SALVANDO O EXCEL
    agora = datetime.now().strftime("%H%M%S")
    nome_arquivo = f"Relatorio_Fechamento_{ano}_{mes:02d}_{agora}.xlsx"
    
    with pd.ExcelWriter(nome_arquivo, engine="openpyxl") as writer:
        df_ticket_completo.sort_values(by=["PLACA", "Data", "Hora"]).to_excel(writer, sheet_name="Aba 1 - Ticketlog Bruto", index=False)
        df_relatorio.to_excel(writer, sheet_name="Aba 2 - Relatorio Real", index=False)
        
        ws_bruto = writer.sheets["Aba 1 - Ticketlog Bruto"]
        ws_real = writer.sheets["Aba 2 - Relatorio Real"]
        
        for row in ws_real.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    if cell.value == 0:
                        cell.value = 0.0
                    cell.number_format = '#,##0.00'
                    
        for row in ws_bruto.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    if cell.value == 0:
                        cell.value = 0.0
                    cell.number_format = '#,##0.00'

    return nome_arquivo

# =====================================================================
# 4. ENVIO DE E-MAIL (MÓDULO DE AUTOMAÇÃO)
# =====================================================================
def enviar_email(nome_arquivo, mes, ano):
    if not EMAIL_USER or not EMAIL_TO:
        print("\n⚠️ Credenciais de e-mail ausentes no GitHub Secrets. Pulando envio de e-mail.")
        return

    print(f"\n[4/4] ✉️ A enviar e-mail com o relatório de {mes:02d}/{ano}...")
    
    msg = EmailMessage()
    msg['Subject'] = f"Relatório de Fechamento de Frota (Automatizado) - {mes:02d}/{ano}"
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    msg.set_content(f"Olá,\n\nSegue em anexo o relatório automatizado de fechamento da frota processado pelo GitHub Actions referente ao mês {mes:02d}/{ano}.\n\nEste é um e-mail automático, por favor não responda.")

    try:
        with open(nome_arquivo, 'rb') as f:
            file_data = f.read()
            file_name = f.name
            
        msg.add_attachment(file_data, maintype='application', subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=file_name)
        
        # Conexão SMTP
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        
        print("✅ E-mail enviado com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao enviar e-mail: {e}")

# =====================================================================
# 5. EXECUÇÃO PRINCIPAL (CÁLCULO DINÂMICO PARA O GITHUB ACTIONS)
# =====================================================================
if __name__ == "__main__":
    
    # O robô pega a data atual e subtrai 5 dias. 
    # Isso garante que se ele rodar no dia 01 a 05 do mês, ele gere o relatório do mês passado.
    hoje = datetime.now() - timedelta(days=5)
    ano_alvo = hoje.year
    mes_alvo = hoje.month
    
    print(f"🚀 A INICIAR AUTOMAÇÃO NO GITHUB ACTIONS | Mês de Ref: {mes_alvo:02d}/{ano_alvo}")
    
    arquivo_gerado = processar_relatorio(ano_alvo, mes_alvo)
    
    if arquivo_gerado:
        print(f"\n🎉 Relatório gerado com sucesso: '{arquivo_gerado}'")
        enviar_email(arquivo_gerado, mes_alvo, ano_alvo)
