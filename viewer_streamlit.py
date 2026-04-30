"""
Viewer Streamlit para a base de leads de incorporadoras.

Como rodar:
    streamlit run viewer_streamlit.py

Pré-requisito: data/incorporadoras_enriquecido.parquet
(gerado por qualificar_leads.py)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"
PARQUET = DATA_DIR / "incorporadoras_enriquecido.parquet"
SOCIOS_CSV = DATA_DIR / "incorporadoras_socios.csv"

st.set_page_config(
    page_title="Leads — Incorporadoras",
    page_icon="🏗️",
    layout="wide",
)


def checar_senha() -> bool:
    """Protege o app com senha definida em st.secrets["password"].
    Em local sem secrets.toml, o app abre direto (sem auth)."""
    try:
        senha_correta = st.secrets["password"]
    except (KeyError, FileNotFoundError):
        return True  # sem secrets configurados = ambiente local livre

    if st.session_state.get("autenticado"):
        return True

    st.title("🏗️ Leads — Incorporadoras")
    senha = st.text_input("Senha de acesso", type="password")
    if senha == senha_correta:
        st.session_state["autenticado"] = True
        st.rerun()
    elif senha:
        st.error("Senha incorreta.")
    return False


if not checar_senha():
    st.stop()


@st.cache_data(show_spinner="Carregando base de leads...")
def carregar() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not PARQUET.exists():
        st.error(f"Parquet não encontrado: {PARQUET}. Rode qualificar_leads.py primeiro.")
        st.stop()
    df = pd.read_parquet(PARQUET)
    # Converter categóricos pra string (Streamlit/pandas3 às vezes não lida bem com Categorical)
    for col in ["bucket_capital", "bucket_idade", "regiao"]:
        if col in df.columns and str(df[col].dtype) == "category":
            df[col] = df[col].astype(str)
    # Garantir que CNPJ é string limpa
    if "cnpj" in df.columns:
        df["cnpj"] = df["cnpj"].fillna("").astype(str)

    if not SOCIOS_CSV.exists():
        soc = pd.DataFrame(columns=[
            "cnpj_basico", "tipo_socio", "nome_socio", "cpf_cnpj_socio",
            "qualificacao_socio", "data_entrada", "pais",
            "nome_representante", "qualificacao_representante", "faixa_etaria",
        ])
    else:
        soc = pd.read_csv(SOCIOS_CSV, dtype={"cnpj_basico": str, "cpf_cnpj_socio": str})
    return df, soc


try:
    df, socios = carregar()
except Exception as e:
    st.error(f"Erro ao carregar dados: {e}")
    st.exception(e)
    st.stop()

# ---------- Sidebar: filtros ----------
st.sidebar.header("🔍 Filtros")

score_min, score_max = st.sidebar.slider(
    "Lead Score", 0, 100, (50, 100), step=5
)

ufs_disponiveis = sorted(df["uf"].dropna().unique())
ufs = st.sidebar.multiselect("UF", ufs_disponiveis, default=[])

regioes = st.sidebar.multiselect(
    "Região",
    options=sorted(df["regiao"].dropna().unique()),
    default=[],
)

bucket_capital = st.sidebar.multiselect(
    "Porte (capital social)",
    options=["micro (<100k)", "pequena (100k-1M)", "media (1M-10M)", "grande (>10M)"],
    default=["media (1M-10M)", "grande (>10M)"],
)

bucket_idade = st.sidebar.multiselect(
    "Idade da empresa",
    options=["nascente (<2y)", "crescimento (2-7y)", "madura (7-15y)", "consolidada (>15y)"],
    default=[],
)

so_matriz = st.sidebar.checkbox("Apenas matriz", value=True)
so_com_email = st.sidebar.checkbox("Tem email válido", value=False)
so_email_corp = st.sidebar.checkbox("Email corporativo (não-gmail/hotmail)", value=False)
so_com_telefone = st.sidebar.checkbox("Tem telefone válido", value=False)
so_em_grupo = st.sidebar.checkbox("Pertence a grupo econômico", value=False)
ocultar_spe = st.sidebar.checkbox("Ocultar SPEs (heurística)", value=False)

busca_razao = st.sidebar.text_input("Buscar razão social/nome fantasia").strip().upper()
busca_cnpj = st.sidebar.text_input("Buscar CNPJ (qualquer parte)").strip()

# ---------- Aplicar filtros ----------
mask = (df["lead_score"] >= score_min) & (df["lead_score"] <= score_max)
if ufs:
    mask &= df["uf"].isin(ufs)
if regioes:
    mask &= df["regiao"].isin(regioes)
if bucket_capital:
    mask &= df["bucket_capital"].isin(bucket_capital)
if bucket_idade:
    mask &= df["bucket_idade"].isin(bucket_idade)
if so_matriz:
    mask &= df["eh_matriz"]
if so_com_email:
    mask &= df["tem_email_valido"]
if so_email_corp:
    mask &= df["email_dominio_proprio"]
if so_com_telefone:
    mask &= df["tem_telefone_valido"]
if so_em_grupo:
    mask &= df["nome_grupo_economico"].notna()
if ocultar_spe:
    mask &= ~df["parece_spe"]
if busca_razao:
    busca_mask = (
        df["razao_social"].fillna("").str.upper().str.contains(busca_razao, regex=False)
        | df["nome_fantasia"].fillna("").str.upper().str.contains(busca_razao, regex=False)
    )
    mask &= busca_mask
if busca_cnpj:
    mask &= df["cnpj"].fillna("").str.contains(busca_cnpj, regex=False)

filtrado = df[mask].sort_values("lead_score", ascending=False).reset_index(drop=True)

# ---------- Header + KPIs ----------
st.title("🏗️ Leads — Incorporadoras")
st.caption(f"Base: {len(df):,} estabelecimentos • Filtrado: {len(filtrado):,}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Leads filtrados", f"{len(filtrado):,}")
c2.metric("Score médio", f"{filtrado['lead_score'].mean():.1f}" if len(filtrado) else "—")
c3.metric(
    "Capital social total",
    f"R$ {filtrado['capital_social'].sum()/1e9:.2f}B" if len(filtrado) else "—",
)
c4.metric(
    "Capital médio",
    f"R$ {filtrado['capital_social'].mean()/1e6:.1f}M" if len(filtrado) else "—",
)
c5.metric("Grupos econômicos", filtrado["nome_grupo_economico"].nunique())

# ---------- Export ----------
csv_export = filtrado.to_csv(index=False).encode("utf-8")
st.download_button(
    "💾 Exportar CSV filtrado",
    data=csv_export,
    file_name=f"leads_filtrados_{len(filtrado)}.csv",
    mime="text/csv",
    disabled=len(filtrado) == 0,
)

# ---------- Tabela ----------
COLS_PRINCIPAIS = [
    "lead_score", "razao_social", "nome_fantasia", "cnpj",
    "capital_social", "bucket_capital", "idade_anos", "uf", "municipio",
    "telefone1", "email", "email_dominio_proprio",
    "qtd_socios", "nome_grupo_economico", "lead_score_motivos",
]
cols_existentes = [c for c in COLS_PRINCIPAIS if c in filtrado.columns]

st.subheader(f"📋 Resultados ({len(filtrado):,})")

PAGE_SIZE = 100
total_paginas = max(1, (len(filtrado) + PAGE_SIZE - 1) // PAGE_SIZE)
pagina = st.number_input(
    "Página", min_value=1, max_value=total_paginas, value=1, step=1
)
inicio = (pagina - 1) * PAGE_SIZE
fim = inicio + PAGE_SIZE
pagina_df = filtrado[cols_existentes].iloc[inicio:fim].copy()

# Formatação de capital
if "capital_social" in pagina_df.columns:
    pagina_df["capital_social"] = pagina_df["capital_social"].apply(
        lambda v: f"R$ {v:,.0f}" if pd.notna(v) else ""
    )

try:
    st.dataframe(
        pagina_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "lead_score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d"
            ),
        },
    )
except Exception:
    st.dataframe(pagina_df, use_container_width=True, hide_index=True)

# ---------- Detalhes da empresa ----------
st.subheader("🔎 Detalhes")
opcoes_cnpj = filtrado["cnpj"].head(500).tolist()
if opcoes_cnpj:
    cnpj_sel = st.selectbox(
        "Selecione um CNPJ (top 500 do filtro atual):",
        options=opcoes_cnpj,
        format_func=lambda c: (
            f"{c} — "
            f"{filtrado.loc[filtrado['cnpj']==c, 'razao_social'].iloc[0]}"
        ),
    )
    def _v(reg, k, default=""):
        """Acesso defensivo: retorna default se None/nan/missing."""
        if k not in reg:
            return default
        val = reg[k]
        try:
            if pd.isna(val):
                return default
        except (TypeError, ValueError):
            pass
        return val

    if cnpj_sel:
        registro = filtrado[filtrado["cnpj"] == cnpj_sel].iloc[0]
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"### {_v(registro, 'razao_social', '(sem razão social)')}")
            fantasia = _v(registro, "nome_fantasia")
            if fantasia:
                st.caption(str(fantasia))
            st.write(f"**CNPJ:** `{_v(registro, 'cnpj')}`")
            st.write(f"**Score:** {_v(registro, 'lead_score', 0)}")
            st.write(f"**Motivos:** {_v(registro, 'lead_score_motivos')}")
            cap = _v(registro, "capital_social", 0)
            st.write(f"**Capital social:** R$ {float(cap):,.2f}")
            idade = _v(registro, "idade_anos")
            if idade != "":
                st.write(f"**Idade:** {idade} anos")
            st.write(f"**Tipo:** {_v(registro, 'matriz_filial')}")
            grupo = _v(registro, "nome_grupo_economico")
            if grupo:
                st.write(f"**Grupo:** {grupo}")
                st.write(f"**Empresas no grupo:** {_v(registro, 'qtd_empresas_grupo')}")
        with col2:
            st.write(
                f"**Endereço:** {_v(registro, 'logradouro')}, {_v(registro, 'numero')}"
            )
            st.write(
                f"{_v(registro, 'bairro')} — "
                f"{_v(registro, 'municipio')}/{_v(registro, 'uf')}"
            )
            st.write(f"**CEP:** {_v(registro, 'cep')}")
            st.write(
                f"**Telefone:** ({_v(registro, 'ddd1')}) {_v(registro, 'telefone1')}"
            )
            st.write(f"**Email:** {_v(registro, 'email')}")
            st.write(f"**CNAE principal:** {_v(registro, 'cnae_principal')}")
            cnae_sec = _v(registro, "cnae_secundario")
            if cnae_sec:
                st.write(f"**CNAEs secundários:** {cnae_sec}")

        st.markdown("#### 👥 Quadro societário")
        cnpj_basico_sel = _v(registro, "cnpj_basico")
        socios_emp = socios[socios["cnpj_basico"] == cnpj_basico_sel] if cnpj_basico_sel else socios.iloc[0:0]
        if len(socios_emp):
            st.dataframe(
                socios_emp[
                    ["nome_socio", "tipo_socio", "qualificacao_socio",
                     "data_entrada", "faixa_etaria"]
                ],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("Nenhum sócio encontrado.")

        # Outras empresas do mesmo grupo
        nome_grupo = _v(registro, "nome_grupo_economico")
        if nome_grupo:
            st.markdown("#### 🕸️ Outras empresas do grupo")
            grupo = df[
                (df["nome_grupo_economico"] == nome_grupo)
                & (df["cnpj"] != _v(registro, "cnpj"))
            ].sort_values("capital_social", ascending=False)
            if len(grupo):
                st.dataframe(
                    grupo[["razao_social", "cnpj", "uf", "capital_social", "lead_score"]],
                    hide_index=True,
                    use_container_width=True,
                )
else:
    st.info("Aplique filtros para selecionar uma empresa.")
