import os
import re
import urllib.request
import urllib.parse
import collections
import json
import unicodedata
import sys
import concurrent.futures
import time

# URLs oficiais do Banco do Saimo
URL_CANAIS = "https://canais-saimo.pages.dev/catalogo.txt"
URL_DESATIVADAS = "https://saimo-tv.pages.dev/fontes-desativadas.json"
BASE_FILMES = "https://filmes-saimo.pages.dev/"
URL_FICHAS = BASE_FILMES + "fichas.txt"
URL_DESTAQUES = BASE_FILMES + "destaques.txt"
URL_DORAMAS = BASE_FILMES + "redeflix/links-doramas.txt"
URL_ANIMES = BASE_FILMES + "redeflix/links-animes.txt"

_fontes_desativadas_cache = None

def carregar_fontes_desativadas():
    global _fontes_desativadas_cache
    if _fontes_desativadas_cache is not None:
        return _fontes_desativadas_cache
    _fontes_desativadas_cache = {"desativado.invalid"}
    raw = baixar_texto(URL_DESATIVADAS)
    if raw:
        try:
            dados = json.loads(raw)
            for d in dados.get("desativados", []):
                if d and d.strip():
                    _fontes_desativadas_cache.add(d.strip().lower())
            print(f"Lista de bloqueio oficial do Saimo carregada: {len(_fontes_desativadas_cache)} domínios/regras.")
        except Exception as e:
            print(f"Aviso ao carregar fontes desativadas: {e}")
    return _fontes_desativadas_cache

def eh_fonte_desativada(url):
    if not url:
        return True
    u = url.lower()
    if "desativado.invalid" in u:
        return True
    bloqueados = carregar_fontes_desativadas()
    for b in bloqueados:
        if b in u:
            return True
    return False

# Caminhos
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OUTPUT_M3U = os.path.join(REPO_ROOT, "canais.m3u")       # Unificada
OUTPUT_AOVIVO = os.path.join(REPO_ROOT, "aovivo.m3u")   # Somente canais ao vivo
OUTPUT_VOD = os.path.join(REPO_ROOT, "vod.m3u")         # Filmes e Series
OUTPUT_RESTRITOS = os.path.join(REPO_ROOT, "restritos.txt") # Canais Adultos +18 (com senha)
OUTPUT_ADULTOS = os.path.join(REPO_ROOT, "adultos.m3u")     # Lista M3U Adulto separada
FILMES_M3U_IN = os.path.join(REPO_ROOT, "filmes.m3u")

ORDEM_CATEGORIAS_CANAIS = [
    "TV Aberta",
    "Filmes e Séries",
    "Esportes",
    "Jogos do Dia",
    "PPV",
    "Reality Shows",
    "24 Horas",
    "Infantil",
    "Documentários",
    "Notícias",
    "Religiosos",
    "Músicas",
    "Variedades",
    "Pluto TV"
]

def baixar_texto(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"Aviso ao baixar {url}: {e}")
        return ""

def normalizar_texto(texto):
    t = unicodedata.normalize("NFD", texto.strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")  # remove acentos
    t = re.sub(r'[^a-z0-9()]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def candidatos_busca(titulo):
    """Variações do título para achar a ficha/capa (prefixo de saga, número final, ano)."""
    base = re.sub(r'\s*\((?:Dublado|Legendado|Opção|Nacional)[^)]*\)', '', titulo, flags=re.IGNORECASE).strip()
    cands = [base]
    sem_ano = re.sub(r'\s*\(\d{4}\)\s*$', '', base).strip()
    sem_prefixo = re.sub(r'^\s*\d{1,2}\s*[-–—.]\s*', '', sem_ano).strip()
    sem_sufixo = re.sub(r'\s+(?:\d{1,2}|0000)$', '', sem_prefixo).strip()
    for c in (sem_ano, sem_prefixo, sem_sufixo):
        if c and c not in cands:
            cands.append(c)
    return cands

_indice_fuzzy = {}
_cache_fuzzy = {}

def _buscar_fuzzy(fichas, titulo):
    """Aproxima grafias diferentes (ex.: camera/camara). Só filmes com 1ª palavra igual e similaridade alta."""
    import difflib
    if id(fichas) not in _indice_fuzzy:
        idx = collections.defaultdict(list)
        for k in fichas:
            idx[k.split(" ")[0]].append(k)
        _indice_fuzzy.clear()
        _indice_fuzzy[id(fichas)] = idx
    chave = normalizar_texto(titulo)
    if chave in _cache_fuzzy:
        return _cache_fuzzy[chave]
    achado = None
    if len(chave) >= 8:
        pool = _indice_fuzzy[id(fichas)].get(chave.split(" ")[0], [])
        m = difflib.get_close_matches(chave, pool, n=1, cutoff=0.9)
        if m:
            achado = fichas[m[0]]
    _cache_fuzzy[chave] = achado
    return achado

def buscar_ficha(fichas, titulo):
    cands = candidatos_busca(titulo)
    for c in cands:
        info = fichas.get(normalizar_texto(c))
        if info:
            return info
    return _buscar_fuzzy(fichas, cands[-1])

def capa_padrao(titulo):
    """Quando não há capa oficial no TMDB nem nas fichas, deixa vazio para usar o design nativo do app."""
    return ""

# ---- Capas pelo TMDB (chave oficial integrada como fallback + suporte a env var) ----
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "").strip() or "15d2ea6d0dc1d476efbca3eba2b9bbfb"
CACHE_CAPAS = os.path.join(REPO_ROOT, "data", "capas_cache.json")
_cache_capas = None

def _carregar_cache_capas():
    global _cache_capas
    if _cache_capas is None:
        try:
            with open(CACHE_CAPAS, "r", encoding="utf-8") as f:
                _cache_capas = json.load(f)
        except Exception:
            _cache_capas = {}
    return _cache_capas

def salvar_cache_capas():
    if _cache_capas is not None and TMDB_API_KEY:
        os.makedirs(os.path.dirname(CACHE_CAPAS), exist_ok=True)
        with open(CACHE_CAPAS, "w", encoding="utf-8") as f:
            json.dump(_cache_capas, f, ensure_ascii=False, indent=0, sort_keys=True)

def capa_tmdb(titulo):
    """Busca a capa no TMDB pelo nome (sem conteúdo adulto). Resultado fica em cache, inclusive 'não achei'."""
    if not TMDB_API_KEY:
        return ""
    cache = _carregar_cache_capas()
    base = re.sub(r'\s*[([]\s*(?:leg|dub|dublado|legendado|dual|national|pt-br|pt-pt|eng|legendada|opção\s*\d*)\s*[)\]]', '', titulo, flags=re.IGNORECASE).strip()
    base = re.sub(r'\b(?:4K|UHD|HD|FHD|SD|BluRay|BDRip|WEB-DL|WEBRip|HDTV|DVDRip|CAM|HDR|SDR|Dublado|Legendado|Nacional|Opção\s*\d*)\b', '', base, flags=re.IGNORECASE).strip()
    ano = extrair_ano(base)
    nome = re.sub(r'\s*[([]\d{4}[)\]]\s*$', '', base).strip()
    nome = re.sub(r'^\s*\d{1,2}\s*[-–—.]\s*', '', nome).strip()
    chave = normalizar_texto(nome) + (f"|{ano}" if ano else "")
    if chave in cache:
        return cache[chave]
    poster = ""
    try:
        url = ("https://api.themoviedb.org/3/search/multi?include_adult=false&language=pt-BR&query="
               + urllib.parse.quote(nome) + "&api_key=" + TMDB_API_KEY)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=8) as r:
            resultados = json.loads(r.read().decode("utf-8")).get("results", [])
        validos = [x for x in resultados if x.get("media_type") in ("movie", "tv") and x.get("poster_path") and not x.get("adult")]
        if ano:
            com_ano = [x for x in validos if str(x.get("release_date") or x.get("first_air_date") or "").startswith(str(ano))]
            validos = com_ano or validos
        if validos:
            poster = "https://image.tmdb.org/t/p/w342" + validos[0]["poster_path"]
        elif not validos and resultados:
            for x in resultados:
                if x.get("poster_path"):
                    poster = "https://image.tmdb.org/t/p/w342" + x["poster_path"]
                    break
    except Exception as e:
        print(f"Aviso TMDB ({nome}): {e}")
        return ""  # falha de rede: não grava em cache para tentar de novo depois
    cache[chave] = poster
    return poster

def extrair_ano(texto):
    m = re.search(r'\((\d{4})\)', texto)
    return int(m.group(1)) if m else None

def ordenar_fontes_contingencia(urls):
    fontes_validas = [u.strip() for u in urls if u.strip().startswith('http') and not eh_fonte_desativada(u)]
    if not fontes_validas:
        return []
    def rank(u):
        u_low = u.strip().lower()
        if not u_low.startswith("http"):
            return -100
        # 1. HLS limpo com chunks .ts oficiais Apple ou servidores de entrega direta
        if any(h in u_low for h in ["comprarebom.xyz", "45.172.0.116", "r2.dev"]):
            return 100
        # 2. Servidores MP4 diretos autenticados com credenciais (300MB - 800MB)
        if any(h in u_low for h in ["dbonline.tech-cdn.top", "vdcdpstop", "aponteiecair", "corruptx", "fenixhub", "apiflix"]):
            return 90
        # 3. API de stream do Saimo
        if "saimo-tv.pages.dev/api/stream" in u_low:
            return 70
        # 4. HLS com chunks .webp
        if any(h in u_low for h in ["maximaimg.online", "mywallpaper", "pixel-sus"]):
            return 50
        # 5. Embedplayer / Oneplayer (precisam de proxy para não dar security error)
        if any(h in u_low for h in ["embedplayer2.xyz", "oneplayer.site"]):
            return 40
        return 10
    fontes_ordenadas = sorted(fontes_validas, key=rank, reverse=True)
    return fontes_ordenadas[:2]

def carregar_fichas():
    raw = baixar_texto(URL_FICHAS)
    fichas = {}
    capa_base = "https://image.tmdb.org/t/p/w342"
    for line in raw.splitlines():
        if line.startswith("capa:"):
            capa_base = line.split(":", 1)[1].strip()
            continue
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 5:
            tipo, tit, tmdb_id, poster, generos = parts[0], parts[1], parts[2], parts[3], parts[4]
            gens = [g.strip() for g in generos.split(",") if g.strip()]
            ano = extrair_ano(tit)
            
            info = {
                "tipo": tipo,
                "titulo": tit,
                "poster": f"{capa_base}{poster}" if poster else "",
                "generos": gens,
                "ano": ano
            }
            fichas[normalizar_texto(tit)] = info
            tit_sem_ano = re.sub(r'\s*\(\d{4}\)\s*$', '', tit)
            ch_sem = normalizar_texto(tit_sem_ano)
            if ch_sem not in fichas:
                fichas[ch_sem] = info
    print(f"Fichas carregadas: {len(fichas)} entradas.")
    return fichas

def carregar_destaques():
    raw = baixar_texto(URL_DESTAQUES)
    destaques = collections.defaultdict(list)
    fila_atual = "Em alta"
    for line in raw.splitlines():
        if line.startswith("fila\t"):
            fila_atual = line.split("\t", 1)[1].strip()
        elif line.startswith("f\t") or line.startswith("s\t"):
            parts = line.split("\t")
            if len(parts) >= 2:
                tit = parts[1].strip()
                destaques[fila_atual].append({
                    "titulo": tit,
                    "letra": parts[2].strip() if len(parts) > 2 else "",
                    "ano": extrair_ano(tit) or (int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None),
                    "chave_sem_ano": normalizar_texto(re.sub(r'\s*\(\d{4}\)\s*$', '', tit))
                })
    print(f"Destaques carregados: {len(destaques)} filas.")
    return destaques


def normalizar_nome_canal(nome):
    if not nome:
        return ""
    n = unicodedata.normalize("NFD", nome.lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")

    # Extrair sufixos de identificação única que NÃO devem ser fundidos
    tags_unicas = []
    # Temporada / Season / Series: S01, T01, S02, Temporada 1, etc.
    m_temp = re.findall(r'\b(?:s|t|temporada|season)\s*(\d+)\b', n)
    for t in m_temp:
        tags_unicas.append(f"s{int(t):02d}")

    # Pack: Pack 01, Pack 02, etc.
    m_pack = re.findall(r'\bpack\s*(\d+)\b', n)
    for p in m_pack:
        tags_unicas.append(f"pack{int(p):02d}")

    # Parte / Vol / Volume: Parte 1, Vol 2
    m_parte = re.findall(r'\b(?:parte|vol|volume|pt)\s*(\d+)\b', n)
    for p in m_parte:
        tags_unicas.append(f"pt{int(p):02d}")

    # Remove colchetes e parênteses
    n = re.sub(r'\[.*?\]|\(.*?\)', ' ', n)
    # Remove numeração inicial (ex: "01 - Globo" -> "Globo")
    n = re.sub(r'^\s*\d+[\s\-\.]+', '', n)
    # Remove termos de qualidade e resoluções
    n = re.sub(r'\b(4k|uhd|fhd|hd|sd|h265|hevc|raw|plus|\+|br|brazil|brasil|aovivo|ao vivo)\b', ' ', n)
    n = re.sub(r'[^a-z0-9]+', ' ', n).strip()

    if tags_unicas:
        n = f"{n} {' '.join(tags_unicas)}".strip()

    return n

def eh_conteudo_adulto(nome, categoria=""):
    """
    Detecta se um canal ou conteúdo é adulto (+18).
    Utiliza o mesmo padrão regex nativo do aplicativo (Lss0;->f), excluindo 'Adult Swim'.
    """
    texto = f"{nome} {categoria}".lower().replace("adult swim", "")
    padrao = r'(?:^|[^a-z0-9])(?:adult|adulto|adultos|xxx|porn|porno|sexo|sex|erotic|erotico|hentai|playboy|sexy\s*hot|venus|sextreme|hustler|redlight|brazzers|private|for\s*man|penthouse|babes|dorcel)(?:[^a-z0-9]|$)|\+18|18\+'
    return bool(re.search(padrao, texto))

def normalizar_categoria(cat, nome_canal=""):
    cat_str = (cat or "").strip()
    nome_str = (nome_canal or "").strip()
    c_low = cat_str.lower()
    n_low = nome_str.lower()

    # 1. Conteúdo Adulto (+18)
    if any(k in c_low for k in ["adult", "xxx", "porn", "porno", "sex", "+18", "18+"]) or any(k in n_low for k in ["+18", "18+", "playboy", "venus", "sextreme", "hustler"]):
        return "Adulto"

    # 2. 24 Horas: prioridade absoluta para não misturar com Filmes e Séries ou Variedades
    if "[24h]" in n_low or "24 horas" in n_low or "24h" in c_low or "24 horas" in c_low:
        return "24 Horas"

    # 3. Jogos do Dia: partidas de futebol ao vivo do dia
    if "jogos do dia" in c_low or "jogos" in c_low or re.match(r"^\[\d{2}:\d{2}\]", nome_str):
        return "Jogos do Dia"

    # 4. Reality Shows (A Fazenda, BBB, etc.) - nunca misturar com Músicas
    if "reality" in c_low or "fazenda" in n_low or "bbb" in n_low or "big brother" in n_low:
        return "Reality Shows"

    # 5. PPV (Pay-per-view, Combate, etc.)
    if "ppv" in c_low or "combate" in c_low or "pay-per-view" in c_low or "combate" in n_low or "bjj" in n_low:
        return "PPV"

    # 6. Esportes: inclui Premiere (a pedido do usuário), SporTV, ESPN, DAZN, NBA, Futsal, etc.
    if any(k in c_low for k in ["esporte", "sport", "futebol", "espn", "sportv", "dazn", "nba", "campeonatos", "futsal", "premiere"]) or any(k in n_low for k in ["premiere", "sportv", "espn", "dazn", "band sports", "tnt sports", "pfc"]):
        return "Esportes"

    # 7. Religiosos
    if "religio" in c_low or any(k in n_low for k in ["aparecida", "pai eterno", "cancao nova", "evangelizar", "redavida", "redegospel"]):
        return "Religiosos"

    # 8. TV Aberta (Globo, Record, SBT, Bandeirantes, etc. - canais 4K abertos ficam aqui)
    if any(k in c_low for k in ["aberto", "aberta", "local", "nacional", "globo", "record", "sbt", "band"]) or any(k in n_low for k in ["globo", "record", "sbt", "bandeirantes", "band tv", "cultura", "redetv", "rede tv"]):
        return "TV Aberta"

    # 9. Filmes e Séries (HBO, Telecine, Max, Warner, TNT, AXN, etc. - sem canais 24h)
    if any(k in c_low for k in ["filme", "serie", "cinema", "telecine", "hbo", "paramount", "disney", "prime video", "apple tv", "max", "legendado"]) or any(k in n_low for k in ["telecine", "hbo", "megapix", "tnt", "space", "axn", "warner", "universal tv", "paramount", "sony"]):
        return "Filmes e Séries"

    # 10. Infantil
    if any(k in c_low for k in ["infantil", "infantis", "kids", "desenho", "animacao"]) or any(k in n_low for k in ["cartoon", "gloob", "nickelodeon", "nick jr", "disney jr", "discovery kids", "tooncast", "boomerang"]):
        return "Infantil"

    # 11. Documentários
    if any(k in c_low for k in ["doc", "discovery", "history", "animal", "geographic"]) or any(k in n_low for k in ["discovery", "history", "animal planet", "nat geo", "national geographic"]):
        return "Documentários"

    # 12. Notícias
    if any(k in c_low for k in ["noticia", "news", "jornal", "cnn", "bandnews"]) or any(k in n_low for k in ["globonews", "cnn brasil", "bandnews", "jovem pan news", "record news"]):
        return "Notícias"

    # 13. Músicas (apenas música pura, sem 'show' genérico)
    if any(k in c_low for k in ["musica", "music", "mtv", "multishow", "bis"]) or any(k in n_low for k in ["multishow", "bis", "mtv", "trace"]):
        return "Músicas"

    # 14. Pluto TV
    if "pluto" in c_low:
        return "Pluto TV"

    # Não criar abas para tags técnicas de resolução ou idioma (4K, Legendados)
    if any(k in c_low for k in ["4k", "fhdr", "uhd", "legendado", "legendados"]):
        return "Variedades"

    # 15. Outras categorias: manter a categoria original limpa em vez de forçar tudo em Variedades
    limpo = re.sub(r"^canais\s*\|\s*", "", cat_str, flags=re.IGNORECASE).strip()
    return limpo if limpo else "Variedades"

def extrair_credenciais_xtream(url):
    """Detecta e extrai credenciais de servidores Xtream Codes."""
    try:
        p = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(p.query)
        user = qs.get("username", [""])[0]
        pwd = qs.get("password", [""])[0]
        if user and pwd:
            base = f"{p.scheme}://{p.netloc}"
            return {"base": base, "user": user, "pwd": pwd}
    except Exception:
        pass
    return None

def carregar_e_fundir_listas_extras(canais_por_cat, canais_adultos=None):
    """
    Lê listas extras de data/listas_extras.txt ou da variável de ambiente M3U_EXTRA_URL.
    Une canais com os do catálogo oficial (por nome) ou adiciona novos canais.
    Para servidores Xtream Codes, utiliza a API nativa (action=get_live_streams) em segundos.
    Separa canais ao vivo de filmes/séries (VOD) e canais adultos (+18 para restritos.txt).
    """
    if canais_adultos is None:
        canais_adultos = []

    caminho_arquivo = os.path.join(REPO_ROOT, "data", "listas_extras.txt")
    urls_para_processar = []

    if os.path.exists(caminho_arquivo):
        with open(caminho_arquivo, "r", encoding="utf-8", errors="ignore") as f:
            for linha in f:
                l = linha.strip()
                if l and not l.startswith("#"):
                    urls_para_processar.append(l)

    env_url = os.environ.get("M3U_EXTRA_URL", "").strip()
    if env_url:
        for u in env_url.split(","):
            u_clean = u.strip()
            if u_clean and u_clean not in urls_para_processar:
                urls_para_processar.append(u_clean)

    if not urls_para_processar:
        return canais_por_cat, canais_adultos

    print(f"Carregando e fundindo {len(urls_para_processar)} lista(s) extra(s)...")

    aliases = {
        "globo sao paulo": "globo sp",
        "globo rio": "globo rj",
        "globo minas": "globo mg",
        "globo brasilia": "globo df",
        "sportv 1": "sportv",
        "premiere club": "premiere",
        "espn 1": "espn"
    }

    mapa_canais = {}
    ordem_global = 9000
    for cat, lista in canais_por_cat.items():
        for ch in lista:
            norm = normalizar_nome_canal(ch["nome"])
            if norm:
                norm = aliases.get(norm, norm)
                mapa_canais[norm] = ch

    mapa_adultos = {}
    ordem_global_adulto = 9500
    for ch in canais_adultos:
        norm = normalizar_nome_canal(ch["nome"])
        if norm:
            mapa_adultos[norm] = ch

    total_adicionados = 0
    total_fundidos = 0
    total_adultos = 0

    for item in urls_para_processar:
        xtream_info = extrair_credenciais_xtream(item)
        if xtream_info:
            print(f"Detectado Xtream Codes: {xtream_info['base']} (Usuário: {xtream_info['user']})")
            base = xtream_info["base"]
            user = xtream_info["user"]
            pwd = xtream_info["pwd"]
            try:
                cache_file = os.path.join(REPO_ROOT, "data", "xtream_cache.json")
                cat_map = {}
                url_cat = f"{base}/player_api.php?username={urllib.parse.quote(user)}&password={urllib.parse.quote(pwd)}&action=get_live_categories"
                texto_cat = baixar_texto(url_cat)
                if texto_cat:
                    try:
                        for c in json.loads(texto_cat):
                            cat_map[str(c.get("category_id"))] = c.get("category_name", "")
                    except Exception:
                        pass

                url_live = f"{base}/player_api.php?username={urllib.parse.quote(user)}&password={urllib.parse.quote(pwd)}&action=get_live_streams"
                texto_live = baixar_texto(url_live)
                streams = []
                if texto_live:
                    try:
                        streams = json.loads(texto_live)
                        if streams:
                            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                            with open(cache_file, "w", encoding="utf-8") as cf:
                                json.dump({"categories": cat_map, "streams": streams}, cf, ensure_ascii=False)
                    except Exception as ej:
                        print(f"Aviso ao decodificar JSON ao vivo: {ej}")

                if not streams:
                    if os.path.exists(cache_file):
                        print(f"Aviso: Não foi possível obter canais online de {base}. Usando cache local permanente ({cache_file})...")
                        try:
                            with open(cache_file, "r", encoding="utf-8") as cf:
                                cache_data = json.load(cf)
                                if not cat_map:
                                    cat_map = cache_data.get("categories", {})
                                streams = cache_data.get("streams", [])
                        except Exception as ec:
                            print(f"Erro ao ler cache xtream: {ec}")

                if not streams:
                    print(f"Aviso: Nenhum canal obtido para {base}.")
                    continue
                print(f"Recebidos {len(streams)} canais ao vivo da lista extra Xtream.")

                for s in streams:
                    nome_canal = s.get("name", "").strip()
                    sid = s.get("stream_id")
                    if not nome_canal or not sid:
                        continue
                    logo_canal = s.get("stream_icon", "").strip()
                    cat_id = str(s.get("category_id", ""))
                    cat_bruta = cat_map.get(cat_id, "") or "Variedades"
                    url_canal = f"{base}/live/{user}/{pwd}/{sid}.ts"

                    # Identificar se é canal adulto (+18)
                    is_adult = (cat_id == "12") or ("adult" in cat_bruta.lower()) or eh_conteudo_adulto(nome_canal, cat_bruta)

                    if is_adult:
                        norm = normalizar_nome_canal(nome_canal)
                        if norm in mapa_adultos:
                            ch_alvo = mapa_adultos[norm]
                            urls_existentes = {f["url"].lower().strip() for f in ch_alvo["fontes"]}
                            if url_canal.lower().strip() not in urls_existentes:
                                ch_alvo["fontes"].append({
                                    "url": url_canal,
                                    "referer": "",
                                    "agente": "Mozilla/5.0",
                                    "qualidade": ""
                                })
                                if not ch_alvo.get("logo") and logo_canal:
                                    ch_alvo["logo"] = logo_canal
                        else:
                            novo_ch = {
                                "nome": nome_canal,
                                "categoria": "Adulto",
                                "logo": logo_canal,
                                "fontes": [{
                                    "url": url_canal,
                                    "referer": "",
                                    "agente": "Mozilla/5.0",
                                    "qualidade": ""
                                }],
                                "ordem": ordem_global_adulto
                            }
                            ordem_global_adulto += 1
                            canais_adultos.append(novo_ch)
                            mapa_adultos[norm] = novo_ch
                            total_adultos += 1
                        continue

                    # Canal normal
                    cat_normalizada = normalizar_categoria(cat_bruta, nome_canal)
                    norm = normalizar_nome_canal(nome_canal)
                    norm = aliases.get(norm, norm)

                    if norm in mapa_canais:
                        ch_alvo = mapa_canais[norm]
                        urls_existentes = {f["url"].lower().strip() for f in ch_alvo["fontes"]}
                        if url_canal.lower().strip() not in urls_existentes:
                            ch_alvo["fontes"].append({
                                "url": url_canal,
                                "referer": "",
                                "agente": "Mozilla/5.0",
                                "qualidade": ""
                            })
                            total_fundidos += 1
                            if not ch_alvo.get("logo") and logo_canal:
                                ch_alvo["logo"] = logo_canal
                    else:
                        novo_ch = {
                            "nome": nome_canal,
                            "categoria": cat_normalizada,
                            "logo": logo_canal,
                            "fontes": [{
                                "url": url_canal,
                                "referer": "",
                                "agente": "Mozilla/5.0",
                                "qualidade": ""
                            }],
                            "ordem": ordem_global
                        }
                        ordem_global += 1
                        canais_por_cat[cat_normalizada].append(novo_ch)
                        mapa_canais[norm] = novo_ch
                        total_adicionados += 1
                continue
            except Exception as e:
                print(f"Erro ao processar Xtream {item}: {e}")

        # Fallback para listas M3U normais
        conteudo = ""
        if os.path.exists(item):
            with open(item, "r", encoding="utf-8", errors="ignore") as f:
                conteudo = f.read()
        elif item.startswith("http://") or item.startswith("https://"):
            conteudo = baixar_texto(item)
        else:
            rel = os.path.join(REPO_ROOT, item)
            if os.path.exists(rel):
                with open(rel, "r", encoding="utf-8", errors="ignore") as f:
                    conteudo = f.read()

        if not conteudo:
            print(f"Aviso: Não foi possível baixar/ler lista extra: {item}")
            continue

        linhas = conteudo.splitlines()
        extinf_atual = None
        ref_atual = ""
        ua_atual = ""

        for linha in linhas:
            l = linha.strip()
            if not l:
                continue
            if l.startswith("#EXTINF:"):
                extinf_atual = l
                ref_atual = ""
                ua_atual = ""
            elif l.startswith("#EXTVLCOPT:http-referrer="):
                ref_atual = l.split("=", 1)[1].strip()
            elif l.startswith("#EXTVLCOPT:http-user-agent="):
                ua_atual = l.split("=", 1)[1].strip()
            elif l.startswith("http://") or l.startswith("https://"):
                url_canal = l
                u_low = url_canal.lower()
                if any(k in u_low for k in ["/movie/", "/series/", ".mp4", ".mkv", ".avi"]):
                    extinf_atual = None
                    continue

                if extinf_atual:
                    m_tvg_name = re.search(r'tvg-name="([^"]*)"', extinf_atual, re.IGNORECASE)
                    m_group = re.search(r'group-title="([^"]*)"', extinf_atual, re.IGNORECASE)
                    m_logo = re.search(r'tvg-logo="([^"]*)"', extinf_atual, re.IGNORECASE)

                    pos_virgula = extinf_atual.rfind(",")
                    titulo_extinf = extinf_atual[pos_virgula+1:].strip() if pos_virgula != -1 else ""

                    nome_canal = (m_tvg_name.group(1).strip() if m_tvg_name else "") or titulo_extinf or "Canal"
                    cat_canal = (m_group.group(1).strip() if m_group else "") or "Variedades"
                    logo_canal = (m_logo.group(1).strip() if m_logo else "")

                    is_adult = ("adult" in cat_canal.lower()) or eh_conteudo_adulto(nome_canal, cat_canal)

                    if is_adult:
                        norm = normalizar_nome_canal(nome_canal)
                        if norm in mapa_adultos:
                            ch_alvo = mapa_adultos[norm]
                            urls_existentes = {f["url"].lower().strip() for f in ch_alvo["fontes"]}
                            if url_canal.lower().strip() not in urls_existentes:
                                ch_alvo["fontes"].append({
                                    "url": url_canal,
                                    "referer": ref_atual,
                                    "agente": ua_atual,
                                    "qualidade": ""
                                })
                                if not ch_alvo.get("logo") and logo_canal:
                                    ch_alvo["logo"] = logo_canal
                        else:
                            novo_ch = {
                                "nome": nome_canal,
                                "categoria": "Adulto",
                                "logo": logo_canal,
                                "fontes": [{
                                    "url": url_canal,
                                    "referer": ref_atual,
                                    "agente": ua_atual,
                                    "qualidade": ""
                                }],
                                "ordem": ordem_global_adulto
                            }
                            ordem_global_adulto += 1
                            canais_adultos.append(novo_ch)
                            mapa_adultos[norm] = novo_ch
                            total_adultos += 1
                    else:
                        cat_normalizada = normalizar_categoria(cat_canal, nome_canal)
                        norm = normalizar_nome_canal(nome_canal)
                        norm = aliases.get(norm, norm)

                        if norm in mapa_canais:
                            ch_alvo = mapa_canais[norm]
                            urls_existentes = {f["url"].lower().strip() for f in ch_alvo["fontes"]}
                            if url_canal.lower().strip() not in urls_existentes:
                                ch_alvo["fontes"].append({
                                    "url": url_canal,
                                    "referer": ref_atual,
                                    "agente": ua_atual,
                                    "qualidade": ""
                                })
                                total_fundidos += 1
                                if not ch_alvo.get("logo") and logo_canal:
                                    ch_alvo["logo"] = logo_canal
                        else:
                            novo_ch = {
                                "nome": nome_canal,
                                "categoria": cat_normalizada,
                                "logo": logo_canal,
                                "fontes": [{
                                    "url": url_canal,
                                    "referer": ref_atual,
                                    "agente": ua_atual,
                                    "qualidade": ""
                                }],
                                "ordem": ordem_global
                            }
                            ordem_global += 1
                            canais_por_cat[cat_normalizada].append(novo_ch)
                            mapa_canais[norm] = novo_ch
                            total_adicionados += 1

                extinf_atual = None

    print(f"Fusão de listas extras concluída: {total_fundidos} fontes fundidas, {total_adicionados} novos canais normais, {total_adultos} canais adultos separados (+18).")
    return canais_por_cat, canais_adultos

def processar_canais():
    texto_bruto = baixar_texto(URL_CANAIS)
    linhas = texto_bruto.splitlines()
    canais_por_cat = collections.defaultdict(list)
    canais_adultos = []

    canal_atual = None
    categoria_atual = "Geral"
    logo_atual = ""
    fontes = []
    fonte_atual = None
    ordem_global = 0

    def salvar_canal():
        nonlocal ordem_global
        if not canal_atual or not fontes:
            return
        fontes_validas = [f for f in fontes if not eh_fonte_desativada(f["url"])]
        if not fontes_validas:
            return
        for f in fontes_validas:
            u_low = f["url"].lower()
            if any(k in u_low for k in ["s25-cloudfront-net", "cdn8embed", "cdn9embed", "cdn10embed", "playerembedcanais"]) and not f.get("referer"):
                f["referer"] = "https://playerembedcanais.site/"
                f["agente"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        
        is_adult = eh_conteudo_adulto(canal_atual, categoria_atual)
        cat_normalizada = normalizar_categoria(categoria_atual, canal_atual)
        cat_final = "Adulto" if is_adult else cat_normalizada
        item_canal = {
            "nome": canal_atual,
            "categoria": cat_final,
            "logo": logo_atual,
            "fontes": fontes_validas,
            "ordem": ordem_global
        }
        ordem_global += 1
        if is_adult:
            canais_adultos.append(item_canal)
        else:
            canais_por_cat[cat_final].append(item_canal)

    for linha in linhas:
        l = linha.strip()
        if not l or l.startswith("#"):
            continue
        pos = l.find(":")
        if pos == -1:
            continue
        chave = l[:pos].strip().lower()
        valor = l[pos + 1:].strip()

        if chave == "canal":
            salvar_canal()
            canal_atual = re.sub(r'(?i)saimo', '', valor).strip()
            categoria_atual = "Geral"
            logo_atual = ""
            fontes = []
            fonte_atual = None
        elif chave == "categoria":
            categoria_atual = valor
        elif chave == "logo":
            logo_atual = valor
        elif chave == "fonte":
            fonte_atual = {"url": valor, "referer": "", "agente": "", "qualidade": ""}
            fontes.append(fonte_atual)
        elif chave == "referer" and fonte_atual:
            fonte_atual["referer"] = valor
        elif chave == "agente" and fonte_atual:
            fonte_atual["agente"] = valor
        elif chave == "qualidade" and fonte_atual:
            fonte_atual["qualidade"] = valor

    salvar_canal()
    canais_por_cat, canais_adultos = carregar_e_fundir_listas_extras(canais_por_cat, canais_adultos)
    return canais_por_cat, canais_adultos

def formatar_canais_m3u(canais_por_cat):
    resultado = []
    todas_categorias = [c for c in ORDEM_CATEGORIAS_CANAIS if c in canais_por_cat]
    outras_categorias = [c for c in sorted(canais_por_cat.keys()) if c not in ORDEM_CATEGORIAS_CANAIS]
    ordem_final = todas_categorias + outras_categorias

    total_canais = 0
    total_links = 0
    numero_canal = 1

    for cat in ordem_final:
        lista_canais = canais_por_cat[cat]
        # Padrao oficial do Saimo: preservar a ordem original do catalogo em vez de alfabetica
        lista_canais.sort(key=lambda x: x.get("ordem", 0))

        for ch in lista_canais:
            total_canais += 1
            num_fontes = len(ch["fontes"])
            chno_attr = f' tvg-chno="{numero_canal}"'
            for idx, f in enumerate(ch["fontes"]):
                total_links += 1
                sufixo = f" (Opção {idx + 1})" if num_fontes > 1 else ""
                nome_final = ch["nome"] + sufixo
                logo_attr = f' tvg-logo="{ch["logo"]}"' if ch["logo"] else ""
                group_attr = f' group-title="{cat}"'
                
                ref = f.get("referer", "").strip()
                ua = f.get("agente", "").strip() or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                
                header_attrs = ""
                if ref:
                    header_attrs += f' http-referrer="{ref}"'
                if f.get("agente"):
                    header_attrs += f' http-user-agent="{ua}"'

                resultado.append(f'#EXTINF:-1{chno_attr} tvg-name="{ch["nome"]}" tvg-type="live"{logo_attr}{group_attr}{header_attrs},{nome_final}')

                if ref:
                    resultado.append(f'#EXTVLCOPT:http-referrer={ref}')
                    resultado.append(f'#EXTVLCOPT:http-user-agent={ua}')
                    resultado.append(f'#EXTHTTP:{{"Referer":"{ref}","User-Agent":"{ua}"}}')

                resultado.append(f["url"])
            numero_canal += 1

    print(f"Canais organizados: {total_canais} canais ({total_links} links ativos em {len(ordem_final)} categorias).")
    return resultado

def formatar_agrupamento_temporadas(dados_agrupados, grupo_nome_singular, fichas):
    """
    Formata as séries no padrão oficial de VOD/Séries de IPTV:
    1. Uma única pasta raiz por tipo: group-title="Séries Dubladas", "Séries Legendadas", etc.
    2. Identificação por série via tags (series-name, season-number, episode-number) e padrão S01E01,
       permitindo que apps de IPTV (IPTV Smarters, TiviMate, XCIPTV, apps de iOS) agrupem cada série
       e suas respectivas temporadas automaticamente.
    3. Sem separadores artificiais que duplicam o primeiro episódio.
    """
    linhas_dub = []
    linhas_leg = []

    # Ordena os títulos em ordem alfabética
    for titulo in sorted(dados_agrupados.keys()):
        por_versao = dados_agrupados[titulo]
        for v_code in ["dub", "leg"]:
            if v_code not in por_versao:
                continue
            por_temp = por_versao[v_code]
            eh_dub = (v_code == "dub")
            v_extenso = "Dublado" if eh_dub else "Legendado"

            # Pasta principal única
            if grupo_nome_singular.lower() == "série":
                grupo = "Séries Dubladas" if eh_dub else "Séries Legendadas"
            elif grupo_nome_singular.lower() == "dorama":
                grupo = "Doramas Dublados" if eh_dub else "Doramas Legendados"
            else:
                grupo = "Animes Dublados" if eh_dub else "Animes Legendados"

            ch_busca = normalizar_texto(re.sub(r'\s*\(\d{4}\)\s*$', '', titulo))
            ficha = fichas.get(normalizar_texto(titulo)) or fichas.get(ch_busca)
            poster_padrao = (ficha["poster"] if ficha else "") or capa_tmdb(titulo) or capa_padrao(titulo)

            dest_lista = linhas_dub if eh_dub else linhas_leg

            # Percorre temporadas em ordem numérica (1, 2, 3...)
            for temp in sorted(por_temp.keys()):
                eps = por_temp[temp]
                if not eps:
                    continue
                # Ordena episódios em ordem crescente (1, 2, 3...)
                eps.sort(key=lambda x: x[0])

                poster_temp = eps[0][1] or poster_padrao

                # Episódios da temporada (formatados exatamente igual aos filmes)
                for ep_num, poster_ep, fontes in eps:
                    s_tag = f"S{temp:02d}E{ep_num:02d}"
                    tvg_name = f"{titulo} ({v_extenso}) {s_tag}"
                    poster_final = poster_ep or poster_temp
                    logo_ep_str = f' tvg-logo="{poster_final}"' if poster_final else ""
                    num_fontes = len(fontes)

                    for idx_f, url_f in enumerate(fontes):
                        sufixo_opcao = f" (Opção {idx_f + 1})" if num_fontes > 1 else ""
                        nome_ep = f"{titulo} ({v_extenso}) - T{temp:02d}E{ep_num:02d} - Episódio {ep_num:02d}{sufixo_opcao}"
                        ep_extinf = f'#EXTINF:-1 tvg-name="{tvg_name}" tvg-type="movie"{logo_ep_str} group-title="{grupo}",{nome_ep}'
                        dest_lista.append((ep_extinf, url_f))

    linhas_finais = []
    for ext, u in linhas_dub:
        linhas_finais.append(ext)
        linhas_finais.append(u)
    for ext, u in linhas_leg:
        linhas_finais.append(ext)
        linhas_finais.append(u)

    return linhas_finais, len(linhas_dub), len(linhas_leg)
    for ext, u in linhas_leg:
        linhas_finais.append(ext)
        linhas_finais.append(u)

    return linhas_finais, len(linhas_dub), len(linhas_leg)

def extrair_series_banco(destaques, fichas):
    print("Processando banco de Séries (Separando Dubladas e Legendadas)...")
    series_alvo = []
    
    for fila in ["Séries em alta", "Séries novas de 2026"]:
        for item in destaques.get(fila, []):
            if item.get("letra"):
                series_alvo.append(item)

    por_letra = collections.defaultdict(list)
    for s in series_alvo:
        por_letra[s["letra"]].append(s)

    dados_series = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
    series_processadas = set()

    for letra, lista_series in por_letra.items():
        letra_enc = "%23" if letra == "#" else letra
        conteudo_letra = baixar_texto(f"{BASE_FILMES}series-{letra_enc}.txt")
        if not conteudo_letra:
            continue

        mapa_pedacos = {}
        for l in conteudo_letra.splitlines():
            cols = l.split("\t")
            if len(cols) >= 3:
                mapa_pedacos[cols[0].strip().lower()] = int(cols[2])

        pedacos_necessarios = collections.defaultdict(list)
        for s in lista_series:
            p = mapa_pedacos.get(s["titulo"].lower())
            if p is not None:
                pedacos_necessarios[p].append(s)

        for p, series_pedaco in pedacos_necessarios.items():
            conteudo_p = baixar_texto(f"{BASE_FILMES}series-{letra_enc}-{p}.txt")
            if not conteudo_p:
                continue

            atual_serie = None
            for l in conteudo_p.splitlines():
                l = l.strip()
                if not l:
                    continue
                if l.startswith("@"):
                    nome_linha = l[1:].split("\t")[0].strip()
                    atual_serie = next((s for s in series_pedaco if s["titulo"].lower() == nome_linha.lower()), None)
                    if atual_serie:
                        series_processadas.add(atual_serie["titulo"])
                elif atual_serie:
                    # Animes duplicados na fila de séries do banco são ignorados aqui para ficarem no grupo de Animes
                    if atual_serie["titulo"].strip().lower() in ["one piece", "dragon ball super: beerus", "black clover", "bleach", "hunter x hunter", "hunter x hunter (2011)"]:
                        continue

                    parts = l.split("\t")
                    if len(parts) >= 4:
                        temp = int(parts[0])
                        ep = int(parts[1])
                        v_code = parts[2].strip().lower()
                        urls = [u.strip() for u in parts[3].split(",") if u.strip()]
                        if not urls:
                            continue

                        fontes = ordenar_fontes_contingencia(urls)
                        if not fontes:
                            continue

                        v_key = "dub" if v_code.startswith("dub") else "leg"
                        ch_busca = normalizar_texto(atual_serie["titulo"])
                        ficha = fichas.get(ch_busca)
                        poster = ficha["poster"] if ficha else ""

                        eps_existentes = {x[0] for x in dados_series[atual_serie["titulo"]][v_key][temp]}
                        if ep not in eps_existentes:
                            dados_series[atual_serie["titulo"]][v_key][temp].append((ep, poster, fontes))

    linhas_series, n_dub, n_leg = formatar_agrupamento_temporadas(dados_series, "série", fichas)
    print(f"Séries processadas: {len(dados_series)} séries ({n_dub} itens dublados, {n_leg} itens legendados).")
    return linhas_series

def extrair_doramas_banco(fichas):
    print("Processando banco de Doramas (Separando Dublados e Legendados)...")
    raw = baixar_texto(URL_DORAMAS)
    if not raw:
        return []

    dados_doramas = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
    atual_dorama = None
    poster_dorama = ""

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@"):
            parts = line[1:].split("\t")
            tit = parts[0].strip()
            atual_dorama = tit
            ch = normalizar_texto(tit)
            ficha = fichas.get(ch)
            poster_dorama = ficha["poster"] if ficha else ""
        elif atual_dorama:
            parts = line.split("\t")
            if len(parts) >= 4:
                temp = int(parts[0])
                ep = int(parts[1])
                v_code = parts[2].strip().lower()
                urls = [u.strip() for u in parts[3].split(",") if u.strip()]
                if not urls:
                    continue

                fontes = ordenar_fontes_contingencia(urls)
                if not fontes:
                    continue

                v_key = "dub" if v_code.startswith("dub") else "leg"
                eps_existentes = {x[0] for x in dados_doramas[atual_dorama][v_key][temp]}
                if ep not in eps_existentes:
                    dados_doramas[atual_dorama][v_key][temp].append((ep, poster_dorama, fontes))

    linhas_doramas, n_dub, n_leg = formatar_agrupamento_temporadas(dados_doramas, "dorama", fichas)
    print(f"Doramas processados: {len(dados_doramas)} doramas ({n_dub} itens dublados, {n_leg} itens legendados).")
    return linhas_doramas

def extrair_animes_destaques(destaques, fichas):
    print("Processando banco de Animes (Separando Dublados e Legendados)...")
    raw = baixar_texto(URL_ANIMES)
    if not raw:
        return []

    animes_destaques_set = set(item["chave_sem_ano"] for item in destaques.get("Animes", []))
    famosos = ["one piece", "bleach", "dragon ball", "naruto", "jujutsu kaisen", "demon slayer", "solo leveling", "black clover", "hunter x hunter", "attack on titan", "shingeki no kyojin", "death note", "chainsaw man", "spy x family"]
    for f in famosos:
        animes_destaques_set.add(f)

    dados_animes = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
    atual_anime = None
    poster_anime = ""
    capturar = False

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@"):
            parts = line[1:].split("\t")
            tit = parts[0].strip()
            ch = normalizar_texto(re.sub(r'\s*\(\d{4}\)\s*$', '', tit))
            capturar = ch in animes_destaques_set or any(f in ch for f in famosos)
            if capturar:
                atual_anime = tit
                ficha = fichas.get(normalizar_texto(tit))
                poster_anime = ficha["poster"] if ficha else ""
            else:
                atual_anime = None
        elif capturar and atual_anime:
            parts = line.split("\t")
            if len(parts) >= 4:
                temp = int(parts[0])
                ep = int(parts[1])
                v_code = parts[2].strip().lower()
                urls = [u.strip() for u in parts[3].split(",") if u.strip()]
                if not urls:
                    continue

                fontes = ordenar_fontes_contingencia(urls)
                if not fontes:
                    continue

                v_key = "dub" if v_code.startswith("dub") else "leg"
                eps_existentes = {x[0] for x in dados_animes[atual_anime][v_key][temp]}
                if ep not in eps_existentes:
                    dados_animes[atual_anime][v_key][temp].append((ep, poster_anime, fontes))

    linhas_animes, n_dub, n_leg = formatar_agrupamento_temporadas(dados_animes, "anime", fichas)
    print(f"Animes processados: {len(dados_animes)} animes ({n_dub} itens dublados, {n_leg} itens legendados).")
    return linhas_animes

def carregar_filmes_extras():
    """
    Opção A: Carrega filmes de listas extras (Xtream Codes / M3U) e adiciona ao catálogo VOD.
    Executado durante a geração diária (main()), sem pesar no ciclo rápido de canais ao vivo.
    """
    caminho_arquivo = os.path.join(REPO_ROOT, "data", "listas_extras.txt")
    urls_para_processar = []

    if os.path.exists(caminho_arquivo):
        with open(caminho_arquivo, "r", encoding="utf-8", errors="ignore") as f:
            for linha in f:
                l = linha.strip()
                if l and not l.startswith("#"):
                    urls_para_processar.append(l)

    env_url = os.environ.get("M3U_EXTRA_URL", "").strip()
    if env_url:
        for u in env_url.split(","):
            u_clean = u.strip()
            if u_clean and u_clean not in urls_para_processar:
                urls_para_processar.append(u_clean)

    if not urls_para_processar:
        return []

    print(f"Carregando catálogo VOD (Filmes) de {len(urls_para_processar)} lista(s) extra(s)...")
    linhas_filmes_extras = []

    for item in urls_para_processar:
        xtream_info = extrair_credenciais_xtream(item)
        if xtream_info:
            base = xtream_info["base"]
            user = xtream_info["user"]
            pwd = xtream_info["pwd"]
            try:
                # 1. Categorias VOD
                cat_map = {}
                url_cat = f"{base}/player_api.php?username={urllib.parse.quote(user)}&password={urllib.parse.quote(pwd)}&action=get_vod_categories"
                texto_cat = baixar_texto(url_cat)
                if texto_cat:
                    try:
                        for c in json.loads(texto_cat):
                            cat_map[str(c.get("category_id"))] = c.get("category_name", "")
                    except Exception:
                        pass

                # 2. Streams VOD (filmes)
                url_vod = f"{base}/player_api.php?username={urllib.parse.quote(user)}&password={urllib.parse.quote(pwd)}&action=get_vod_streams"
                texto_vod = baixar_texto(url_vod)
                if texto_vod:
                    vod_list = json.loads(texto_vod)
                    print(f"Recebidos {len(vod_list)} filmes extras via Xtream API.")
                    for v in vod_list:
                        name = v.get("name", "").strip()
                        sid = v.get("stream_id")
                        if not name or not sid:
                            continue
                        ext = v.get("container_extension", "mp4")
                        logo = v.get("stream_icon", "").strip()
                        cat_id = str(v.get("category_id", ""))
                        cat_nome = cat_map.get(cat_id, "Filmes - Geral")
                        if not cat_nome.lower().startswith("filmes"):
                            cat_nome = f"Filmes - {cat_nome}"
                        url_filme = f"{base}/movie/{user}/{pwd}/{sid}.{ext}"
                        logo_attr = f' tvg-logo="{logo}"' if logo else ""
                        extinf = f'#EXTINF:-1 tvg-name="{name}" tvg-type="movie"{logo_attr} group-title="{cat_nome}",{name}'
                        linhas_filmes_extras.append(extinf)
                        linhas_filmes_extras.append(url_filme)
            except Exception as e:
                print(f"Erro ao carregar filmes extras de {item}: {e}")

    print(f"Total de filmes extras carregados para VOD: {len(linhas_filmes_extras) // 2} títulos.")
    return linhas_filmes_extras

def processar_filmes(fichas, destaques):
    if not os.path.exists(FILMES_M3U_IN):
        return []

    print(f"Lendo filmes de {FILMES_M3U_IN}...")
    em_alta_list = destaques.get("Em alta", [])
    lancamentos_list = destaques.get("Lançamentos de 2026", [])

    filmes_por_grupo = collections.defaultdict(list)
    total_filmes_lidos = 0

    with open(FILMES_M3U_IN, "r", encoding="utf-8", errors="ignore") as f:
        linha_extinf = None
        for line in f:
            l = line.strip()
            if not l:
                continue
            if l.startswith("#EXTINF"):
                linha_extinf = l
            elif l.startswith("http") and linha_extinf:
                url = l
                if eh_fonte_desativada(url):
                    linha_extinf = None
                    continue

                total_filmes_lidos += 1
                virgula_idx = linha_extinf.rfind(",")
                titulo_exibicao = linha_extinf[virgula_idx + 1:].strip() if virgula_idx != -1 else "Filme"
                ano_titulo = extrair_ano(titulo_exibicao)

                nome_sem_versao = re.sub(r'\s*\((?:Dublado|Legendado|Opção)[^)]*\)', '', titulo_exibicao).strip()
                chave_com_ano = normalizar_texto(nome_sem_versao)
                chave_sem_ano = normalizar_texto(re.sub(r'\s*\(\d{4}\)\s*$', '', nome_sem_versao))

                info = fichas.get(chave_com_ano) or fichas.get(chave_sem_ano) or buscar_ficha(fichas, nome_sem_versao)
                poster = (info["poster"] if info else "") or capa_tmdb(nome_sem_versao) or capa_padrao(nome_sem_versao)
                generos = info["generos"] if info else []
                ano_final = ano_titulo or (info.get("ano") if info else None)

                grupo = None
                pode_ser_lancamento_2026 = (ano_final == 2026) or (ano_final is None and "2026" in titulo_exibicao)

                if pode_ser_lancamento_2026 and any(item["chave_sem_ano"] == chave_sem_ano for item in lancamentos_list):
                    grupo = "Filmes - Lançamentos 2026"
                elif any(item["chave_sem_ano"] == chave_sem_ano for item in em_alta_list):
                    grupo = "Filmes - Em Alta"
                elif generos:
                    grupo = f"Filmes - {generos[0].capitalize()}"
                else:
                    grupo = "Filmes - Geral"

                logo_str = f' tvg-logo="{poster}"' if poster else ""
                novo_extinf = f'#EXTINF:-1 tvg-name="{nome_sem_versao}" tvg-type="movie"{logo_str} group-title="{grupo}",{titulo_exibicao}'

                filmes_por_grupo[grupo].append((novo_extinf, url))
                linha_extinf = None

    print(f"Filmes processados: {total_filmes_lidos} links.")
    
    ordem_grupos_filmes = [
        "Filmes - Em Alta",
        "Filmes - Lançamentos 2026",
        "Filmes - Ação",
        "Filmes - Comédia",
        "Filmes - Drama",
        "Filmes - Terror",
        "Filmes - Suspense",
        "Filmes - Ficção científica",
        "Filmes - Aventura",
        "Filmes - Animação",
        "Filmes - Romance",
        "Filmes - Documentário",
        "Filmes - Família",
        "Filmes - Fantasia",
        "Filmes - Crime",
        "Filmes - Guerra",
        "Filmes - Faroeste",
        "Filmes - Geral"
    ]

    resultado_filmes = []
    for grp in ordem_grupos_filmes:
        if grp in filmes_por_grupo:
            for ext, link in filmes_por_grupo[grp]:
                resultado_filmes.append(ext)
                resultado_filmes.append(link)

    for grp in sorted(filmes_por_grupo.keys()):
        if grp not in ordem_grupos_filmes:
            for ext, link in filmes_por_grupo[grp]:
                resultado_filmes.append(ext)
                resultado_filmes.append(link)

    return resultado_filmes

def testar_fonte(f):
    """Teste de disponibilidade e medição precisa de latência (velocidade) com headers."""
    url = f.get("url", "").strip()
    ref = f.get("referer", "").strip()
    ua = f.get("agente", "").strip() or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    headers = {"User-Agent": ua}
    if ref:
        headers["Referer"] = ref
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.5) as r:
            chunk = r.read(256)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if r.status < 400 and (chunk or r.status == 200):
                return (True, elapsed_ms)
    except Exception:
        pass
    return (False, 99999.0)

def validar_canais(canais_por_cat, canais_adultos=None):
    """
    Testa os links dos canais ao vivo e adultos, mede latência (ping/velocidade) e coloca
    o link mais rápido em primeiro lugar (Opção 1).
    Descarta links mortos quando houver links saudáveis ativos.
    """
    if canais_adultos is None:
        canais_adultos = []

    todos_normais = [f for lista in canais_por_cat.values() for ch in lista for f in ch["fontes"]]
    todos_adultos = [f for ch in canais_adultos for f in ch["fontes"]]
    todos = todos_normais + todos_adultos

    if not todos:
        return canais_por_cat, canais_adultos

    print(f"Validando e medindo velocidade de {len(todos)} links ({len(todos_normais)} normais + {len(todos_adultos)} adultos)...")
    
    fontes_unicas = {}
    for f in todos:
        chave = (f["url"], f.get("referer", ""))
        if chave not in fontes_unicas:
            fontes_unicas[chave] = f
            
    chaves = list(fontes_unicas.keys())
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
        resultados_lista = list(ex.map(testar_fonte, [fontes_unicas[k] for k in chaves]))
    resultado = dict(zip(chaves, resultados_lista))
    
    vivos = sum(1 for v in resultado.values() if v[0])
    taxa = vivos / len(chaves) if chaves else 0
    print(f"Links respondendo: {vivos}/{len(chaves)} ({taxa*100:.0f}%).")
    
    if vivos == 0 and len(chaves) > 50:
        print("Aviso: Nenhum link respondeu (possível falha de rede geral no runner). Mantendo canais originais.")
        return canais_por_cat, canais_adultos
        
    def ordenar_lista(lista_canais):
        for ch in lista_canais:
            def sort_key(f):
                chave = (f["url"], f.get("referer", ""))
                res = resultado.get(chave, (False, 99999.0))
                return (0 if res[0] else 1, res[1])

            ch["fontes"].sort(key=sort_key)

            for f in ch["fontes"]:
                chave = (f["url"], f.get("referer", ""))
                res = resultado.get(chave, (False, 99999.0))
                f["ativo"] = res[0]
                f["latencia_ms"] = round(res[1], 1)

            ativas = [f for f in ch["fontes"] if f.get("ativo")]
            inativas = [f for f in ch["fontes"] if not f.get("ativo")]
            if ativas:
                ch["fontes"] = ativas + inativas[:1]

    for lista in canais_por_cat.values():
        ordenar_lista(lista)

    ordenar_lista(canais_adultos)

    return canais_por_cat, canais_adultos

def salvar_canais_json(canais_por_cat):
    total = sum(len(v) for v in canais_por_cat.values())
    if total < 500:
        print(f"PROTEÇÃO ATIVADA: Apenas {total} canais gerados (mínimo exigido: 500). Abortando gravação de data/canais.json para não apagar canais existentes!")
        return
    caminho = os.path.join(REPO_ROOT, "data", "canais.json")
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    lista_saida = []
    todas_categorias = [c for c in ORDEM_CATEGORIAS_CANAIS if c in canais_por_cat]
    outras_categorias = [c for c in sorted(canais_por_cat.keys()) if c not in ORDEM_CATEGORIAS_CANAIS]
    ordem_final = todas_categorias + outras_categorias
    for cat in ordem_final:
        canais = sorted(canais_por_cat[cat], key=lambda x: x.get("ordem", 0))
        for ch in canais:
            cid = normalizar_texto(ch["nome"]).replace(" ", "_")
            fontes_lista = []
            for idx, f in enumerate(ch["fontes"]):
                fontes_lista.append({
                    "url": f["url"],
                    "nome": f.get("qualidade") or (f"Fonte {idx+1}" if len(ch["fontes"]) > 1 else "Principal"),
                    "referer": f.get("referer", ""),
                    "agente": f.get("agente", ""),
                    "chave": f.get("chave", ""),
                    "latencia_ms": f.get("latencia_ms"),
                    "ativo": f.get("ativo", True)
                })
            lista_saida.append({
                "id": cid,
                "nome": ch["nome"],
                "logo": ch["logo"],
                "categoria": cat,
                "fontes": fontes_lista
            })
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(lista_saida, f, ensure_ascii=False, indent=2)
    print(f"data/canais.json atualizado: {len(lista_saida)} canais.")

def salvar_restritos_txt(canais_adultos):
    """Grava restritos.txt no formato nativo lido pelo app Android apenas após digitar a senha."""
    caminho = OUTPUT_RESTRITOS
    linhas = []
    canais = sorted(canais_adultos, key=lambda x: x.get("ordem", 0))
    for ch in canais:
        linhas.append(f"canal: {ch['nome']}")
        linhas.append("categoria: Adulto")
        if ch.get("logo"):
            linhas.append(f"logo: {ch['logo']}")
        for f in ch.get("fontes", []):
            linhas.append(f"fonte: {f['url']}")
            linhas.append(f"url: {f['url']}")
            if f.get("qualidade"):
                linhas.append(f"qualidade: {f['qualidade']}")
            if f.get("referer"):
                linhas.append(f"referer: {f['referer']}")
            if f.get("agente"):
                linhas.append(f"agente: {f['agente']}")
            if f.get("chave"):
                linhas.append(f"chave: {f['chave']}")
        linhas.append("")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    print(f"restritos.txt atualizado: {len(canais)} canais adultos salvos (protegidos por senha).")

def salvar_restritos_json(canais_adultos):
    caminho = os.path.join(REPO_ROOT, "data", "restritos.json")
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    lista_saida = []
    canais = sorted(canais_adultos, key=lambda x: x.get("ordem", 0))
    for ch in canais:
        cid = normalizar_texto(ch["nome"]).replace(" ", "_")
        fontes_lista = []
        for idx, f in enumerate(ch["fontes"]):
            fontes_lista.append({
                "url": f["url"],
                "nome": f.get("qualidade") or (f"Fonte {idx+1}" if len(ch["fontes"]) > 1 else "Principal"),
                "referer": f.get("referer", ""),
                "agente": f.get("agente", ""),
                "chave": f.get("chave", ""),
                "latencia_ms": f.get("latencia_ms"),
                "ativo": f.get("ativo", True)
            })
        lista_saida.append({
            "id": cid,
            "nome": ch["nome"],
            "logo": ch["logo"],
            "categoria": "Adulto",
            "fontes": fontes_lista
        })
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(lista_saida, f, ensure_ascii=False, indent=2)
    print(f"data/restritos.json atualizado: {len(lista_saida)} canais.")

def formatar_adultos_m3u(canais_adultos):
    resultado = []
    numero_canal = 1
    for ch in sorted(canais_adultos, key=lambda x: x.get("ordem", 0)):
        num_fontes = len(ch["fontes"])
        chno_attr = f' tvg-chno="{numero_canal}"'
        for idx, f in enumerate(ch["fontes"]):
            sufixo = f" (Opção {idx + 1})" if num_fontes > 1 else ""
            nome_final = ch["nome"] + sufixo
            logo_attr = f' tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
            group_attr = ' group-title="Adulto"'
            ref = f.get("referer", "").strip()
            ua = f.get("agente", "").strip() or "Mozilla/5.0"
            header_attrs = ""
            if ref:
                header_attrs += f' http-referrer="{ref}"'
            if ua:
                header_attrs += f' http-user-agent="{ua}"'
            resultado.append(f'#EXTINF:-1{chno_attr} tvg-name="{ch["nome"]}" tvg-type="live"{logo_attr}{group_attr}{header_attrs},{nome_final}')
            resultado.append(f["url"])
        numero_canal += 1
    return resultado

def salvar_catalogo_txt(canais_por_cat):
    total = sum(len(v) for v in canais_por_cat.values())
    if total < 500:
        print(f"PROTEÇÃO ATIVADA: Apenas {total} canais gerados (mínimo de segurança: 500). Abortando gravação de catalogo.txt para não apagar os canais existentes!")
        return
    caminho = os.path.join(REPO_ROOT, "catalogo.txt")
    linhas = []
    todas_categorias = [c for c in ORDEM_CATEGORIAS_CANAIS if c in canais_por_cat]
    outras_categorias = [c for c in sorted(canais_por_cat.keys()) if c not in ORDEM_CATEGORIAS_CANAIS]
    ordem_final = todas_categorias + outras_categorias
    for cat in ordem_final:
        canais = sorted(canais_por_cat[cat], key=lambda x: x.get("ordem", 0))
        for ch in canais:
            linhas.append(f"canal: {ch['nome']}")
            linhas.append(f"categoria: {cat}")
            if ch.get("logo"):
                linhas.append(f"logo: {ch['logo']}")
            for f in ch["fontes"]:
                linhas.append(f"fonte: {f['url']}")
                if f.get("qualidade"):
                    linhas.append(f"qualidade: {f['qualidade']}")
                if f.get("referer"):
                    linhas.append(f"referer: {f['referer']}")
                if f.get("agente"):
                    linhas.append(f"agente: {f['agente']}")
                if f.get("chave"):
                    linhas.append(f"chave: {f['chave']}")
            linhas.append("")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    print(f"catalogo.txt atualizado: {len(linhas)} linhas.")

def main_aovivo():
    """Atualização rápida: só canais ao vivo. Reaproveita o VOD já gerado."""
    print("Atualizando canais ao vivo (separando canais adultos para restritos.txt)...")
    epg_urls = "https://iptv-epg.org/files/epg-br.xml,https://www.open-epg.com/files/brazil3.xml,https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/PlutoTV/br.xml"
    
    canais_por_cat, canais_adultos = processar_canais()
    total_canais = sum(len(v) for v in canais_por_cat.values())
    if total_canais < 500:
        print(f"PROTEÇÃO ATIVADA: Apenas {total_canais} canais gerados (mínimo exigido: 500). Abortando atualização de aovivo.m3u e canais.m3u para proteger as categorias 24h e lista completa!")
        return

    canais_por_cat, canais_adultos = validar_canais(canais_por_cat, canais_adultos)

    # 1. Salva canais ao vivo normais (LIVRES de canais adultos)
    salvar_canais_json(canais_por_cat)
    salvar_catalogo_txt(canais_por_cat)
    linhas_canais = formatar_canais_m3u(canais_por_cat)

    with open(OUTPUT_AOVIVO, "w", encoding="utf-8") as f:
        f.write("\n".join([
            f'#EXTM3U url-tvg="{epg_urls}" x-tvg-url="{epg_urls}" name="Canais Ao Vivo Play"',
            '# Lista dedicada exclusivamente a canais de TV ao vivo (livre de conteúdo adulto)', ''
        ] + linhas_canais))

    # 2. Salva canais adultos (+18) isolados no restritos.txt (aparecem no app apenas após digitar a senha)
    if canais_adultos:
        salvar_restritos_txt(canais_adultos)
        salvar_restritos_json(canais_adultos)
        linhas_adultos = formatar_adultos_m3u(canais_adultos)
        with open(OUTPUT_ADULTOS, "w", encoding="utf-8") as f:
            f.write("\n".join([
                f'#EXTM3U name="Canais Adultos +18 (Restrito)"',
                '# Lista restrita protegida por controle parental', ''
            ] + linhas_adultos))
    else:
        print("Aviso: nenhum canal adulto retornado nesta execução; mantendo restritos.txt, data/restritos.json e adultos.m3u existentes.")

    corpo_vod = []
    if os.path.exists(OUTPUT_VOD):
        with open(OUTPUT_VOD, "r", encoding="utf-8", errors="ignore") as f:
            corpo_vod = f.read().splitlines()[2:]
    todas = [
        f'#EXTM3U url-tvg="{epg_urls}" x-tvg-url="{epg_urls}" name="Canais & Filmes Play"',
        '# Lista unificada de canais ao vivo, filmes, series, animes e doramas com temporadas e EPG',
        ''
    ] + linhas_canais + corpo_vod
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("\n".join(todas))
    print("aovivo.m3u (sem adultos), restritos.txt (adultos com senha) e canais.m3u atualizados.")

def main():
    print("Iniciando geração completa da lista IPTV (Canais + Filmes + Séries + Doramas + Animes)...")
    epg_urls = "https://iptv-epg.org/files/epg-br.xml,https://www.open-epg.com/files/brazil3.xml,https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/PlutoTV/br.xml"
    cabecalho = [
        f'#EXTM3U url-tvg="{epg_urls}" x-tvg-url="{epg_urls}" name="Canais & Filmes Play"',
        '# Lista unificada de canais ao vivo, filmes, series, animes e doramas com temporadas e EPG',
        ''
    ]

    fichas = carregar_fichas()
    destaques = carregar_destaques()

    # 1. Canais Ao Vivo
    canais_por_cat, canais_adultos = processar_canais()
    total_canais = sum(len(v) for v in canais_por_cat.values())
    if total_canais < 500:
        print(f"PROTEÇÃO ATIVADA: Apenas {total_canais} canais gerados (mínimo exigido: 500). Abortando atualização de aovivo.m3u e canais.m3u para proteger as categorias 24h e lista completa!")
        return

    canais_por_cat, canais_adultos = validar_canais(canais_por_cat, canais_adultos)
    if canais_adultos:
        salvar_restritos_txt(canais_adultos)
        salvar_restritos_json(canais_adultos)
        linhas_adultos = formatar_adultos_m3u(canais_adultos)
        with open(OUTPUT_ADULTOS, "w", encoding="utf-8") as f:
            f.write("\n".join([f'#EXTM3U name="Canais Adultos +18 (Restrito)"', ''] + linhas_adultos))
    else:
        print("Aviso: nenhum canal adulto retornado nesta execução; mantendo restritos.txt, data/restritos.json e adultos.m3u existentes.")
    salvar_canais_json(canais_por_cat)
    salvar_catalogo_txt(canais_por_cat)
    linhas_canais = formatar_canais_m3u(canais_por_cat)

    # 2. Filmes On Demand
    linhas_filmes = processar_filmes(fichas, destaques)
    linhas_filmes_extras = carregar_filmes_extras()
    if linhas_filmes_extras:
        linhas_filmes.extend(linhas_filmes_extras)

    # 3. Séries com Temporadas e Episódios (S01 E01)
    linhas_series = extrair_series_banco(destaques, fichas)

    # 4. Doramas completos com Temporadas e Episódios (264 títulos)
    linhas_doramas = extrair_doramas_banco(fichas)

    # 5. Animes populares com Temporadas e Episódios
    linhas_animes = extrair_animes_destaques(destaques, fichas)

    # 6. Grava canais.m3u (LISTA UNIFICADA COMPLETA)
    todas_linhas = cabecalho + linhas_canais
    if linhas_filmes:
        todas_linhas.append("\n# ===========================================")
        todas_linhas.append("# CATÁLOGO DE FILMES ON DEMAND")
        todas_linhas.append("# ===========================================\n")
        todas_linhas.extend(linhas_filmes)

    if linhas_series:
        todas_linhas.append("\n# ===========================================")
        todas_linhas.append("# CATÁLOGO DE SÉRIES COM TEMPORADAS E EPISÓDIOS")
        todas_linhas.append("# ===========================================\n")
        todas_linhas.extend(linhas_series)

    if linhas_doramas:
        todas_linhas.append("\n# ===========================================")
        todas_linhas.append("# CATÁLOGO DE DORAMAS COM TEMPORADAS E EPISÓDIOS")
        todas_linhas.append("# ===========================================\n")
        todas_linhas.extend(linhas_doramas)

    if linhas_animes:
        todas_linhas.append("\n# ===========================================")
        todas_linhas.append("# CATÁLOGO DE ANIMES COM TEMPORADAS E EPISÓDIOS")
        todas_linhas.append("# ===========================================\n")
        todas_linhas.extend(linhas_animes)

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("\n".join(todas_linhas))
    mb_unif = os.path.getsize(OUTPUT_M3U) / (1024 * 1024)
    print(f"Sucesso! canais.m3u gerado em: {OUTPUT_M3U} ({mb_unif:.2f} MB, {len(todas_linhas)} linhas).")

    # 7. Grava aovivo.m3u (APENAS CANAIS AO VIVO)
    linhas_so_ao_vivo = [
        f'#EXTM3U url-tvg="{epg_urls}" x-tvg-url="{epg_urls}" name="Canais Ao Vivo Play"',
        '# Lista dedicada exclusivamente a canais de TV ao vivo',
        ''
    ] + linhas_canais

    with open(OUTPUT_AOVIVO, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas_so_ao_vivo))
    kb_aovivo = os.path.getsize(OUTPUT_AOVIVO) / 1024
    print(f"Sucesso! aovivo.m3u gerado em: {OUTPUT_AOVIVO} ({kb_aovivo:.1f} KB).")

    # 8. Grava vod.m3u (FILMES, SÉRIES, DORAMAS E ANIMES)
    vod_linhas = ['#EXTM3U name="Filmes, Séries, Doramas e Animes On Demand"', '']
    if linhas_filmes:
        vod_linhas.extend(linhas_filmes)
    if linhas_series:
        vod_linhas.extend(linhas_series)
    if linhas_doramas:
        vod_linhas.extend(linhas_doramas)
    if linhas_animes:
        vod_linhas.extend(linhas_animes)

    with open(OUTPUT_VOD, "w", encoding="utf-8") as f:
        f.write("\n".join(vod_linhas))
    salvar_cache_capas()
    mb_vod = os.path.getsize(OUTPUT_VOD) / (1024 * 1024)
    print(f"Sucesso! vod.m3u gerado em: {OUTPUT_VOD} ({mb_vod:.2f} MB).")

if __name__ == "__main__":
    if "--aovivo" in sys.argv:
        main_aovivo()
    else:
        main()
