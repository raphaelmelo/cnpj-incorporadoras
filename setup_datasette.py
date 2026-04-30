"""
Cria o SQLite que o Datasette serve, com índices nas colunas mais filtradas.

Como rodar:
    python setup_datasette.py
    datasette serve data/incorporadoras.db --metadata data/datasette_metadata.json -o

Pré-requisito: data/incorporadoras_enriquecido.parquet (gerado por qualificar_leads.py)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
PARQUET = DATA_DIR / "incorporadoras_enriquecido.parquet"
SOCIOS_CSV = DATA_DIR / "incorporadoras_socios.csv"
DB = DATA_DIR / "incorporadoras.db"
METADATA = DATA_DIR / "datasette_metadata.json"


def main() -> int:
    if not PARQUET.exists():
        print(
            f"ERRO: {PARQUET} não encontrado. Rode qualificar_leads.py primeiro.",
            file=sys.stderr,
        )
        return 1

    inicio = time.time()
    print(f"[1/4] Lendo {PARQUET.name}...")
    emp = pd.read_parquet(PARQUET)

    # Converte tipos para serem amigáveis ao SQLite
    if "data_inicio_atividade" in emp.columns:
        emp["data_inicio_atividade"] = emp["data_inicio_atividade"].dt.strftime("%Y-%m-%d")
    for col in ["bucket_capital", "bucket_idade", "regiao"]:
        if col in emp.columns:
            emp[col] = emp[col].astype(str)

    print(f"[2/4] Lendo {SOCIOS_CSV.name}...")
    soc = pd.read_csv(SOCIOS_CSV, dtype=str)

    if DB.exists():
        DB.unlink()
    print(f"[3/4] Escrevendo {DB.name}...")
    with sqlite3.connect(DB) as con:
        emp.to_sql("incorporadoras", con, index=False, if_exists="replace")
        soc.to_sql("socios", con, index=False, if_exists="replace")

        print("       Criando índices...")
        idx_emp = [
            ("idx_emp_cnpj_basico", "incorporadoras(cnpj_basico)"),
            ("idx_emp_cnpj", "incorporadoras(cnpj)"),
            ("idx_emp_uf", "incorporadoras(uf)"),
            ("idx_emp_score", "incorporadoras(lead_score)"),
            ("idx_emp_capital", "incorporadoras(capital_social)"),
            ("idx_emp_municipio", "incorporadoras(municipio)"),
            ("idx_emp_grupo", "incorporadoras(nome_grupo_economico)"),
        ]
        idx_soc = [
            ("idx_soc_cnpj_basico", "socios(cnpj_basico)"),
            ("idx_soc_cpf_cnpj", "socios(cpf_cnpj_socio)"),
        ]
        for nome, target in idx_emp + idx_soc:
            con.execute(f"CREATE INDEX IF NOT EXISTS {nome} ON {target}")
        con.execute("ANALYZE")

    print(f"[4/4] Gerando {METADATA.name} com queries salvas...")
    metadata = {
        "title": "Incorporadoras — Leads",
        "description_html": (
            "Base de incorporadoras (CNAE 4110-7/00) extraída dos dados abertos "
            "da Receita Federal e enriquecida com lead score, features de "
            "porte/idade/contato, e detecção de grupos econômicos via grafo de sócios."
        ),
        "databases": {
            "incorporadoras": {
                "tables": {
                    "incorporadoras": {
                        "label_column": "razao_social",
                        "sortable_columns": [
                            "lead_score", "capital_social", "idade_anos",
                            "qtd_estabelecimentos",
                        ],
                        "facets": [
                            "uf", "regiao", "bucket_capital", "bucket_idade",
                            "matriz_filial",
                        ],
                    },
                    "socios": {
                        "label_column": "nome_socio",
                        "facets": ["qualificacao_socio", "tipo_socio"],
                    },
                },
                "queries": {
                    "top_100_score": {
                        "title": "🔥 Top 100 leads por score",
                        "sql": (
                            "SELECT lead_score, razao_social, cnpj, "
                            "capital_social, uf, municipio, telefone1, email, "
                            "lead_score_motivos "
                            "FROM incorporadoras "
                            "ORDER BY lead_score DESC LIMIT 100"
                        ),
                    },
                    "grandes_consolidadas": {
                        "title": "💰 Grandes consolidadas (>10M, >10y, matriz)",
                        "sql": (
                            "SELECT razao_social, cnpj, capital_social, "
                            "idade_anos, uf, municipio, telefone1, email "
                            "FROM incorporadoras "
                            "WHERE capital_social > 10000000 "
                            "  AND idade_anos > 10 "
                            "  AND eh_matriz = 1 "
                            "ORDER BY capital_social DESC"
                        ),
                    },
                    "recem_criadas": {
                        "title": "🆕 Recém-criadas (<1 ano, capital >500k)",
                        "sql": (
                            "SELECT razao_social, cnpj, capital_social, "
                            "idade_anos, uf, municipio, telefone1, email "
                            "FROM incorporadoras "
                            "WHERE idade_anos < 1 "
                            "  AND capital_social > 500000 "
                            "ORDER BY capital_social DESC"
                        ),
                    },
                    "contataveis": {
                        "title": "📞 Contatáveis (score>=60, com email+tel)",
                        "sql": (
                            "SELECT lead_score, razao_social, cnpj, "
                            "capital_social, uf, municipio, telefone1, email, "
                            "lead_score_motivos "
                            "FROM incorporadoras "
                            "WHERE lead_score >= 60 "
                            "  AND tem_email_valido = 1 "
                            "  AND tem_telefone_valido = 1 "
                            "ORDER BY lead_score DESC"
                        ),
                    },
                    "grupos_economicos": {
                        "title": "🕸️ Grupos econômicos (>=3 empresas)",
                        "sql": (
                            "SELECT nome_grupo_economico, qtd_empresas_grupo, "
                            "razao_social, cnpj, uf, capital_social, lead_score "
                            "FROM incorporadoras "
                            "WHERE qtd_empresas_grupo >= 3 "
                            "ORDER BY qtd_empresas_grupo DESC, "
                            "         nome_grupo_economico, lead_score DESC"
                        ),
                    },
                    "ranking_por_uf": {
                        "title": "📊 Ranking de capital por UF",
                        "sql": (
                            "SELECT uf, COUNT(*) AS qtd_empresas, "
                            "ROUND(SUM(capital_social)/1e9, 2) AS capital_total_bi, "
                            "ROUND(AVG(capital_social)/1e6, 2) AS capital_medio_mi, "
                            "ROUND(AVG(lead_score), 1) AS score_medio "
                            "FROM incorporadoras "
                            "GROUP BY uf ORDER BY capital_total_bi DESC"
                        ),
                    },
                    "buscar_por_socio": {
                        "title": "🔍 Buscar empresas por nome de sócio",
                        "sql": (
                            "SELECT i.razao_social, i.cnpj, i.uf, "
                            "i.capital_social, i.lead_score, "
                            "s.nome_socio, s.qualificacao_socio "
                            "FROM incorporadoras i "
                            "JOIN socios s ON s.cnpj_basico = i.cnpj_basico "
                            "WHERE s.nome_socio LIKE '%' || :nome || '%' "
                            "ORDER BY i.lead_score DESC LIMIT 100"
                        ),
                    },
                },
            }
        },
    }
    METADATA.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

    minutos = (time.time() - inicio) / 60
    print(f"\n✅ Pronto em {minutos:.1f} min")
    print(f"\nPara servir localmente:")
    print(
        f"   datasette serve {DB} --metadata {METADATA} -o"
    )
    print(f"\nDeploy no Fly.io (pra equipe acessar):")
    print(f"   datasette publish fly {DB} -m {METADATA} --app cnpj-leads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
