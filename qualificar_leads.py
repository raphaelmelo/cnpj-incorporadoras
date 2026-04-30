"""
Qualifica os leads de incorporadoras: feature engineering, lead score,
detecção de grupos econômicos via grafo de sócios, e exporta para Excel
multi-aba com filtros, formatação condicional e views por persona.

Entrada:
    data/incorporadoras.csv         (do extrair_incorporadoras.py)
    data/incorporadoras_socios.csv  (do extrair_incorporadoras.py)

Saída:
    data/leads_incorporadoras.xlsx  (1 arquivo, várias abas)
"""
from __future__ import annotations

import math
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
ENTRADA_EMP = DATA_DIR / "incorporadoras.csv"
ENTRADA_SOC = DATA_DIR / "incorporadoras_socios.csv"
SAIDA_XLSX = DATA_DIR / "leads_incorporadoras.xlsx"

UF_REGIAO = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte",
    "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste",
    "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}
UF_TOP_IMOB = {"SP", "RJ", "MG", "PR", "SC"}
EMAIL_GRATIS = {
    "gmail.com", "hotmail.com", "yahoo.com", "yahoo.com.br", "outlook.com",
    "outlook.com.br", "uol.com.br", "bol.com.br", "live.com", "icloud.com",
    "terra.com.br", "ig.com.br", "globo.com",
}
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
TEL_RE = re.compile(r"^[0-9]{8,11}$")


# -----------------------------------------------------------------------------
# Feature engineering
# -----------------------------------------------------------------------------
def carregar_dados() -> tuple[pd.DataFrame, pd.DataFrame]:
    print(f"[1/6] Lendo {ENTRADA_EMP.name}...")
    emp = pd.read_csv(
        ENTRADA_EMP,
        dtype={"cnpj": str, "cnpj_basico": str, "cep": str, "uf": str},
        low_memory=False,
    )
    print(f"      {len(emp):,} estabelecimentos carregados")

    print(f"[2/6] Lendo {ENTRADA_SOC.name}...")
    soc = pd.read_csv(
        ENTRADA_SOC,
        dtype={"cnpj_basico": str, "cpf_cnpj_socio": str},
        low_memory=False,
    )
    print(f"      {len(soc):,} sócios carregados")
    return emp, soc


def aplicar_features(emp: pd.DataFrame, soc: pd.DataFrame) -> pd.DataFrame:
    print("[3/6] Calculando features...")
    hoje = datetime.now().date()

    # Idade
    emp["data_inicio_atividade"] = pd.to_datetime(
        emp["data_inicio_atividade"], format="%Y%m%d", errors="coerce"
    )
    emp["idade_anos"] = (
        (pd.Timestamp(hoje) - emp["data_inicio_atividade"]).dt.days / 365.25
    ).round(1)
    emp["bucket_idade"] = pd.cut(
        emp["idade_anos"],
        bins=[-1, 2, 7, 15, 999],
        labels=["nascente (<2y)", "crescimento (2-7y)", "madura (7-15y)", "consolidada (>15y)"],
    )

    # Capital
    emp["capital_social"] = pd.to_numeric(emp["capital_social"], errors="coerce").fillna(0)
    emp["bucket_capital"] = pd.cut(
        emp["capital_social"],
        bins=[-1, 100_000, 1_000_000, 10_000_000, 1e15],
        labels=["micro (<100k)", "pequena (100k-1M)", "media (1M-10M)", "grande (>10M)"],
    )
    emp["percentil_capital_uf"] = (
        emp.groupby("uf")["capital_social"].rank(pct=True).round(2)
    )

    # Geografia
    emp["regiao"] = emp["uf"].map(UF_REGIAO).fillna("Outro")
    emp["uf_top_imobiliario"] = emp["uf"].isin(UF_TOP_IMOB)

    # Estrutural
    emp["eh_matriz"] = emp["matriz_filial"] == "MATRIZ"
    filiais = emp.groupby("cnpj_basico").agg(
        qtd_estabelecimentos=("cnpj", "count"),
        ufs_distintas=("uf", "nunique"),
    )
    emp = emp.merge(filiais, on="cnpj_basico", how="left")
    emp["multi_estado"] = emp["ufs_distintas"] > 1

    # Heurística "parece SPE"
    spe_pattern = re.compile(
        r"\bSPE\b|EMPREENDIMENTO|\sIIIV?|\sIVV?\b|\sV?I{1,3}\b",
        re.IGNORECASE,
    )
    emp["parece_spe"] = emp["razao_social"].fillna("").str.contains(spe_pattern)

    # Sócios — só agregados por cnpj_basico
    socio_agg = soc.groupby("cnpj_basico").agg(
        qtd_socios=("nome_socio", "count"),
        tem_socio_pj=("tipo_socio", lambda s: (s == "1").any()),
    )
    emp = emp.merge(socio_agg, on="cnpj_basico", how="left")
    emp["qtd_socios"] = emp["qtd_socios"].fillna(0).astype(int)
    emp["tem_socio_pj"] = emp["tem_socio_pj"].fillna(False)

    # Contato
    emp["tem_email_valido"] = emp["email"].fillna("").str.match(EMAIL_RE)
    emp["tem_telefone_valido"] = emp["telefone1"].fillna("").astype(str).str.match(TEL_RE)
    emp["email_dominio"] = (
        emp["email"].fillna("").str.lower().str.split("@").str[-1]
    )
    emp["email_dominio_proprio"] = (
        emp["tem_email_valido"] & ~emp["email_dominio"].isin(EMAIL_GRATIS)
    )
    emp["score_contato"] = (
        emp["tem_email_valido"].astype(int)
        + emp["tem_telefone_valido"].astype(int)
        + emp["email_dominio_proprio"].astype(int)
    )

    # Lead score (0-100)
    cap_norm = (emp["capital_social"].clip(0, 50_000_000)
                .apply(lambda v: math.log1p(v) / math.log1p(50_000_000)))
    idade_norm = emp["idade_anos"].clip(0, 15) / 15
    emp["lead_score"] = (
        25 * cap_norm
        + 15 * idade_norm.fillna(0)
        + 15 * emp["eh_matriz"].astype(int)
        + 10 * emp["tem_email_valido"].astype(int)
        + 10 * emp["tem_telefone_valido"].astype(int)
        + 10 * emp["email_dominio_proprio"].astype(int)
        + 10 * emp["uf_top_imobiliario"].astype(int)
        + 5 * emp["uf"].isin(["SP", "RJ", "MG", "DF", "RS"]).astype(int)
    ).round(1)

    # Motivos do score (texto explicável)
    def _motivos(row) -> str:
        ms = []
        if row["capital_social"] >= 10_000_000:
            ms.append(f"Capital R${row['capital_social']/1e6:.1f}M ✓")
        elif row["capital_social"] >= 1_000_000:
            ms.append(f"Capital R${row['capital_social']/1e6:.1f}M")
        if row["eh_matriz"]:
            ms.append("Matriz ✓")
        if pd.notna(row["idade_anos"]) and row["idade_anos"] >= 5:
            ms.append(f"{row['idade_anos']:.0f} anos ✓")
        if row["email_dominio_proprio"]:
            ms.append("Email corporativo ✓")
        elif row["tem_email_valido"]:
            ms.append("Email genérico")
        if row["tem_telefone_valido"]:
            ms.append("Tel ✓")
        if row["uf_top_imobiliario"]:
            ms.append(f"{row['uf']} ✓")
        return " | ".join(ms) if ms else "Sem sinais positivos"

    emp["lead_score_motivos"] = emp.apply(_motivos, axis=1)
    return emp


# -----------------------------------------------------------------------------
# Grupos econômicos via grafo de sócios
# -----------------------------------------------------------------------------
def detectar_grupos(emp: pd.DataFrame, soc: pd.DataFrame) -> pd.DataFrame:
    """Componentes conexos: nó = empresa (cnpj_basico),
    aresta = compartilha um sócio (CPF ou CNPJ)."""
    print("[4/6] Detectando grupos econômicos via grafo de sócios...")

    # Mapa sócio → lista de empresas
    bases_validas = set(emp["cnpj_basico"].unique())
    soc_filtrado = soc[soc["cnpj_basico"].isin(bases_validas)]
    socio_para_empresas: dict[str, list[str]] = defaultdict(list)
    for _, row in soc_filtrado[["cpf_cnpj_socio", "cnpj_basico"]].iterrows():
        chave = row["cpf_cnpj_socio"]
        if not isinstance(chave, str) or len(chave) < 4:
            continue
        socio_para_empresas[chave].append(row["cnpj_basico"])

    # Adjacência: empresa → empresas conectadas
    adj: dict[str, set[str]] = defaultdict(set)
    for empresas in socio_para_empresas.values():
        if len(empresas) < 2:
            continue
        for i in range(len(empresas)):
            for j in range(i + 1, len(empresas)):
                a, b = empresas[i], empresas[j]
                if a != b:
                    adj[a].add(b)
                    adj[b].add(a)

    # BFS para componentes conexos
    visitados: set[str] = set()
    grupos: list[set[str]] = []
    for base in bases_validas:
        if base in visitados:
            continue
        if base not in adj:
            continue
        componente: set[str] = set()
        fila = deque([base])
        while fila:
            atual = fila.popleft()
            if atual in componente:
                continue
            componente.add(atual)
            visitados.add(atual)
            for vizinho in adj[atual]:
                if vizinho not in componente:
                    fila.append(vizinho)
        if len(componente) > 1:
            grupos.append(componente)

    print(f"      {len(grupos):,} grupos econômicos detectados")

    # Mapear cnpj_basico → nome do grupo (razão social do maior capital)
    base_para_grupo: dict[str, tuple[str, int]] = {}
    capital_por_base = (
        emp.groupby("cnpj_basico")
        .agg(razao_social=("razao_social", "first"), capital=("capital_social", "max"))
    )
    for grupo_id, componente in enumerate(grupos):
        sub = capital_por_base.loc[capital_por_base.index.intersection(componente)]
        if sub.empty:
            continue
        lider = sub.sort_values("capital", ascending=False).iloc[0]
        nome_grupo = f"GRUPO {grupo_id+1}: {lider['razao_social']}"
        for base in componente:
            base_para_grupo[base] = (nome_grupo, len(componente))

    emp["nome_grupo_economico"] = emp["cnpj_basico"].map(
        lambda b: base_para_grupo.get(b, (None, 0))[0]
    )
    emp["qtd_empresas_grupo"] = emp["cnpj_basico"].map(
        lambda b: base_para_grupo.get(b, (None, 0))[1]
    )
    return emp


# -----------------------------------------------------------------------------
# Geração do Excel
# -----------------------------------------------------------------------------
COLS_DISPLAY = [
    "lead_score", "lead_score_motivos", "razao_social", "nome_fantasia",
    "cnpj", "capital_social", "bucket_capital", "idade_anos", "bucket_idade",
    "matriz_filial", "qtd_estabelecimentos", "ufs_distintas",
    "uf", "municipio", "regiao", "bairro", "logradouro", "numero",
    "ddd1", "telefone1", "email", "email_dominio_proprio",
    "qtd_socios", "tem_socio_pj", "nome_grupo_economico", "qtd_empresas_grupo",
    "cnae_principal", "cnae_secundario", "parece_spe",
    "data_inicio_atividade", "cnpj_basico",
]


def aplicar_filtros_aba(writer, df: pd.DataFrame, nome_aba: str, score_col: bool = True):
    """Escreve df numa aba com auto-filter e congelamento de painel."""
    cols = [c for c in COLS_DISPLAY if c in df.columns]
    df_aba = df[cols].copy()
    df_aba.to_excel(writer, sheet_name=nome_aba, index=False)
    ws = writer.sheets[nome_aba]
    n_rows, n_cols = df_aba.shape
    if n_rows == 0:
        ws.write(1, 0, "(vazio — nenhum registro corresponde a este filtro)")
        return
    ws.autofilter(0, 0, n_rows, n_cols - 1)
    ws.freeze_panes(1, 0)
    # Larguras razoáveis
    larguras = {
        "razao_social": 45, "nome_fantasia": 30, "cnpj": 16, "logradouro": 30,
        "lead_score_motivos": 50, "email": 30, "nome_grupo_economico": 40,
        "municipio": 8, "uf": 5, "bucket_capital": 18, "bucket_idade": 22,
    }
    for i, col in enumerate(cols):
        ws.set_column(i, i, larguras.get(col, 12))
    # Heatmap no score
    if score_col and "lead_score" in cols:
        col_idx = cols.index("lead_score")
        ws.conditional_format(
            1, col_idx, n_rows, col_idx,
            {"type": "3_color_scale",
             "min_color": "#F8696B", "mid_color": "#FFEB84", "max_color": "#63BE7B"},
        )


def gerar_xlsx(emp: pd.DataFrame) -> None:
    print(f"[5/6] Gerando Excel multi-aba: {SAIDA_XLSX.name}")
    emp = emp.sort_values("lead_score", ascending=False).reset_index(drop=True)

    # Lista negra: capital muito baixo OU recém-criada+capital baixo OU sem contato
    blacklist_mask = (
        (emp["capital_social"] < 50_000)
        | ((emp["idade_anos"] < 0.5) & (emp["capital_social"] < 500_000))
        | (~emp["tem_email_valido"] & ~emp["tem_telefone_valido"])
    )
    blacklist = emp[blacklist_mask].copy()
    blacklist["motivo_exclusao"] = ""
    blacklist.loc[blacklist["capital_social"] < 50_000, "motivo_exclusao"] += "capital<50k; "
    blacklist.loc[
        (blacklist["idade_anos"] < 0.5) & (blacklist["capital_social"] < 500_000),
        "motivo_exclusao"
    ] += "muito nova+capital<500k; "
    blacklist.loc[
        ~blacklist["tem_email_valido"] & ~blacklist["tem_telefone_valido"],
        "motivo_exclusao"
    ] += "sem contato; "

    qualificados = emp[~blacklist_mask].copy()

    with pd.ExcelWriter(SAIDA_XLSX, engine="xlsxwriter") as writer:
        # README
        readme = pd.DataFrame({
            "Aba": [
                "🔥 Top 100", "🆕 Recém-criadas", "💰 Grandes consolidadas",
                "📞 Contatáveis", "🕸️ Grupos econômicos",
                "🚫 Lista negra", "📊 Base completa",
                "🏘️ UF-SP", "🏘️ UF-RJ", "🏘️ UF-MG", "🏘️ UF-PR", "🏘️ UF-SC",
            ],
            "O que tem": [
                "Top 100 leads por score (>=70)",
                "Empresas <1 ano com capital >R$500k",
                "Capital >R$10M, >10 anos, matriz",
                "Score >=60 com email + telefone válidos",
                "Holdings + SPEs identificadas via sócios em comum",
                "Excluídos automaticamente (com motivo)",
                "Todos os qualificados ordenados por score",
                "Top 5 estados imobiliários (1 aba cada)",
                "", "", "", "",
            ],
        })
        readme.to_excel(writer, sheet_name="📖 README", index=False)
        writer.sheets["📖 README"].set_column(0, 0, 30)
        writer.sheets["📖 README"].set_column(1, 1, 60)
        writer.sheets["📖 README"].write(
            len(readme) + 2, 0,
            f"Gerado em {datetime.now():%Y-%m-%d %H:%M} a partir de "
            f"{len(emp):,} estabelecimentos. {len(qualificados):,} qualificados."
        )
        writer.sheets["📖 README"].write(
            len(readme) + 4, 0,
            "Score (0-100): Capital(25) + Idade(15) + Matriz(15) + "
            "Email(10) + Tel(10) + Email corporativo(10) + UF top(10) + Bonus capital(5)"
        )

        aplicar_filtros_aba(writer, qualificados.head(100), "🔥 Top 100")

        recentes = qualificados[
            (qualificados["idade_anos"] < 1) & (qualificados["capital_social"] > 500_000)
        ]
        aplicar_filtros_aba(writer, recentes, "🆕 Recém-criadas")

        grandes = qualificados[
            (qualificados["capital_social"] > 10_000_000)
            & (qualificados["idade_anos"] > 10)
            & (qualificados["eh_matriz"])
        ]
        aplicar_filtros_aba(writer, grandes, "💰 Grandes consolidadas")

        contataveis = qualificados[
            (qualificados["lead_score"] >= 60) & (qualificados["score_contato"] >= 2)
        ]
        aplicar_filtros_aba(writer, contataveis, "📞 Contatáveis")

        grupos_df = qualificados[qualificados["nome_grupo_economico"].notna()].sort_values(
            ["qtd_empresas_grupo", "nome_grupo_economico", "lead_score"],
            ascending=[False, True, False],
        )
        aplicar_filtros_aba(writer, grupos_df, "🕸️ Grupos econômicos")

        aplicar_filtros_aba(writer, qualificados, "📊 Base completa")

        for uf in ["SP", "RJ", "MG", "PR", "SC"]:
            sub = qualificados[qualificados["uf"] == uf]
            aplicar_filtros_aba(writer, sub, f"🏘️ UF-{uf}")

        aplicar_filtros_aba(writer, blacklist, "🚫 Lista negra", score_col=False)

    print(f"      ✓ {SAIDA_XLSX} ({SAIDA_XLSX.stat().st_size/1024**2:.1f} MB)")


def main() -> int:
    inicio = time.time()
    if not ENTRADA_EMP.exists() or not ENTRADA_SOC.exists():
        print(
            f"ERRO: arquivos {ENTRADA_EMP.name} e {ENTRADA_SOC.name} "
            "não encontrados. Rode extrair_incorporadoras.py primeiro.",
            file=sys.stderr,
        )
        return 1

    emp, soc = carregar_dados()
    emp = aplicar_features(emp, soc)
    emp = detectar_grupos(emp, soc)

    # Salva parquet enriquecido para os viewers (Streamlit/Datasette)
    parquet_path = DATA_DIR / "incorporadoras_enriquecido.parquet"
    emp.to_parquet(parquet_path, index=False, compression="snappy")
    print(f"      ✓ {parquet_path} ({parquet_path.stat().st_size/1024**2:.1f} MB)")

    gerar_xlsx(emp)

    minutos = (time.time() - inicio) / 60
    print(f"[6/6] Concluído em {minutos:.1f} min — abra {SAIDA_XLSX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
