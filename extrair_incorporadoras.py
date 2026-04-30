"""
Extrai dados de empresas incorporadoras (CNAE 4110-7/00) dos dados abertos
do CNPJ da Receita Federal.

Saída: data/incorporadoras.csv e data/incorporadoras_socios.csv
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

import duckdb
import requests
from tqdm import tqdm

# A RFB hospeda os dumps em um Nextcloud. O acesso público é via WebDAV
# usando um share_token como usuário (sem senha). Layout vigente desde fev/2026.
SHARE_TOKEN = "YggdBLfdninEJX9"
WEBDAV_URL = f"https://arquivos.receitafederal.gov.br/public.php/webdav"
DOWNLOAD_BASE = f"https://arquivos.receitafederal.gov.br/public.php/dav/files/{SHARE_TOKEN}"
DAV_NS = {"d": "DAV:"}

DATA_DIR = Path(__file__).parent / "data"
ZIPS_DIR = DATA_DIR / "zips"
CNAE_INCORPORADORA = "4110700"
SITUACAO_ATIVA = "02"

GROUPS = ("Empresas", "Estabelecimentos", "Socios")


def _propfind(url: str) -> ElementTree.Element:
    resp = requests.request(
        "PROPFIND", url, auth=(SHARE_TOKEN, ""), headers={"Depth": "1"}, timeout=60
    )
    resp.raise_for_status()
    return ElementTree.fromstring(resp.content)


def descobrir_mes_mais_recente() -> tuple[str, list[str]]:
    """Lista pastas YYYY-MM via WebDAV, retorna (competencia, arquivos_zip)."""
    print(f"[1/4] Descobrindo competência mais recente via WebDAV")
    root = _propfind(WEBDAV_URL + "/")
    competencias: list[str] = []
    for r in root.findall("d:response", DAV_NS):
        href = r.find("d:href", DAV_NS).text or ""
        m = re.search(r"(\d{4}-\d{2})/?$", href)
        if m:
            competencias.append(m.group(1))
    if not competencias:
        raise RuntimeError("Nenhuma pasta YYYY-MM encontrada no WebDAV da RFB")
    competencias.sort()
    competencia = competencias[-1]

    root = _propfind(f"{WEBDAV_URL}/{competencia}/")
    arquivos: list[str] = []
    for r in root.findall("d:response", DAV_NS):
        href = r.find("d:href", DAV_NS).text or ""
        m = re.search(r"/([^/]+\.zip)$", href, re.IGNORECASE)
        if m:
            arquivos.append(m.group(1))
    print(f"      → competência: {competencia} ({len(arquivos)} arquivos disponíveis)")
    return competencia, arquivos


def _tamanho_remoto(url: str) -> int:
    head = requests.head(url, auth=(SHARE_TOKEN, ""), timeout=30, allow_redirects=True)
    head.raise_for_status()
    return int(head.headers.get("Content-Length", 0))


def baixar_arquivo(url: str, destino: Path, max_tentativas: int = 6) -> None:
    """Baixa com retry e resume via HTTP Range. Idempotente."""
    auth = (SHARE_TOKEN, "")
    try:
        total = _tamanho_remoto(url)
    except requests.RequestException as e:
        raise RuntimeError(f"não consegui consultar tamanho de {url}: {e}") from e

    if destino.exists() and destino.stat().st_size == total:
        return
    if destino.exists() and destino.stat().st_size > total:
        destino.unlink()

    for tentativa in range(1, max_tentativas + 1):
        baixados = destino.stat().st_size if destino.exists() else 0
        if baixados == total:
            return

        headers = {"Range": f"bytes={baixados}-"} if baixados else {}
        try:
            with requests.get(
                url, auth=auth, headers=headers, stream=True, timeout=(30, 120)
            ) as resp:
                if baixados and resp.status_code == 200:
                    # servidor ignorou o Range — começa do zero
                    destino.unlink()
                    baixados = 0
                elif resp.status_code not in (200, 206):
                    resp.raise_for_status()

                modo = "ab" if baixados else "wb"
                with open(destino, modo) as f, tqdm(
                    total=total,
                    initial=baixados,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=destino.name,
                    leave=False,
                ) as bar:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            bar.update(len(chunk))
            if destino.stat().st_size == total:
                return
        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.ReadTimeout,
        ) as e:
            espera = min(60, 2**tentativa)
            print(
                f"      ! {destino.name} falhou ({type(e).__name__}); "
                f"retry {tentativa}/{max_tentativas} em {espera}s"
            )
            time.sleep(espera)

    raise RuntimeError(
        f"falha persistente ao baixar {destino.name} após {max_tentativas} tentativas"
    )


def baixar_dumps(competencia: str, arquivos: list[str]) -> None:
    """Baixa apenas Empresas*.zip, Estabelecimentos*.zip e Socios*.zip."""
    ZIPS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[2/4] Baixando dumps em {ZIPS_DIR}")
    alvos = [a for a in arquivos if any(a.startswith(g) for g in GROUPS)]
    if not alvos:
        raise RuntimeError(f"Nenhum arquivo dos grupos {GROUPS} em {competencia}")
    falhas: list[str] = []
    for nome in sorted(alvos):
        url = f"{DOWNLOAD_BASE}/{competencia}/{nome}"
        destino = ZIPS_DIR / nome
        try:
            baixar_arquivo(url, destino)
            print(f"      ✓ {nome}")
        except RuntimeError as e:
            print(f"      ✗ {nome}: {e}")
            falhas.append(nome)
    if falhas:
        raise RuntimeError(
            f"{len(falhas)} arquivo(s) não baixaram: {falhas}. "
            "Rode o script de novo para retomar."
        )


EXTRACTED_DIR = DATA_DIR / "extracted"


def descompactar_grupo(grupo: str) -> list[str]:
    """Descompacta + sanitiza + converte CP1252→UTF-8 em streaming.
    A RFB usa CP1252 (windows-1252). DuckDB nativo só suporta UTF-8/UTF-16/latin-1
    e a extensão 'encodings' tem bug conhecido com windows-1252-2000.
    Solução: converter pra UTF-8 aqui, DuckDB lê UTF-8 nativamente.
    Idempotente via marcador `.utf8`."""
    import zipfile
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    csvs: list[str] = []
    zips = sorted(ZIPS_DIR.glob(f"{grupo}*.zip"))
    for idx, zpath in enumerate(zips, 1):
        with zipfile.ZipFile(zpath) as zf:
            for nome in zf.namelist():
                destino = EXTRACTED_DIR / f"{zpath.stem}__{nome}"
                marcador = destino.with_suffix(destino.suffix + ".utf8")
                if destino.exists() and marcador.exists():
                    print(f"      ⇣ [{idx}/{len(zips)}] {zpath.name} (já em UTF-8, pulando)")
                    csvs.append(str(destino))
                    continue
                tamanho_total = zf.getinfo(nome).file_size
                nulls = 0
                # Decodifica CP1252 byte-a-byte (sempre válido — 256 bytes mapeados)
                # e re-encoda como UTF-8. Faz tudo em streaming, ~4MB de buffer.
                with zf.open(nome) as src, open(destino, "wb") as dst, tqdm(
                    total=tamanho_total,
                    unit="B", unit_scale=True, unit_divisor=1024,
                    desc=f"[{idx}/{len(zips)}] {zpath.name}",
                    leave=False,
                ) as bar:
                    pendente = b""
                    while chunk := src.read(1024 * 1024 * 4):
                        bar.update(len(chunk))
                        if b"\x00" in chunk:
                            nulls += chunk.count(b"\x00")
                            chunk = chunk.replace(b"\x00", b"")
                        # Junta com sobra anterior pra não cortar no meio de uma linha
                        chunk = pendente + chunk
                        # Acha último \n pra cortar limpo
                        ultimo_lf = chunk.rfind(b"\n")
                        if ultimo_lf == -1:
                            pendente = chunk
                            continue
                        decodificavel, pendente = chunk[:ultimo_lf+1], chunk[ultimo_lf+1:]
                        dst.write(decodificavel.decode("cp1252", errors="replace").encode("utf-8"))
                    if pendente:
                        dst.write(pendente.decode("cp1252").encode("utf-8"))
                if nulls:
                    print(f"        (removidos {nulls:,} null bytes)")
                marcador.touch()
                csvs.append(str(destino))
    return csvs


def extrair_incorporadoras() -> None:
    """Roda DuckDB sobre os ZIPs, gera os dois CSVs de saída."""
    print("[3/4] Processando com DuckDB")
    con = duckdb.connect(":memory:")
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA enable_progress_bar")

    print("      → descompactando ZIPs (necessário para encoding latin-1)")
    estab_zips = descompactar_grupo("Estabelecimentos")
    emp_zips = descompactar_grupo("Empresas")
    socio_zips = descompactar_grupo("Socios")
    if not estab_zips or not emp_zips:
        raise RuntimeError("CSVs de Empresas/Estabelecimentos não encontrados")

    estab_cols = [
        "cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial",
        "nome_fantasia", "situacao_cadastral", "data_situacao_cadastral",
        "motivo_situacao", "nome_cidade_exterior", "pais",
        "data_inicio_atividade", "cnae_principal", "cnae_secundario",
        "tipo_logradouro", "logradouro", "numero", "complemento", "bairro",
        "cep", "uf", "municipio", "ddd1", "telefone1", "ddd2", "telefone2",
        "ddd_fax", "fax", "email", "situacao_especial", "data_situacao_especial",
    ]
    emp_cols = [
        "cnpj_basico", "razao_social", "natureza_juridica",
        "qualificacao_responsavel", "capital_social", "porte",
        "ente_federativo",
    ]
    socio_cols = [
        "cnpj_basico", "tipo_socio", "nome_socio", "cpf_cnpj_socio",
        "qualificacao_socio", "data_entrada", "pais", "cpf_representante",
        "nome_representante", "qualificacao_representante", "faixa_etaria",
    ]

    con.execute(f"""
        CREATE VIEW estabelecimentos AS
        SELECT * FROM read_csv(
            {estab_zips!r},
            delim=';', header=false, encoding='utf-8',
            quote='"', escape='"',
            ignore_errors=true, strict_mode=false,
            columns={{{', '.join(f"'{c}': 'VARCHAR'" for c in estab_cols)}}}
        )
    """)
    con.execute(f"""
        CREATE VIEW empresas AS
        SELECT * FROM read_csv(
            {emp_zips!r},
            delim=';', header=false, encoding='utf-8',
            quote='"', escape='"',
            ignore_errors=true, strict_mode=false,
            columns={{{', '.join(f"'{c}': 'VARCHAR'" for c in emp_cols)}}}
        )
    """)
    if socio_zips:
        con.execute(f"""
            CREATE VIEW socios_raw AS
            SELECT * FROM read_csv(
                {socio_zips!r},
                delim=';', header=false, encoding='utf-8',
                quote='"', escape='"',
                columns={{{', '.join(f"'{c}': 'VARCHAR'" for c in socio_cols)}}}
            )
        """)

    out_emp = DATA_DIR / "incorporadoras.csv"
    print(f"      → filtrando CNAE {CNAE_INCORPORADORA} ativas")
    con.execute(f"""
        COPY (
            SELECT
                est.cnpj_basico || est.cnpj_ordem || est.cnpj_dv AS cnpj,
                emp.razao_social,
                est.nome_fantasia,
                CAST(REPLACE(emp.capital_social, ',', '.') AS DOUBLE) AS capital_social,
                emp.porte,
                emp.natureza_juridica,
                CASE est.matriz_filial WHEN '1' THEN 'MATRIZ' WHEN '2' THEN 'FILIAL' END AS matriz_filial,
                est.cnae_principal,
                est.cnae_secundario,
                est.situacao_cadastral,
                est.data_inicio_atividade,
                est.tipo_logradouro,
                est.logradouro,
                est.numero,
                est.complemento,
                est.bairro,
                est.cep,
                est.uf,
                est.municipio,
                est.ddd1,
                est.telefone1,
                est.email,
                est.cnpj_basico
            FROM estabelecimentos est
            INNER JOIN empresas emp USING (cnpj_basico)
            WHERE est.situacao_cadastral = '{SITUACAO_ATIVA}'
              AND (
                  est.cnae_principal = '{CNAE_INCORPORADORA}'
                  OR est.cnae_secundario LIKE '%{CNAE_INCORPORADORA}%'
              )
        ) TO '{out_emp}' (HEADER, DELIMITER ',', QUOTE '"')
    """)
    qtd_emp = con.execute(f"SELECT COUNT(*) FROM read_csv('{out_emp}')").fetchone()[0]
    print(f"      ✓ {out_emp} ({qtd_emp:,} linhas)")

    if socio_zips:
        out_soc = DATA_DIR / "incorporadoras_socios.csv"
        print("      → cruzando com sócios")
        con.execute(f"""
            COPY (
                SELECT DISTINCT
                    s.cnpj_basico,
                    s.tipo_socio,
                    s.nome_socio,
                    s.cpf_cnpj_socio,
                    s.qualificacao_socio,
                    s.data_entrada,
                    s.pais,
                    s.nome_representante,
                    s.qualificacao_representante,
                    s.faixa_etaria
                FROM socios_raw s
                WHERE s.cnpj_basico IN (
                    SELECT DISTINCT est.cnpj_basico
                    FROM estabelecimentos est
                    WHERE est.situacao_cadastral = '{SITUACAO_ATIVA}'
                      AND (
                          est.cnae_principal = '{CNAE_INCORPORADORA}'
                          OR est.cnae_secundario LIKE '%{CNAE_INCORPORADORA}%'
                      )
                )
            ) TO '{out_soc}' (HEADER, DELIMITER ',', QUOTE '"')
        """)
        qtd_soc = con.execute(f"SELECT COUNT(*) FROM read_csv('{out_soc}')").fetchone()[0]
        print(f"      ✓ {out_soc} ({qtd_soc:,} linhas)")

    con.close()

    # Os CSVs extraídos ocupam ~85GB. Apaga depois do processamento.
    # Para preservar (debug), comente as linhas abaixo.
    print("      → limpando CSVs descompactados temporários")
    for csv in EXTRACTED_DIR.glob("*"):
        csv.unlink()
    EXTRACTED_DIR.rmdir()


def main() -> int:
    inicio = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    competencia, arquivos = descobrir_mes_mais_recente()
    baixar_dumps(competencia, arquivos)
    extrair_incorporadoras()
    minutos = (time.time() - inicio) / 60
    print(f"[4/4] Concluído em {minutos:.1f} min — saída em {DATA_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
