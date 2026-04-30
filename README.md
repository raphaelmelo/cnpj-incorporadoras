# Pipeline de Leads — Incorporadoras

Pipeline completo: extrai empresas com CNAE de incorporadora (4110-7/00) dos
dados abertos da Receita Federal, enriquece com lead score + features +
grupos econômicos, e disponibiliza em 4 formatos:

1. **CSV bruto** — pra processar em outras ferramentas
2. **Excel multi-aba** — pra mandar pro time comercial direto por email
3. **Streamlit** — UI web local com filtros
4. **Datasette** — UI web que pode ser publicada pra equipe inteira acessar

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Fluxo completo

```
extrair_incorporadoras.py    →  data/incorporadoras.csv (77MB, 311k linhas)
                                data/incorporadoras_socios.csv (45MB, 631k linhas)

qualificar_leads.py          →  data/leads_incorporadoras.xlsx (127MB)
                                data/incorporadoras_enriquecido.parquet (34MB)

viewer_streamlit.py          →  UI web em http://localhost:8501

setup_datasette.py           →  data/incorporadoras.db (SQLite)
                                data/datasette_metadata.json
```

## 1. Extrair (1x por mês)

Baixa os dumps mensais da Receita, descompacta, converte CP1252→UTF-8,
filtra por CNAE 4110-7/00, gera 2 CSVs:

```bash
python extrair_incorporadoras.py
```

Tempo: ~30-40 min (download ~10GB) + ~5min processamento.
Re-execução pula download dos arquivos já presentes.

## 2. Qualificar leads

Calcula features, lead score 0-100, detecta grupos econômicos via grafo
de sócios e gera 2 saídas:

- **`leads_incorporadoras.xlsx`** — 12 abas (Top 100, Recém-criadas, Grandes,
  Contatáveis, Grupos econômicos, Lista negra, Base completa, e 1 aba por UF top)
- **`incorporadoras_enriquecido.parquet`** — versão otimizada pros viewers

```bash
python qualificar_leads.py
```

Tempo: ~1-2 min.

## 3a. Streamlit (uso local)

UI web local com filtros laterais, busca, KPIs e detalhes por empresa.

```bash
streamlit run viewer_streamlit.py
```

Abre automaticamente no http://localhost:8501.

**Recursos:**
- Filtros: UF, região, porte, idade, score, contato, grupo econômico, busca por razão/CNPJ
- Tabela paginada com heatmap de score
- Click numa empresa → detalhes + QSA + outras empresas do mesmo grupo
- Botão "exportar CSV filtrado"

## 3b. Datasette (uso local ou compartilhado)

SQL livre, queries salvas e UI web. Pode ser publicado pra equipe acessar.

```bash
# 1. Criar o SQLite a partir do parquet
python setup_datasette.py

# 2. Servir localmente
datasette serve data/incorporadoras.db --metadata data/datasette_metadata.json -o
```

Abre em http://localhost:8001. Já vem com 7 queries pré-configuradas:

- 🔥 Top 100 leads por score
- 💰 Grandes consolidadas (>10M, >10y, matriz)
- 🆕 Recém-criadas (<1 ano, capital >500k)
- 📞 Contatáveis (score >=60, com email+tel)
- 🕸️ Grupos econômicos (>=3 empresas)
- 📊 Ranking de capital por UF
- 🔍 Buscar empresas por nome de sócio (parametrizada)

**Deploy pra equipe** (Fly.io, US$0-5/mês):

```bash
datasette publish fly data/incorporadoras.db \
    -m data/datasette_metadata.json \
    --app cnpj-leads
```

Em ~5 minutos tem URL pública pro time inteiro acessar.

## Schema dos dados

### incorporadoras.csv (saída do extrair)
| Campo | Tipo | Descrição |
|---|---|---|
| cnpj | str | CNPJ completo (14 dígitos) |
| razao_social | str | Razão social da empresa |
| nome_fantasia | str | Nome fantasia |
| capital_social | float | Capital social em R$ |
| matriz_filial | str | "MATRIZ" ou "FILIAL" |
| cnae_principal | str | Código CNAE principal |
| cnae_secundario | str | CNAEs secundários (CSV) |
| logradouro/numero/bairro/cep/uf/municipio | str | Endereço |
| telefone1/email | str | Contatos |
| cnpj_basico | str | 8 primeiros dígitos (chave de relacionamento) |

### incorporadoras_enriquecido.parquet (saída do qualificar)
Tudo do CSV +:

| Campo | Descrição |
|---|---|
| lead_score | 0-100 (capital + idade + matriz + contato + UF) |
| lead_score_motivos | Texto explicável do score |
| bucket_capital | micro/pequena/media/grande |
| bucket_idade | nascente/crescimento/madura/consolidada |
| idade_anos | Anos desde início da atividade |
| eh_matriz | bool |
| qtd_estabelecimentos | matriz + filiais |
| multi_estado | tem filiais em UFs diferentes |
| parece_spe | heurística "Sociedade de Propósito Específico" |
| qtd_socios | quantos sócios na empresa |
| tem_socio_pj | algum sócio é PJ (parte de grupo) |
| nome_grupo_economico | label do grupo (BFS no grafo de sócios) |
| qtd_empresas_grupo | tamanho do componente conexo |
| tem_email_valido / tem_telefone_valido / email_dominio_proprio | bools |
| score_contato | 0-3 |
| regiao | Norte/Nordeste/Sudeste/Sul/Centro-Oeste |
| percentil_capital_uf | 0-1 dentro do estado |

## Customização

- **Outro CNAE:** edite `CNAE_INCORPORADORA` em `extrair_incorporadoras.py`
- **Pesos do score:** edite a função `aplicar_features` em `qualificar_leads.py`
- **Filtros do Streamlit:** edite `viewer_streamlit.py` (sidebar)
- **Queries do Datasette:** edite `setup_datasette.py` (dict `metadata`)

## Atualização mensal

A RFB atualiza no início de cada mês. Para gerar uma nova versão:

```bash
python extrair_incorporadoras.py    # detecta competência mais recente
python qualificar_leads.py
python setup_datasette.py            # se usar Datasette
```
