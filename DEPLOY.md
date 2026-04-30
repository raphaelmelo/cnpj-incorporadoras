# Deploy gratuito — Streamlit Community Cloud

Guia passo-a-passo para subir o `viewer_streamlit.py` na nuvem da Streamlit
(grátis, sem cartão), com proteção por senha para o time comercial.

## Pré-requisitos

- Conta no [GitHub](https://github.com) (grátis)
- Conta na [Streamlit Community Cloud](https://share.streamlit.io) (grátis,
  loga com GitHub)

---

## Passo 1 — Criar repositório no GitHub

```bash
cd /Volumes/PortableSSD/raphaelmelogois/cnpj
git init
git add .
git commit -m "Pipeline inicial de leads de incorporadoras"
```

Crie um repositório novo no GitHub (ex: `cnpj-incorporadoras`).
**Pode ser público** — os dados são abertos da Receita Federal.

```bash
git remote add origin https://github.com/SEU_USUARIO/cnpj-incorporadoras.git
git branch -M main
git push -u origin main
```

### O que sobe e o que NÃO sobe

O `.gitignore` já está configurado:

✅ Sobe (cabe no GitHub <100MB):
- `viewer_streamlit.py`, `qualificar_leads.py`, `extrair_incorporadoras.py`
- `requirements.txt`, `README.md`
- `data/incorporadoras_enriquecido.parquet` (~34MB) — base do app
- `data/incorporadoras_socios.csv` (~45MB) — usado nos detalhes da empresa

❌ NÃO sobe:
- `data/zips/`, `data/extracted/` — arquivos temporários (~10GB)
- `data/incorporadoras.csv` (77MB) — redundante com o parquet
- `data/leads_incorporadoras.xlsx` (127MB) — passa do limite GitHub
- `.streamlit/secrets.toml` — senha (segredo!)

---

## Passo 2 — Configurar a senha

```bash
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edite a senha em .streamlit/secrets.toml
```

Para testar localmente com a senha:

```bash
streamlit run viewer_streamlit.py
```

(o `secrets.toml` está no `.gitignore`, não vai pro GitHub)

---

## Passo 3 — Deploy na Streamlit Community Cloud

1. Vá em https://share.streamlit.io
2. Login com GitHub
3. Clique **"New app"**
4. Preencha:
   - **Repository:** `SEU_USUARIO/cnpj-incorporadoras`
   - **Branch:** `main`
   - **Main file path:** `viewer_streamlit.py`
   - **App URL:** escolha algo como `cnpj-leads-incorporadoras`
5. Clique em **"Advanced settings"** → **Secrets**, e cole:

   ```toml
   password = "sua_senha_definitiva_aqui"
   ```

6. Clique **"Deploy"**

A primeira build demora ~3-5 min (instala todas as dependências).
A URL final fica tipo `https://cnpj-leads-incorporadoras.streamlit.app`.

---

## Passo 4 — Compartilhar com o comercial

Mande o link e a senha para o time:

> **App de leads:** https://cnpj-leads-incorporadoras.streamlit.app
> **Senha:** xxxxxxx

Eles vão ver:
- Filtros laterais (UF, capital, idade, score, contato, grupo)
- Tabela paginada com heatmap no score
- Detalhes da empresa selecionada (incluindo QSA e outras empresas do grupo)
- Botão para exportar CSV filtrado

---

## Limites do plano gratuito

- **1 GB RAM** — cabe sem problema (parquet de 34MB)
- **Sem limite de visitantes** — comercial inteiro pode usar
- **App "dorme" após inatividade** — primeira pessoa do dia espera ~30s pra
  acordar. Depois fica rápido.
- **Repo público obrigatório** — não tem problema porque os dados são abertos

Se quiser repo privado: Streamlit cobra a partir de US$20/mês.

---

## Atualização mensal

A RFB atualiza no início de cada mês. Pra atualizar o app:

```bash
# 1. Localmente, gere os dados novos
python extrair_incorporadoras.py    # ~30 min
python qualificar_leads.py          # ~2 min

# 2. Commita só o parquet e socios (rápido)
git add data/incorporadoras_enriquecido.parquet data/incorporadoras_socios.csv
git commit -m "Atualização: competência YYYY-MM"
git push

# 3. Streamlit Cloud detecta o push e redeploya sozinho em ~2min
```

---

## Trocar a senha depois

1. Acesse o app no [share.streamlit.io](https://share.streamlit.io)
2. Clique nos 3 pontinhos ao lado do app → **Settings**
3. Aba **Secrets** → edite e salve
4. App recarrega automaticamente em segundos
