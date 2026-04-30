"""
Viewer Streamlit para a base de leads de incorporadoras.

Como rodar:
    streamlit run viewer_streamlit.py

Pré-requisito: data/incorporadoras_enriquecido.parquet
(gerado por qualificar_leads.py)
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from urllib.parse import quote_plus

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
        return True

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
    for col in ["bucket_capital", "bucket_idade", "regiao"]:
        if col in df.columns and str(df[col].dtype) == "category":
            df[col] = df[col].astype(str)
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


# ---------- Definição das views ----------
def view_top_100(d: pd.DataFrame) -> pd.DataFrame:
    return d.sort_values("lead_score", ascending=False).head(100)


def view_recem(d: pd.DataFrame) -> pd.DataFrame:
    return d[(d["idade_anos"] < 1) & (d["capital_social"] > 500_000)].sort_values(
        "capital_social", ascending=False
    )


def view_grandes(d: pd.DataFrame) -> pd.DataFrame:
    return d[
        (d["capital_social"] > 10_000_000)
        & (d["idade_anos"] > 10)
        & (d["eh_matriz"])
    ].sort_values("capital_social", ascending=False)


def view_contataveis(d: pd.DataFrame) -> pd.DataFrame:
    return d[
        (d["lead_score"] >= 60)
        & (d["tem_email_valido"])
        & (d["tem_telefone_valido"])
    ].sort_values("lead_score", ascending=False)


def view_grupos(d: pd.DataFrame) -> pd.DataFrame:
    return d[d["nome_grupo_economico"].notna()].sort_values(
        ["qtd_empresas_grupo", "nome_grupo_economico", "lead_score"],
        ascending=[False, True, False],
    )


def view_uf(d: pd.DataFrame, uf: str) -> pd.DataFrame:
    return d[d["uf"] == uf].sort_values("lead_score", ascending=False)


def view_blacklist(d: pd.DataFrame) -> pd.DataFrame:
    mask = (
        (d["capital_social"] < 50_000)
        | ((d["idade_anos"] < 0.5) & (d["capital_social"] < 500_000))
        | (~d["tem_email_valido"] & ~d["tem_telefone_valido"])
    )
    bl = d[mask].copy()
    motivos = []
    for _, r in bl.iterrows():
        ms = []
        if r["capital_social"] < 50_000:
            ms.append("capital<50k")
        if r["idade_anos"] < 0.5 and r["capital_social"] < 500_000:
            ms.append("nova+capital<500k")
        if not r["tem_email_valido"] and not r["tem_telefone_valido"]:
            ms.append("sem contato")
        motivos.append(" | ".join(ms))
    bl["motivo_exclusao"] = motivos
    return bl


def view_qualificados(d: pd.DataFrame) -> pd.DataFrame:
    """Base completa exceto blacklist."""
    mask_bl = (
        (d["capital_social"] < 50_000)
        | ((d["idade_anos"] < 0.5) & (d["capital_social"] < 500_000))
        | (~d["tem_email_valido"] & ~d["tem_telefone_valido"])
    )
    return d[~mask_bl].sort_values("lead_score", ascending=False)


# ---------- Sidebar: filtro global opcional ----------
st.sidebar.header("🔍 Busca rápida")
busca_razao = st.sidebar.text_input("Razão social ou nome fantasia").strip().upper()
busca_cnpj = st.sidebar.text_input("CNPJ (qualquer parte)").strip()

if busca_razao or busca_cnpj:
    mask = pd.Series(True, index=df.index)
    if busca_razao:
        mask &= (
            df["razao_social"].fillna("").str.upper().str.contains(busca_razao, regex=False)
            | df["nome_fantasia"].fillna("").str.upper().str.contains(busca_razao, regex=False)
        )
    if busca_cnpj:
        mask &= df["cnpj"].fillna("").str.contains(busca_cnpj, regex=False)
    df_base = df[mask]
    st.sidebar.success(f"{len(df_base):,} resultado(s) da busca")
else:
    df_base = df

st.sidebar.markdown("---")
st.sidebar.caption(
    f"Base: **{len(df):,}** estabelecimentos\n\n"
    f"Última atualização: parquet local"
)

# ---------- Header ----------
st.title("🏗️ Leads — Incorporadoras")

# ---------- Tabs (views prontas) ----------
VIEWS = [
    "📖 README",
    "🔥 Top 100",
    "🆕 Recém-criadas",
    "💰 Grandes",
    "📞 Contatáveis",
    "🕸️ Grupos",
    "🏘️ SP", "🏘️ RJ", "🏘️ MG", "🏘️ PR", "🏘️ SC",
    "📊 Base completa",
    "🚫 Lista negra",
]

# Radio na sidebar em vez de st.tabs porque tabs renderiza TUDO upfront
# e estoura memória de 1GB no plano free do Streamlit Cloud.
view_atual = st.sidebar.radio("📂 View", VIEWS, key="view_radio")


COLS_PRINCIPAIS = [
    "lead_score", "razao_social", "nome_fantasia", "cnpj",
    "capital_social", "bucket_capital", "idade_anos", "uf", "municipio",
    "telefone1", "email", "email_dominio_proprio",
    "qtd_socios", "nome_grupo_economico", "lead_score_motivos",
]


def _limpar_tel(ddd, tel) -> str:
    """Devolve telefone normalizado pra discador / wa.me."""
    s = "".join(c for c in f"{ddd or ''}{tel or ''}" if c.isdigit())
    if 8 <= len(s) <= 11:
        return s
    return ""


def gerar_xlsx_bytes(d: pd.DataFrame, cols: list[str]) -> bytes:
    """Gera XLSX formatado em memória."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        d_export = d[cols].copy()
        d_export.to_excel(writer, sheet_name="Leads", index=False)
        ws = writer.sheets["Leads"]
        if len(d_export):
            ws.autofilter(0, 0, len(d_export), len(cols) - 1)
            ws.freeze_panes(1, 0)
            for i, col in enumerate(cols):
                largura = {
                    "razao_social": 45, "nome_fantasia": 30, "cnpj": 16,
                    "logradouro": 30, "lead_score_motivos": 50, "email": 30,
                    "nome_grupo_economico": 40,
                }.get(col, 14)
                ws.set_column(i, i, largura)
    return buf.getvalue()


def gerar_lista_contatos(d: pd.DataFrame) -> pd.DataFrame:
    """Versão dedup'd por telefone único, formato CRM-friendly."""
    if len(d) == 0:
        return d
    out = pd.DataFrame({
        "razao_social": d["razao_social"],
        "cnpj": d["cnpj"],
        "telefone": d.apply(lambda r: _limpar_tel(r.get("ddd1"), r.get("telefone1")), axis=1),
        "telefone_e164": d.apply(
            lambda r: f"+55{_limpar_tel(r.get('ddd1'), r.get('telefone1'))}"
            if _limpar_tel(r.get("ddd1"), r.get("telefone1")) else "",
            axis=1,
        ),
        "whatsapp_url": d.apply(
            lambda r: f"https://wa.me/55{_limpar_tel(r.get('ddd1'), r.get('telefone1'))}"
            if _limpar_tel(r.get("ddd1"), r.get("telefone1")) else "",
            axis=1,
        ),
        "email": d["email"].fillna(""),
        "uf": d["uf"],
        "municipio": d["municipio"],
        "capital_social": d["capital_social"],
        "lead_score": d["lead_score"],
    })
    # dedup por telefone (vendedor não quer ligar 2x pro mesmo número)
    out = out.sort_values("lead_score", ascending=False)
    out_com_tel = out[out["telefone"] != ""].drop_duplicates(subset=["telefone"])
    out_sem_tel = out[out["telefone"] == ""]
    return pd.concat([out_com_tel, out_sem_tel], ignore_index=True)


def render_tabela(d: pd.DataFrame, key_prefix: str, extra_cols: list[str] | None = None):
    """Renderiza KPIs + tabela com seleção + múltiplos exports."""
    if len(d) == 0:
        st.info("(vazio — nenhum lead corresponde aos critérios desta view)")
        return

    # KPIs
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Leads", f"{len(d):,}")
    c2.metric("Score médio", f"{d['lead_score'].mean():.1f}")
    c3.metric(
        "Capital total",
        f"R$ {d['capital_social'].sum()/1e9:.2f}B"
        if d["capital_social"].sum() >= 1e9
        else f"R$ {d['capital_social'].sum()/1e6:.1f}M",
    )
    c4.metric(
        "Capital médio",
        f"R$ {d['capital_social'].mean()/1e6:.1f}M",
    )

    cols = [c for c in COLS_PRINCIPAIS if c in d.columns]
    if extra_cols:
        cols = list(dict.fromkeys(extra_cols + cols))

    # ---------- Limite de export ----------
    st.markdown("#### 📤 Exportar")
    col_l, col_dedup = st.columns([2, 1])
    with col_l:
        limite_opc = ["Top 50", "Top 100", "Top 500", "Top 1000", f"Tudo ({len(d):,})"]
        limite_sel = st.radio(
            "Quantos leads exportar?",
            options=limite_opc,
            horizontal=True,
            index=1,
            key=f"limite_{key_prefix}",
        )
    with col_dedup:
        dedup_tel = st.checkbox(
            "Dedup por telefone",
            value=True,
            help="Remove números duplicados (matriz/filiais com mesmo tel)",
            key=f"dedup_{key_prefix}",
        )

    if limite_sel.startswith("Tudo"):
        d_export = d
    else:
        n = int(re.search(r"\d+", limite_sel).group())
        d_export = d.head(n)

    if dedup_tel:
        with_tel = d_export[
            d_export.apply(
                lambda r: bool(_limpar_tel(r.get("ddd1"), r.get("telefone1"))), axis=1
            )
        ].drop_duplicates(
            subset=d_export.apply(
                lambda r: _limpar_tel(r.get("ddd1"), r.get("telefone1")), axis=1
            ).rename("__tel").to_frame().columns.tolist(),
        ) if False else d_export  # short-circuit a versão acima — vou aplicar diferente
        # forma simples: gera coluna temporária, dedup, descarta
        d_export = d_export.copy()
        d_export["__tel_norm"] = d_export.apply(
            lambda r: _limpar_tel(r.get("ddd1"), r.get("telefone1")), axis=1
        )
        com_tel = d_export[d_export["__tel_norm"] != ""].drop_duplicates(subset=["__tel_norm"])
        sem_tel = d_export[d_export["__tel_norm"] == ""]
        d_export = pd.concat([com_tel, sem_tel]).drop(columns=["__tel_norm"])

    st.caption(f"📦 {len(d_export):,} leads selecionados pra exportar")

    # ---------- Export LAZY: escolhe formato e GERA só ao clicar ----------
    formato = st.selectbox(
        "Formato de exportação",
        [
            "📄 CSV completo (todas as colunas)",
            "📊 XLSX formatado (Excel com filtros)",
            "📞 Lista de contatos (CRM-friendly)",
            "☎️ Telefones .txt (discador)",
            "✉️ Emails .txt (cadência)",
        ],
        key=f"fmt_{key_prefix}",
    )

    if st.button("⚙️ Gerar arquivo", key=f"gerar_{key_prefix}", width="stretch"):
        with st.spinner("Preparando arquivo..."):
            if formato.startswith("📄"):
                payload = d_export[cols].to_csv(index=False).encode("utf-8")
                fname = f"{key_prefix}_{len(d_export)}.csv"
                mime = "text/csv"
            elif formato.startswith("📊"):
                payload = gerar_xlsx_bytes(d_export, cols)
                fname = f"{key_prefix}_{len(d_export)}.xlsx"
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif formato.startswith("📞"):
                contatos = gerar_lista_contatos(d_export)
                payload = contatos.to_csv(index=False).encode("utf-8")
                fname = f"{key_prefix}_contatos_{len(contatos)}.csv"
                mime = "text/csv"
            elif formato.startswith("☎️"):
                tels = (
                    d_export.apply(
                        lambda r: _limpar_tel(r.get("ddd1"), r.get("telefone1")),
                        axis=1,
                    )
                    .replace("", pd.NA).dropna().drop_duplicates()
                )
                payload = "\n".join(tels.tolist()).encode("utf-8")
                fname = f"{key_prefix}_tels_{len(tels)}.txt"
                mime = "text/plain"
            else:  # emails
                email_re = r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$"
                emails = (
                    d_export["email"].fillna("").str.strip()
                    .where(d_export["email"].fillna("").str.contains(
                        email_re, regex=True, case=False, na=False))
                    .dropna().drop_duplicates()
                )
                payload = "\n".join(emails.tolist()).encode("utf-8")
                fname = f"{key_prefix}_emails_{len(emails)}.txt"
                mime = "text/plain"
            st.session_state[f"payload_{key_prefix}"] = (payload, fname, mime)

    if f"payload_{key_prefix}" in st.session_state:
        payload, fname, mime = st.session_state[f"payload_{key_prefix}"]
        st.download_button(
            f"💾 Baixar {fname}",
            data=payload,
            file_name=fname,
            mime=mime,
            key=f"dl_{key_prefix}",
            width="stretch",
        )

    # ---------- Tabela com seleção ----------
    st.markdown("#### 📋 Resultados")
    PAGE_SIZE = 100
    total_paginas = max(1, (len(d_export) + PAGE_SIZE - 1) // PAGE_SIZE)
    pagina = st.number_input(
        "Página", min_value=1, max_value=total_paginas, value=1, step=1,
        key=f"page_{key_prefix}",
    )
    inicio = (pagina - 1) * PAGE_SIZE
    fim = inicio + PAGE_SIZE
    pagina_df = d_export[cols].iloc[inicio:fim].copy()

    if "capital_social" in pagina_df.columns:
        pagina_df["capital_social"] = pagina_df["capital_social"].apply(
            lambda v: f"R$ {v:,.0f}" if pd.notna(v) else ""
        )

    # Modo seleção (data_editor com checkbox) ou só visualização
    modo_selecao = st.checkbox(
        "🎯 Modo seleção (escolher leads pra exportar)",
        value=False,
        key=f"sel_{key_prefix}",
    )

    if modo_selecao:
        pagina_df.insert(0, "✓", False)
        edited = st.data_editor(
            pagina_df,
            width="stretch",
            hide_index=True,
            disabled=[c for c in pagina_df.columns if c != "✓"],
            key=f"editor_{key_prefix}",
        )
        selecionados = edited[edited["✓"]].drop(columns=["✓"])
        if len(selecionados):
            st.success(f"✅ {len(selecionados)} selecionados nesta página")
            # Pega registros completos pelos CNPJs
            cnpjs_sel = selecionados["cnpj"].tolist()
            sel_full = d_export[d_export["cnpj"].isin(cnpjs_sel)]
            st.download_button(
                f"📥 Baixar {len(sel_full)} selecionados (CSV)",
                data=sel_full[cols].to_csv(index=False).encode("utf-8"),
                file_name=f"{key_prefix}_selecionados_{len(sel_full)}.csv",
                mime="text/csv",
                key=f"dl_sel_{key_prefix}",
            )
    else:
        try:
            st.dataframe(
                pagina_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "lead_score": st.column_config.ProgressColumn(
                        "Score", min_value=0, max_value=100, format="%d"
                    ),
                },
            )
        except Exception:
            st.dataframe(pagina_df, width="stretch", hide_index=True)


# ---------- Conteúdo da view selecionada (lazy: só renderiza a ativa) ----------
if view_atual == "📖 README":
    st.markdown("""
### 📖 Como usar

Este painel mostra **incorporadoras ativas** (CNAE 4110-7/00) extraídas da
Receita Federal e qualificadas por um lead score 0–100.

#### Views

| Tab | O que tem |
|---|---|
| 🔥 Top 100 | Top 100 leads por score |
| 🆕 Recém-criadas | Empresas <1 ano com capital >R$ 500k |
| 💰 Grandes | Capital >R$ 10M, >10 anos, matriz |
| 📞 Contatáveis | Score ≥60 com email + telefone válidos |
| 🕸️ Grupos | Holdings/SPEs (cruzamento de sócios) |
| 🏘️ SP/RJ/MG/PR/SC | Top 5 estados imobiliários |
| 📊 Base completa | Todos os qualificados |
| 🚫 Lista negra | Excluídos automaticamente (com motivo) |

#### Lead Score (0–100)

| Componente | Pontos | Critério |
|---|---|---|
| Capital social | 25 | log(capital), até R$ 50M |
| Idade da empresa | 15 | (idade/15) capped at 1 |
| Matriz | 15 | bool |
| Email válido | 10 | regex |
| Telefone válido | 10 | regex |
| Email corporativo | 10 | não-gmail/hotmail |
| UF top imobiliário | 10 | SP/RJ/MG/PR/SC |
| Bonus capital cidade | 5 | SP/RJ/MG/DF/RS |

A coluna **`lead_score_motivos`** explica em texto por que um lead recebeu
aquela pontuação. Ex: `Capital R$15M ✓ | Matriz ✓ | 12 anos ✓ | Email
corporativo ✓ | SP ✓`.

#### Lista negra (exclusão automática)

Um lead é excluído (vai pra aba 🚫) se:

- Capital social < R$ 50.000, **OU**
- Empresa com <6 meses E capital < R$ 500k, **OU**
- Sem nenhum contato válido (sem email **e** sem telefone)

#### Busca rápida

A barra lateral tem busca por razão social/CNPJ que **filtra todas as views
simultaneamente** — útil pra ver onde uma empresa aparece.

#### Detalhes da empresa

Abaixo de qualquer view, escolha um CNPJ no dropdown para ver:
- Endereço, contatos, capital
- Quadro societário (QSA) completo
- Outras empresas do mesmo grupo econômico
- Links de busca: Google, LinkedIn, WhatsApp
""")

elif view_atual == "🔥 Top 100":
    st.markdown("### 🔥 Top 100 — Quem ligar amanhã")
    st.caption("Os 100 leads com maior lead_score")
    render_tabela(view_top_100(df_base), "top100")

elif view_atual == "🆕 Recém-criadas":
    st.markdown("### 🆕 Recém-criadas — Compram tudo")
    st.caption("Empresas com <1 ano e capital social > R$ 500k. "
               "Acabaram de abrir, momento de compra.")
    render_tabela(view_recem(df_base), "recem")

elif view_atual == "💰 Grandes":
    st.markdown("### 💰 Grandes consolidadas — Tickets altos")
    st.caption("Capital > R$ 10M, mais de 10 anos de mercado, matriz.")
    render_tabela(view_grandes(df_base), "grandes")

elif view_atual == "📞 Contatáveis":
    st.markdown("### 📞 Contatáveis — Com email e telefone validados")
    st.caption("Score ≥60 + email regex válido + telefone regex válido.")
    render_tabela(view_contataveis(df_base), "contataveis")

elif view_atual == "🕸️ Grupos":
    st.markdown("### 🕸️ Grupos econômicos — Holdings + SPEs")
    st.caption("Empresas conectadas via sócios em comum (componentes conexos no grafo). "
               "Útil pra evitar prospectar a mesma empresa N vezes.")
    render_tabela(
        view_grupos(df_base),
        "grupos",
        extra_cols=["nome_grupo_economico", "qtd_empresas_grupo"],
    )

elif view_atual in ("🏘️ SP", "🏘️ RJ", "🏘️ MG", "🏘️ PR", "🏘️ SC"):
    uf = view_atual.split()[-1]
    st.markdown(f"### 🏘️ Estado {uf}")
    st.caption(f"Todas as incorporadoras qualificadas em {uf}, ordenadas por score.")
    render_tabela(view_uf(view_qualificados(df_base), uf), f"uf_{uf}")

elif view_atual == "📊 Base completa":
    st.markdown("### 📊 Base completa qualificada")
    st.caption("Todos os leads exceto a lista negra. Use a busca lateral pra filtrar.")
    render_tabela(view_qualificados(df_base), "base")

elif view_atual == "🚫 Lista negra":
    st.markdown("### 🚫 Lista negra — Excluídos automaticamente")
    st.caption("Use isso pra conferir se algum lead bom não foi excluído por engano.")
    render_tabela(
        view_blacklist(df_base),
        "blacklist",
        extra_cols=["motivo_exclusao"],
    )


# ---------- Detalhes da empresa (abaixo das tabs) ----------
st.markdown("---")
st.subheader("🔎 Detalhes da empresa")


def _v(reg, k, default=""):
    if k not in reg:
        return default
    val = reg[k]
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    return val


# Pega TOP 1000 da base completa qualificada como universo do dropdown
universo_busca = view_qualificados(df_base).head(1000)
opcoes_cnpj = universo_busca["cnpj"].tolist()

if opcoes_cnpj:
    cnpj_sel = st.selectbox(
        "Selecione um CNPJ (top 1000 do filtro atual):",
        options=opcoes_cnpj,
        format_func=lambda c: (
            f"{c} — "
            f"{universo_busca.loc[universo_busca['cnpj']==c, 'razao_social'].iloc[0]}"
        ),
    )
    if cnpj_sel:
        registro = universo_busca[universo_busca["cnpj"] == cnpj_sel].iloc[0]
        razao = _v(registro, "razao_social", "(sem razão social)")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"### {razao}")
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
            ddd = _v(registro, "ddd1")
            tel = _v(registro, "telefone1")
            email = _v(registro, "email")
            if ddd and tel:
                tel_full = f"{ddd}{tel}"
                wa_url = f"https://wa.me/55{tel_full}"
                st.write(f"**Telefone:** ({ddd}) {tel}  •  [WhatsApp]({wa_url})")
            elif tel:
                st.write(f"**Telefone:** {tel}")
            if email:
                st.write(f"**Email:** {email}")
            st.write(f"**CNAE principal:** {_v(registro, 'cnae_principal')}")
            cnae_sec = _v(registro, "cnae_secundario")
            if cnae_sec:
                st.write(f"**CNAEs secundários:** {cnae_sec}")

        # Links de busca rápidos
        st.markdown("#### 🔗 Buscar online")
        razao_q = quote_plus(str(razao))
        cnpj_q = quote_plus(str(_v(registro, "cnpj")))
        col_a, col_b, col_c = st.columns(3)
        col_a.markdown(f"[🔍 Google]({f'https://www.google.com/search?q={razao_q}+{cnpj_q}'})")
        col_b.markdown(
            f"[💼 LinkedIn empresa]({f'https://www.google.com/search?q=site:linkedin.com/company+{razao_q}'})"
        )
        col_c.markdown(
            f"[📰 Notícias]({f'https://www.google.com/search?q={razao_q}&tbm=nws'})"
        )

        # Quadro societário
        st.markdown("#### 👥 Quadro societário")
        cnpj_basico_sel = _v(registro, "cnpj_basico")
        socios_emp = (
            socios[socios["cnpj_basico"] == cnpj_basico_sel]
            if cnpj_basico_sel else socios.iloc[0:0]
        )
        if len(socios_emp):
            socios_show = socios_emp[
                ["nome_socio", "tipo_socio", "qualificacao_socio",
                 "data_entrada", "faixa_etaria", "cpf_cnpj_socio"]
            ].copy()

            # Cruzamento: pra cada sócio, quantas outras incorporadoras ele tem?
            socio_para_qtd = (
                socios.groupby("cpf_cnpj_socio")["cnpj_basico"]
                .nunique()
                .rename("qtd_outras_empresas")
            )
            socios_show = socios_show.merge(
                socio_para_qtd, left_on="cpf_cnpj_socio", right_index=True, how="left"
            )
            socios_show["qtd_outras_empresas"] = (
                socios_show["qtd_outras_empresas"].fillna(1).astype(int) - 1
            )
            st.dataframe(socios_show, hide_index=True, width="stretch")

            # Link LinkedIn pra cada sócio (top 5)
            st.markdown("**🔗 Buscar sócios no LinkedIn:**")
            for nome in socios_emp["nome_socio"].head(5):
                if not isinstance(nome, str):
                    continue
                nome_q = quote_plus(nome)
                st.markdown(
                    f"- {nome} → "
                    f"[LinkedIn](https://www.google.com/search?q=site:linkedin.com/in+{nome_q})"
                )
        else:
            st.info("Nenhum sócio encontrado.")

        # Outras empresas do mesmo grupo
        if grupo:
            st.markdown("#### 🕸️ Outras empresas do grupo")
            outras = df[
                (df["nome_grupo_economico"] == grupo)
                & (df["cnpj"] != _v(registro, "cnpj"))
            ].sort_values("capital_social", ascending=False)
            if len(outras):
                st.dataframe(
                    outras[["razao_social", "cnpj", "uf", "capital_social", "lead_score"]],
                    hide_index=True,
                    width="stretch",
                )
else:
    st.info("Use a busca lateral ou navegue pelas views acima.")
