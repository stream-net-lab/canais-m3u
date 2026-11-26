#!/usr/bin/env python3
"""
Gera o catálogo VOD fatiado (pasta vod/) a partir de vod.m3u no formato exato
lido pelo app TV & Filmes (Vod.kt, Generos.kt, Destaques.kt).

Recursos:
- Separa Filmes e Séries/Animes/Doramas (com temporadas e episódios).
- Prioriza sempre Dublado (PT-BR) e ordena servidores por latência (amostragem HTTP).
- Extrai capas TMDB e gêneros para fichas.txt (sem precisar consultar API na TV Box).
- Monta fileiras de destaques em destaques.txt (Em Alta, Lançamentos, Séries, Ação, Comédia, Terror, Animes).
- Compacta prefixos de URLs repetidos via linhas `base:` em indice.txt para arquivos ultraleves.
"""

import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOD_M3U = os.path.join(REPO_DIR, "vod.m3u")
VOD_DIR = os.path.join(REPO_DIR, "vod")
CAPA_BASE = "https://image.tmdb.org/t/p/w342"

RE_ATTR = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')
RE_SE = re.compile(
    r'^(.*?)\s*(?:\((Dublado|Legendado)[^)]*\))?\s*S(\d{1,2})\s*E(\d{1,3})\b',
    re.IGNORECASE,
)
RE_ANO = re.compile(r'\s*\((\d{4})\)\s*$')
RE_TMDB_ID = re.compile(r'[?&]id=(\d+)')
RE_VERSAO_DISPLAY = re.compile(r'\((Dublado|Legendado)[^)]*\)', re.IGNORECASE)
RE_PREFIXO_ARTIGO = re.compile(r'^(?:O |A |Os |As |The )')

SERIES_POR_PEDACO = 35


def normalizar_chave(texto: str) -> str:
    s = unicodedata.normalize("NFD", texto)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def gaveta_letra(titulo: str) -> str:
    sem_ano = RE_ANO.sub("", titulo).strip()
    limpo = RE_PREFIXO_ARTIGO.sub("", sem_ano).strip()
    if not limpo:
        return "#"
    c = limpo[0].upper()
    return c if "A" <= c <= "Z" else "#"


def limpar_poster(logo: str) -> str:
    logo = (logo or "").strip()
    if not logo:
        return ""
    if logo.startswith(CAPA_BASE):
        return logo[len(CAPA_BASE):]
    return logo


def extrair_genero(grupo: str) -> str:
    g = (grupo or "").strip()
    if not g:
        return ""
    if g.startswith("Filmes - "):
        sub = g[len("Filmes - "):].strip()
        if sub in ("Geral", "Em Alta", "Tv", "TV") or sub.startswith("Lançamentos"):
            return ""
        return sub
    if "Anime" in g:
        return "Animes"
    if "Dorama" in g:
        return "Doramas"
    if "Série" in g or "Serie" in g:
        return "Séries"
    return ""


def testar_host_latencia(amostra_url: str, timeout: float = 3.5) -> float:
    inicio = time.monotonic()
    try:
        req = urllib.request.Request(
            amostra_url,
            method="HEAD",
            headers={"User-Agent": "TVFilmes/4.1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status < 500:
                return (time.monotonic() - inicio) * 1000.0
    except Exception:
        pass
    # Tenta GET com Range mínimo caso o servidor recuse HEAD
    inicio = time.monotonic()
    try:
        req = urllib.request.Request(
            amostra_url,
            headers={"User-Agent": "TVFilmes/4.1", "Range": "bytes=0-1023"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status < 500:
                return (time.monotonic() - inicio) * 1000.0
    except Exception:
        pass
    return 99999.0


def medir_hosts(amostras_por_host: dict[str, str], pular_teste: bool = False) -> dict[str, float]:
    if pular_teste or not amostras_por_host:
        return {h: 500.0 for h in amostras_por_host}
    print(f"Medindo latência de {len(amostras_por_host)} servidores VOD...")
    resultados = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(testar_host_latencia, url): host for host, url in amostras_por_host.items()}
        for fut, host in futs.items():
            lat = fut.result()
            resultados[host] = lat
            status = f"{lat:.0f} ms" if lat < 90000 else "lento/timeout"
            print(f"  [VOD] {host}: {status}")
    return resultados


def prefixo_url(url: str) -> tuple[str, str]:
    """Divide uma URL em (prefixo_reutilizavel, resto) para compactação `base:`."""
    if "?" in url:
        return ("", url)
    idx = url.rfind("/")
    if idx > 10:
        return (url[: idx + 1], url[idx + 1 :])
    return ("", url)


def compactar_url(url: str, mapa_bases: dict[str, int]) -> str:
    pref, resto = prefixo_url(url)
    if pref and pref in mapa_bases and resto and "," not in resto:
        idx = mapa_bases[pref]
        if resto.endswith(".mp4") and resto.count(".") == 1:
            resto = resto[:-4]
        return f"{idx}:{resto}"
    return url


def gerar_vod_fatiado(m3u_path: str = VOD_M3U, out_dir: str = VOD_DIR, testar_velocidade: bool = True):
    if not os.path.exists(m3u_path):
        print(f"Aviso: {m3u_path} não encontrado; pulando geração VOD fatiado.")
        return

    t0 = time.monotonic()
    print(f"Lendo {m3u_path}...")

    # filmes[titulo] = {"dub": [], "leg": [], "poster": "", "tmdb": 0, "generos": set(), "grupos": set()}
    filmes = {}
    # series[(titulo, ano)] = {"eps": {(temp, ep, versao): []}, "poster": "", "tmdb": 0, "generos": set(), "grupos": set()}
    series = {}
    amostras_host = {}
    contagem_prefixos = Counter()

    extinf_atual = None
    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
        for linha in f:
            linha = linha.strip()
            if not linha:
                continue
            if linha.startswith("#EXTINF"):
                extinf_atual = linha
                continue
            if linha.startswith("#"):
                continue
            if not extinf_atual:
                continue

            url = linha
            attrs = dict(RE_ATTR.findall(extinf_atual))
            display = extinf_atual.rsplit(",", 1)[-1].strip() if "," in extinf_atual else ""
            extinf_atual = None

            if not (url.startswith("http://") or url.startswith("https://")):
                continue

            # Registra host para benchmark e prefixo para compactação
            try:
                host = urllib.parse.urlsplit(url).netloc.lower()
                if host and host not in amostras_host:
                    amostras_host[host] = url
            except Exception:
                host = ""

            pref, _ = prefixo_url(url)
            if pref:
                contagem_prefixos[pref] += 1

            tvg_name = (attrs.get("tvg-name") or display).strip()
            grupo = (attrs.get("group-title") or "").strip()
            poster = limpar_poster(attrs.get("tvg-logo") or "")
            m_id = RE_TMDB_ID.search(url)
            tmdb_id = int(m_id.group(1)) if m_id else 0

            # Determina versão (dub vs leg)
            versao = "dub"
            if "versao=leg" in url.lower() or "legendad" in display.lower() or "legendad" in grupo.lower():
                versao = "leg"

            # Verifica se é episódio de série/anime/dorama
            m_se = RE_SE.match(tvg_name) or RE_SE.match(display)
            if m_se:
                nome_bruto = m_se.group(1).strip()
                if m_se.group(2):
                    versao = "leg" if m_se.group(2).lower().startswith("leg") else "dub"
                temp = int(m_se.group(3))
                ep = int(m_se.group(4))

                m_ano = RE_ANO.search(nome_bruto)
                ano = m_ano.group(1) if m_ano else ""
                titulo = RE_ANO.sub("", nome_bruto).strip()
                if not titulo:
                    continue

                chave = (titulo, ano)
                reg = series.get(chave)
                if reg is None:
                    reg = {"eps": defaultdict(list), "poster": "", "tmdb": 0, "generos": set(), "grupos": set()}
                    series[chave] = reg
                if poster and not reg["poster"]:
                    reg["poster"] = poster
                if tmdb_id and not reg["tmdb"]:
                    reg["tmdb"] = tmdb_id
                gen = extrair_genero(grupo)
                if gen:
                    reg["generos"].add(gen)
                if grupo:
                    reg["grupos"].add(grupo)
                lista_u = reg["eps"][(temp, ep, versao)]
                if url not in lista_u:
                    lista_u.append(url)
            else:
                # Filme
                titulo = RE_VERSAO_DISPLAY.sub("", tvg_name).strip()
                if not titulo:
                    continue
                reg = filmes.get(titulo)
                if reg is None:
                    reg = {"dub": [], "leg": [], "poster": "", "tmdb": 0, "generos": set(), "grupos": set()}
                    filmes[titulo] = reg
                if poster and not reg["poster"]:
                    reg["poster"] = poster
                if tmdb_id and not reg["tmdb"]:
                    reg["tmdb"] = tmdb_id
                gen = extrair_genero(grupo)
                if gen:
                    reg["generos"].add(gen)
                if grupo:
                    reg["grupos"].add(grupo)
                if url not in reg[versao]:
                    reg[versao].append(url)

    print(f"Parse concluído: {len(filmes)} filmes, {len(series)} séries/animes/doramas.")

    # Mede velocidade dos servidores VOD
    latencias = medir_hosts(amostras_host, pular_teste=not testar_velocidade)

    def chave_url(u: str) -> tuple:
        host = urllib.parse.urlsplit(u).netloc.lower()
        # Evita colocar proxy externo lento na frente de links diretos rápidos
        eh_api = 1 if "/api/stream" in u else 0
        return (eh_api, latencias.get(host, 50000.0))

    # Seleciona os 60 prefixos mais frequentes (com >= 10 ocorrências) para compactação `base:`
    bases_lista = [p for p, cnt in contagem_prefixos.most_common(60) if cnt >= 10]
    mapa_bases = {p: i for i, p in enumerate(bases_lista)}

    os.makedirs(out_dir, exist_ok=True)
    # Limpa arquivos antigos da pasta vod/
    for nome_antigo in os.listdir(out_dir):
        if nome_antigo.endswith(".txt"):
            os.remove(os.path.join(out_dir, nome_antigo))

    # Agrupa filmes e séries por letra
    filmes_por_letra = defaultdict(list)
    for titulo, reg in filmes.items():
        filmes_por_letra[gaveta_letra(titulo)].append((titulo, reg))

    series_por_letra = defaultdict(list)
    for (titulo, ano), reg in series.items():
        series_por_letra[gaveta_letra(titulo)].append((titulo, ano, reg))

    letras = sorted(set(filmes_por_letra.keys()) | set(series_por_letra.keys()), key=lambda x: "" if x == "#" else x)

    linhas_indice = [f"base: {i} {b}" for i, b in enumerate(bases_lista)]
    linhas_busca = []
    linhas_fichas = [f"capa: {CAPA_BASE}"]

    for letra in letras:
        f_lista = sorted(filmes_por_letra.get(letra, []), key=lambda x: normalizar_chave(x[0]))
        s_lista = sorted(series_por_letra.get(letra, []), key=lambda x: (normalizar_chave(x[0]), x[1]))
        linhas_indice.append(f"{letra}\t{len(f_lista)}\t{len(s_lista)}\t0")

        # 1. filmes-<LETRA>.txt
        if f_lista:
            out_f = []
            for titulo, reg in f_lista:
                campos = [titulo]
                for v in ("dub", "leg"):
                    urls = sorted(reg[v], key=chave_url)
                    if urls:
                        comp = ",".join(compactar_url(u, mapa_bases) for u in urls)
                        campos.append(f"{v}={comp}")
                if len(campos) > 1:
                    out_f.append("\t".join(campos))
                    m_ano = RE_ANO.search(titulo)
                    ano = m_ano.group(1) if m_ano else ""
                    linhas_busca.append(f"{titulo}\tf\t{letra}\t{ano}")
                    if reg["poster"] or reg["generos"] or reg["tmdb"]:
                        gens = ",".join(sorted(reg["generos"]))
                        linhas_fichas.append(f"f\t{titulo}\t{reg['tmdb']}\t{reg['poster']}\t{gens}")
            with open(os.path.join(out_dir, f"filmes-{letra}.txt"), "w", encoding="utf-8") as fp:
                fp.write("\n".join(out_f) + "\n")

        # 2. series-<LETRA>.txt e series-<LETRA>-<pedaco>.txt
        if s_lista:
            out_s_idx = []
            pedacos = defaultdict(list)
            for idx_s, (titulo, ano, reg) in enumerate(s_lista):
                pedaco = idx_s // SERIES_POR_PEDACO
                eps_itens = sorted(reg["eps"].items(), key=lambda x: (x[0][0], x[0][1], 0 if x[0][2] == "dub" else 1))
                # Conta episódios únicos (temp, ep)
                qtd_eps = len({(t, e) for (t, e, _) in reg["eps"].keys()})
                out_s_idx.append(f"{titulo}\t{ano}\t{pedaco}\t{qtd_eps}")
                linhas_busca.append(f"{titulo}\ts\t{letra}\t{ano}")
                if reg["poster"] or reg["generos"] or reg["tmdb"]:
                    gens = ",".join(sorted(reg["generos"]))
                    linhas_fichas.append(f"s\t{titulo}\t{reg['tmdb']}\t{reg['poster']}\t{gens}")

                bloco = [f"@{titulo}\t{ano}"]
                for (temp, ep, versao), urls in eps_itens:
                    urls_ord = sorted(urls, key=chave_url)
                    comp = ",".join(compactar_url(u, mapa_bases) for u in urls_ord)
                    bloco.append(f"{temp}\t{ep}\t{versao}\t{comp}")
                pedacos[pedaco].append("\n".join(bloco))

            with open(os.path.join(out_dir, f"series-{letra}.txt"), "w", encoding="utf-8") as fp:
                fp.write("\n".join(out_s_idx) + "\n")
            for p_idx, blocos in pedacos.items():
                with open(os.path.join(out_dir, f"series-{letra}-{p_idx}.txt"), "w", encoding="utf-8") as fp:
                    fp.write("\n".join(blocos) + "\n")

    # 3. indice.txt, busca.txt, fichas.txt
    with open(os.path.join(out_dir, "indice.txt"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(linhas_indice) + "\n")
    with open(os.path.join(out_dir, "busca.txt"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(linhas_busca) + "\n")
    with open(os.path.join(out_dir, "fichas.txt"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(linhas_fichas) + "\n")

    # 4. destaques.txt (fileiras da tela inicial)
    linhas_dest = [f"capa: {CAPA_BASE}"]

    def adicionar_fila_filmes(nome_fila: str, filtro, limite: int = 24):
        achados = []
        for titulo, reg in filmes.items():
            if not reg["poster"]:
                continue
            if filtro(titulo, reg):
                m_ano = RE_ANO.search(titulo)
                ano = m_ano.group(1) if m_ano else ""
                achados.append((titulo, gaveta_letra(titulo), ano, reg["poster"], len(reg["dub"])))
        # Prioriza títulos com mais fontes dubladas
        achados.sort(key=lambda x: (-x[4], x[0]))
        if achados:
            linhas_dest.append(f"fila\t{nome_fila}")
            for titulo, let, ano, poster, _ in achados[:limite]:
                linhas_dest.append(f"f\t{titulo}\t{let}\t{ano}\t{poster}")

    def adicionar_fila_series(nome_fila: str, filtro, limite: int = 24):
        achados = []
        for (titulo, ano), reg in series.items():
            if not reg["poster"]:
                continue
            if filtro(titulo, reg):
                achados.append((titulo, gaveta_letra(titulo), ano, reg["poster"], len(reg["eps"])))
        achados.sort(key=lambda x: (-x[4], x[0]))
        if achados:
            linhas_dest.append(f"fila\t{nome_fila}")
            for titulo, let, ano, poster, _ in achados[:limite]:
                linhas_dest.append(f"s\t{titulo}\t{let}\t{ano}\t{poster}")

    adicionar_fila_filmes("Em Alta", lambda t, r: "Filmes - Em Alta" in r["grupos"])
    adicionar_fila_filmes("Lançamentos 2026", lambda t, r: any("Lançamentos" in g for g in r["grupos"]) or "(2026)" in t)
    adicionar_fila_series("Séries em Destaque", lambda t, r: "Séries" in r["generos"])
    adicionar_fila_filmes("Ação", lambda t, r: "Ação" in r["generos"])
    adicionar_fila_filmes("Comédia", lambda t, r: "Comédia" in r["generos"])
    adicionar_fila_filmes("Terror e Suspense", lambda t, r: "Terror" in r["generos"] or "Suspense" in r["generos"])
    adicionar_fila_series("Animes Populares", lambda t, r: "Animes" in r["generos"])

    with open(os.path.join(out_dir, "destaques.txt"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(linhas_dest) + "\n")

    arqs = [a for a in os.listdir(out_dir) if a.endswith(".txt")]
    total_mb = sum(os.path.getsize(os.path.join(out_dir, a)) for a in arqs) / (1024 * 1024)
    print(f"Catálogo VOD fatiado gerado em {out_dir}: {len(arqs)} arquivos ({total_mb:.2f} MB) em {time.monotonic() - t0:.1f}s.")


if __name__ == "__main__":
    import sys
    gerar_vod_fatiado(testar_velocidade="--sem-teste" not in sys.argv)
