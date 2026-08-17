import os
import time
import requests
import psycopg2
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv(override=True)

# =====================================================================
# 1. CREDENCIAIS E CONFIGURAÇÕES
# =====================================================================
TOKEN_GOBRAX = os.getenv("TOKEN_GOBRAX")
URL_GOBRAX = "https://gateway-v3.gobrax.com.br:8889/api/v1/vehicle-statistics"

# Banco do Sistema / Empresa (Origem das placas)
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Banco Supabase (Destino) - Com Fallback Automático caso o Secret venha vazio
SUPA_HOST = os.getenv("DB_HOST_SUPA") or "aws-0-sa-east-1.pooler.supabase.com"
SUPA_PORT = os.getenv("DB_PORT_SUPA") or "6543"
SUPA_NAME = os.getenv("DB_NAME_SUPA") or "postgres"
SUPA_USER = os.getenv("DB_USER_SUPA") or "postgres.ndnwtrnjclsbihvthdrg"
SUPA_PASSWORD = os.getenv("DB_PASSWORD_SUPA") or "35XLBG0ReOAUVjin"

MAX_WORKERS = 10

# =====================================================================
# 2. CONSULTA AO BANCO DA EMPRESA (PLACAS ATIVAS)
# =====================================================================
def buscar_placas_ativas_por_historico(data_fim_str):
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    
    sql_placas = """
    SELECT DISTINCT ON (v."PLACA")
        v."PLACA" AS "Placa",
        vm."MARCA" AS "Marca",
        vm1."MODELO" AS "Modelo",
        vma."MODALIDADE" AS "Frota",
        va2."DESCRICAO" AS "Grupo_de_Veiculos",
        vs."SITUACAO" AS "Situacao",
        vhs."DATA" AS "DATA_DA_SITUACAO"
    FROM veiculo.veiculo v
    JOIN veiculo.veiculo_marca vm ON vm."MARCA_ID" = v."MARCA_ID" 
    JOIN veiculo.veiculo_modelo vm1 ON vm1."MODELO_ID" = v."MODELO_ID" 
    JOIN veiculo.veiculo_modalidade_atual vma ON v."PLACA" = vma."PLACA"
    JOIN veiculo.veiculo_tipo_carroceria vtc ON vtc."TIPO_CARROCERIA_ID" = v."TIPO_CARROCERIA_ID" 
    JOIN veiculo.veiculo_hist_situacao vhs ON vhs."PLACA" = v."PLACA" 
    JOIN veiculo.veiculo_situacao vs ON vs."SITUACAO_ID" = vhs."SITUACAO_ID"
    JOIN veiculo.veiculo_agrupamento va2 ON va2."AGRUPAMENTO_ID" = vtc."AGRUPAMENTO_ID"
    WHERE vma."MODALIDADE" = 'FROTA'
      AND v."PLACA" IS NOT NULL 
      AND TRIM(v."PLACA") <> ''
      AND va2."DESCRICAO" NOT LIKE '%%SEMI REBOQUE%%' 
      AND va2."DESCRICAO" NOT LIKE '%%SEMI-REBOQUE%%'
      AND va2."DESCRICAO" NOT LIKE '%%REBOQUE%%'
      AND vhs."DATA" <= %s
    ORDER BY 
        v."PLACA", 
        vhs."DATA" DESC;
    """
    
    df_placas = pd.read_sql_query(sql_placas, conn, params=(f"{data_fim_str} 23:59:59",))
    conn.close()

    df_ativas = df_placas[df_placas["Situacao"].str.upper().str.contains("ATIVO", na=False)].copy()
    df_ativas["Tração"] = "NÃO INFORMADO"

    return df_ativas

# =====================================================================
# 3. EXTRAÇÃO VIA API GOBRAX (MÊS VIGENTE)
# =====================================================================
def consultar_placa_gobrax(row_veiculo, data_inicio_str, data_fim_str):
    placa = row_veiculo.Placa
    headers = {"Authorization": f"Bearer {TOKEN_GOBRAX}", "Accept": "application/json"}
    
    params = {
        "startDate": data_inicio_str,
        "endDate": data_fim_str,
        "vehicleIdentification": placa,
        "groupBy": "DAY"
    }
    
    linhas = []
    for _ in range(3):
        try:
            r = requests.get(URL_GOBRAX, headers=headers, params=params, timeout=30)
            if r.status_code == 200:
                res_json = r.json()
                records = res_json.get("records") or res_json.get("data") or (res_json if isinstance(res_json, list) else [])
                
                if records:
                    for item in records:
                        data_item = item.get("date") or item.get("startDate") or item.get("period") or item.get("data")
                        if not data_item:
                            continue
                        
                        dt_obj = datetime.strptime(str(data_item)[:10], "%Y-%m-%d")

                        raw_km = item.get("totalMileage") or item.get("mileage") or item.get("distance") or item.get("kmTotal") or 0
                        km_total = float(raw_km)
                        if km_total > 100000:
                            km_total = km_total / 1000.0

                        consumo_total = float(item.get("totalConsumption") or item.get("consumption") or item.get("fuelConsumption") or 0)
                        
                        linhas.append({
                            "origem": "GOBRAX",
                            "ano": dt_obj.year,
                            "mes": dt_obj.month,
                            "data_registro": dt_obj.strftime("%Y-%m-%d"),
                            "frota": row_veiculo.Frota,
                            "placa": placa,
                            "marca": row_veiculo.Marca,
                            "modelo": row_veiculo.Modelo,
                            "tracao": row_veiculo.Tração,
                            "grupo_veiculos": row_veiculo.Grupo_de_Veiculos,
                            "nota_geral": float(item.get("generalScore") or item.get("score") or 0),
                            "km_total": km_total,
                            "velocidade_media": float(item.get("averageSpeed") or item.get("avgSpeed") or 0),
                            "consumo_total": consumo_total,
                            "media_computador_bordo": float(item.get("boardComputerAverage") or item.get("averageConsumption") or 0),
                            "odometro": float(item.get("odometer") or item.get("endOdometer") or 0)
                        })
                    return linhas
                break
        except Exception:
            time.sleep(1)
            
    return []

# =====================================================================
# 4. GRAVAÇÃO NO SUPABASE (UPSERT)
# =====================================================================
def salvar_no_supabase(registros):
    if not registros:
        print("⚠️ Nenhum registro útil capturado para salvar no Supabase.")
        return

    conn = psycopg2.connect(
        host=SUPA_HOST, port=SUPA_PORT, database=SUPA_NAME, user=SUPA_USER, password=SUPA_PASSWORD
    )
    cursor = conn.cursor()

    sql_insert = """
    INSERT INTO public.historico_telemetria_consolidado (
        origem, ano, mes, data_registro, frota, placa, marca, modelo, tracao, 
        grupo_veiculos, nota_geral, km_total, velocidade_media, 
        consumo_total, media_computador_bordo, odometro
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (placa, data_registro) DO UPDATE SET
        origem = EXCLUDED.origem,
        marca = EXCLUDED.marca,
        modelo = EXCLUDED.modelo,
        nota_geral = EXCLUDED.nota_geral,
        km_total = EXCLUDED.km_total,
        velocidade_media = EXCLUDED.velocidade_media,
        consumo_total = EXCLUDED.consumo_total,
        media_computador_bordo = EXCLUDED.media_computador_bordo,
        odometro = EXCLUDED.odometro;
    """

    dados_tupla = [
        (
            r["origem"], r["ano"], r["mes"], r["data_registro"], r["frota"], r["placa"],
            r["marca"], r["modelo"], r["tracao"], r["grupo_veiculos"],
            r["nota_geral"], r["km_total"], r["velocidade_media"],
            r["consumo_total"], r["media_computador_bordo"], r["odometro"]
        )
        for r in registros
    ]

    tamanho_lote = 1000
    for i in range(0, len(dados_tupla), tamanho_lote):
        lote = dados_tupla[i:i + tamanho_lote]
        cursor.executemany(sql_insert, lote)
        conn.commit()

    cursor.close()
    conn.close()

    print(f"✅ {len(registros)} registros salvos/atualizados com sucesso no Supabase!")

# =====================================================================
# 5. EXECUÇÃO PRINCIPAL
# =====================================================================
def executar_rotina_automatica():
    hoje = datetime.now()
    ontem = hoje - timedelta(days=1)
    
    primeiro_dia_mes_atual = ontem.replace(day=1)
    
    data_inicio_str = primeiro_dia_mes_atual.strftime("%Y-%m-%d 00:00:00")
    data_fim_str = ontem.strftime("%Y-%m-%d 23:59:59")
    data_fim_apenas = ontem.strftime("%Y-%m-%d")

    print(f"🚀 SINCRONIZANDO MÊS ATUAL ({primeiro_dia_mes_atual.strftime('%d/%m/%Y')} até {ontem.strftime('%d/%m/%Y')})")

    df_placas = buscar_placas_ativas_por_historico(data_fim_apenas)
    print(f"📌 {len(df_placas)} veículos ATIVOS encontrados.")

    if df_placas.empty:
        print("⚠️ Nenhum veículo ativo retornado.")
        return

    registros = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(consultar_placa_gobrax, row, data_inicio_str, data_fim_str)
            for row in df_placas.itertuples(index=False)
        ]
        for f in tqdm(as_completed(futures), total=len(futures), desc="Sincronizando Gobrax"):
            res_list = f.result()
            for res in res_list:
                if (res["km_total"] and res["km_total"] > 0) or (res["consumo_total"] and res["consumo_total"] > 0):
                    registros.append(res)

    salvar_no_supabase(registros)
    print("🎉 PROCESSAMENTO DO MÊS VIGENTE CONCLUÍDO COM SUCESSO!\n")

if __name__ == "__main__":
    executar_rotina_automatica()
