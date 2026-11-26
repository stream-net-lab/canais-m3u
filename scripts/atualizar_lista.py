import os
import re
import gzip
import urllib.request
import urllib.parse
import urllib.error
import collections
import json
import unicodedata
import sys
import concurrent.futures
import time

# URLs oficiais do Catálogo Base
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
            print(f"Lista de bloqueio de segurança carregada: {len(_fontes_desativadas_cache)} domínios/regras.")
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
    "24 Horas",
    "Infantil",
    "Documentários",
    "Notícias",
    "Religiosos",
    "Músicas",
    "Variedades",
    "Pluto TV"
]

def _ler_resposta_texto(resp, max_bytes=None):
    """Lê resposta HTTP descompactando gzip automaticamente se necessário."""
    raw = resp.read(max_bytes) if max_bytes else resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")

def baixar_texto(url):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Encoding": "gzip, identity",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _ler_resposta_texto(resp)
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
        # 3. API de stream de alta disponibilidade
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

def eh_conteudo_gay_ou_trans(nome):
    """
    Detecta canais e conteúdos voltados ao público gay, trans, travesti ou homossexual
    para remoção completa e definitiva da lista restrita de adultos.
    """
    texto = (nome or "").lower()
    padrao = r'(?:^|[^a-z0-9])(?:trans|travesti|shemale|ladyboy|crossdresser|homossexual|gay|glbt|lgbt|twink|bear|garotos|boys|for\s*man|men)(?:[^a-z0-9]|$)'
    return bool(re.search(padrao, texto))

def normalizar_categoria(cat, nome_canal=""):
    cat_str = (cat or "").strip()
    nome_str = (nome_canal or "").strip()
    c_low = cat_str.lower()
    n_low = nome_str.lower()

    # 1. Conteúdo Adulto (+18)
    if any(k in c_low for k in ["adult", "xxx", "porn", "porno", "sex", "+18", "18+"]) or any(k in n_low for k in ["+18", "18+", "playboy", "venus", "sextreme", "hustler"]):
        return "Adulto"

    # 2. 24 Horas: prioridade absoluta para NUNCA vazar para Filmes e Séries, Infantil, Músicas ou Variedades
    if "[24h]" in n_low or re.search(r'\b24\s*h(?:oras)?\b|\b24/7\b|\[24h\]', n_low) or "24h" in c_low or "24 horas" in c_low:
        return "24 Horas"

    # 3. Jogos do Dia: partidas de futebol ao vivo do dia com horário marcado [HH:MM]
    if "jogos do dia" in c_low or re.match(r"^\[\d{2}:\d{2}\]", nome_str):
        return "Jogos do Dia"

    # 4. PPV: A Fazenda (todas as câmeras e feeds), BBB (Big Brother), feeds de reality shows e eventos PPV / Lutas
    if any(k in c_low for k in ["ppv", "pay-per-view", "reality", "combate", "ufc", "bjj"]) or any(k in n_low for k in ["fazenda", "bbb", "big brother", "reality", "combate", "ufc", "bjj stars", "furacao live", "onefootball", "mr. olympia", "barretos", "fcf tv"]):
        return "PPV"

    # 5. Esportes: Premiere (todas as opções/clubes), SporTV, ESPN, BandSports, TNT Sports, DAZN, Copa do Brasil, Brasileirão, League Cup, NBA, Futsal, etc.
    # Exceção: "Universal Premiere" é canal de filmes da Universal+
    eh_universal_filme = "universal premiere" in n_low or "universal+" in n_low
    if not eh_universal_filme:
        termos_esporte_cat = ["esporte", "sport", "futebol", "espn", "sportv", "dazn", "nba", "campeonatos", "futsal", "premiere", "copa", "brasileirao", "liga"]
        termos_esporte_nome = [
            "premiere", "sportv", "espn", "dazn", "band sports", "bandsports", "tnt sports", "pfc",
            "copa do brasil", "brasileirao", "league cup", "nba league pass", "liga futsal",
            "canal goat", "goat", "cazetv", "caze tv", "ge tv", "canal off", "off 4k", "woohoo",
            "fish tv", "x sports", "n sports", "sportynet", "fifa+", "a bola", "benfica", "canal 11",
            "eleven"
        ]
        if any(k in c_low for k in termos_esporte_cat) or any(k in n_low for k in termos_esporte_nome):
            return "Esportes"

    # 6. Religiosos: TV Aparecida, Pai Eterno, Canção Nova, Rede Vida, IMPD, Rede Gospel, TV Novo Tempo, RIT, Evangelizar, etc.
    termos_relig_cat = ["religio", "gospel", "catolico", "evangelico"]
    termos_relig_nome = ["aparecida", "pai eterno", "cancao nova", "canção nova", "evangelizar", "redavida", "rede vida", "redegospel", "rede gospel", "novo tempo", "boa vontade"]
    if any(k in c_low for k in termos_relig_cat) or any(k in n_low for k in termos_relig_nome) or re.search(r'\b(?:rit|impd)\b', n_low):
        return "Religiosos"

    # 7. Notícias: GloboNews, SBT News, Record News, CNN Brasil, BandNews, Jovem Pan News, CNBC Brasil, Canal UOL, France 24
    termos_notic_cat = ["noticia", "news", "jornal", "cnn", "bandnews", "globonews"]
    termos_notic_nome = ["globonews", "globo news", "cnn brasil", "bandnews", "band news", "jovem pan news", "record news", "sbt news", "cnbc brasil", "canal uol", "france 24", "tvideonews"]
    if any(k in c_low for k in termos_notic_cat) or any(k in n_low for k in termos_notic_nome):
        return "Notícias"

    # 8. Infantil: Cartoon, Discovery Kids, Gloob, Nickelodeon, etc.
    termos_inf_cat = ["infantil", "infantis", "kids", "desenho", "animacao", "anime"]
    termos_inf_nome = ["cartoon", "gloob", "nickelodeon", "nick jr", "disney jr", "discovery kids", "tooncast", "boomerang", "dumdum", "nickonline", "nicktoons", "tv rá tim bum", "tv ra tim bum", "anime channel", "otaku sign", "geekdot", "loading", "zoomoo"]
    if any(k in c_low for k in termos_inf_cat) or any(k in n_low for k in termos_inf_nome):
        return "Infantil"

    # 9. Documentários: Discovery, History, Animal Planet, Nat Geo, Curta, etc.
    termos_doc_cat = ["doc", "discovery", "history", "animal", "geographic"]
    termos_doc_nome = ["discovery channel", "discovery theater", "discovery turbo", "discovery science", "discovery id", "discovery home", "discovery world", "discovery h&h", "history", "history 2", "animal planet", "nat geo", "national geographic", "curta", "h2 4k", "id 4k"]
    if any(k in c_low for k in termos_doc_cat) or any(k in n_low for k in termos_doc_nome):
        return "Documentários"

    # 10. Músicas: Multishow, BIS, MTV, Trace, Retro Music, Now Rock, Music Box Brazil, Top TV
    termos_mus_cat = ["musica", "music", "mtv", "multishow", "bis"]
    termos_mus_nome = ["multishow", "bis", "mtv", "trace", "music box brazil", "now rock", "retro music", "top tv"]
    if any(k in c_low for k in termos_mus_cat) or any(k in n_low for k in termos_mus_nome):
        return "Músicas"

    # 11. Filmes e Séries: Telecine, HBO, Warner, Universal TV, Sony, Megapix, TNT Séries, Eurochannel, Paramount, Disney, etc.
    termos_film_cat = ["filme", "serie", "cinema", "telecine", "hbo", "paramount", "disney", "prime video", "apple tv", "max", "legendado"]
    termos_film_nome = ["telecine", "hbo", "megapix", "tnt", "space", "axn", "warner", "universal tv", "paramount", "sony", "cinemax", "usa network", "studio universal", "universal premiere", "canal brasil", "eurochannel", "film&arts", "syfy", "tcm", "runtime", "moviesphere", "classique faroeste", "tv clássicos", "amc"]
    if any(k in c_low for k in termos_film_cat) or any(k in n_low for k in termos_film_nome):
        return "Filmes e Séries"

    # 12. TV Aberta: Globo, Record, SBT, Band, RedeTV!, TV Cultura, TV Brasil, TV Gazeta e afiliadas regionais (canais 4K abertos ficam aqui)
    termos_aberta_cat = ["aberto", "aberta", "local", "nacional", "globo", "record", "sbt", "band"]
    termos_aberta_nome = ["globo", "record", "sbt", "bandeirantes", "band ", "band tv", "cultura", "redetv", "rede tv", "tv brasil", "canal gov", "tv justica", "tv justiça", "tv senado", "canal educacao", "futura", "tv a critica", "tv gazeta", "rede brasil", "playtv", "tv uniao", "tve bahia"]
    if any(k in c_low for k in termos_aberta_cat) or any(k in n_low for k in termos_aberta_nome):
        return "TV Aberta"

    # 13. Pluto TV
    if "pluto" in c_low:
        return "Pluto TV"

    # Não criar abas para tags técnicas de resolução ou idioma (4K, Legendados)
    if any(k in c_low for k in ["4k", "fhdr", "uhd", "legendado", "legendados"]):
        return "Variedades"

    # 14. Outras categorias: manter a categoria original limpa em vez de forçar tudo em Variedades
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

def mascarar_url_log(url):
    """Remove credenciais da URL para exibição segura em relatórios/painel."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc or url
        return f"{parsed.scheme}://{host}{parsed.path}" if parsed.scheme else host
    except Exception:
        return "servidor"


def testar_servidor_lista(url):
    """
    Testa se uma URL de lista enviada pelo painel (Xtream Codes ou M3U) está online e funcional.
    Suporta gzip, tenta API Xtream com User-Agent padrão e faz fallback direto para M3U.
    Retorna (ok: bool, motivo: str, latencia_ms: float).
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return (False, "URL inválida (precisa começar com http:// ou https://)", 99999.0)

    ua_browser = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    motivo_xtream = ""

    t0 = time.perf_counter()
    xtream = extrair_credenciais_xtream(url)
    if xtream:
        base = xtream["base"]
        user = xtream["user"]
        pwd = xtream["pwd"]
        api_url = f"{base}/player_api.php?username={urllib.parse.quote(user)}&password={urllib.parse.quote(pwd)}"
        try:
            req = urllib.request.Request(
                api_url,
                headers={"User-Agent": ua_browser, "Accept": "application/json, */*", "Accept-Encoding": "gzip, identity"},
            )
            with urllib.request.urlopen(req, timeout=10.0) as r:
                raw = _ler_resposta_texto(r, 32768).strip()
            elapsed = (time.perf_counter() - t0) * 1000.0
            if not raw:
                motivo_xtream = "API Xtream retornou resposta vazia"
            elif raw.startswith("<"):
                motivo_xtream = "API Xtream retornou HTML (possível bloqueio de IP de datacenter)"
            else:
                data = json.loads(raw)
                uinfo = data.get("user_info", {})
                auth = str(uinfo.get("auth", "0"))
                status = str(uinfo.get("status", "")).lower()
                if auth == "1" and status == "active":
                    return (True, f"Xtream ativo ({round(elapsed)}ms)", elapsed)
                return (False, f"Conta inativa ou expirada (status={status or 'inválido'})", elapsed)
        except urllib.error.HTTPError as he:
            motivo_xtream = f"API Xtream retornou HTTP {he.code}"
        except Exception as e:
            motivo_xtream = f"API Xtream falhou ({type(e).__name__})"

    # Teste direto como lista M3U (também serve como fallback se player_api.php falhar)
    try:
        t1 = time.perf_counter()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": ua_browser, "Accept": "*/*", "Accept-Encoding": "gzip, identity"},
        )
        with urllib.request.urlopen(req, timeout=12.0) as r:
            amostra = _ler_resposta_texto(r, 65536).strip()
        elapsed = (time.perf_counter() - t1) * 1000.0
        if "#EXTINF" in amostra or "#EXTM3U" in amostra:
            return (True, f"Lista M3U ativa ({round(elapsed)}ms)", elapsed)
        if not amostra:
            det = "Servidor retornou resposta vazia (0 bytes)"
        elif amostra.startswith("<"):
            det = "Servidor retornou página HTML (possível bloqueio de IP de datacenter/região)"
        else:
            det = "Resposta recebida não é uma lista M3U válida"
        if motivo_xtream:
            det = f"{motivo_xtream} | Fallback M3U: {det}"
        return (False, det, elapsed)
    except urllib.error.HTTPError as he:
        det = f"HTTP {he.code} ao baixar lista"
        if motivo_xtream:
            det = f"{motivo_xtream} | Fallback M3U: {det}"
        return (False, det, 99999.0)
    except Exception as e:
        det = f"Servidor inacessível ({type(e).__name__})"
        if motivo_xtream:
            det = f"{motivo_xtream} | Fallback M3U: {det}"
        return (False, det, 99999.0)


def processar_listas_recebidas_painel():
    """
    Recebe novas listas enviadas pelo painel do celular (via variável NOVAS_LISTAS_PAINEL),
    testa cada servidor, adiciona os ativos à lista mestre (data/listas_extras.txt)
    e grava um resumo em data/ultimo_envio_painel.json para exibição no painel.
    """
    bruto = os.environ.get("NOVAS_LISTAS_PAINEL", "").strip()
    if not bruto:
        return

    candidatas = []
    for linha in re.split(r"[\r\n,]+", bruto):
        u = linha.strip()
        if u and u not in candidatas:
            candidatas.append(u)

    if not candidatas:
        return

    caminho_extras = os.path.join(REPO_ROOT, "data", "listas_extras.txt")
    existentes = set()
    if os.path.exists(caminho_extras):
        with open(caminho_extras, "r", encoding="utf-8", errors="ignore") as f:
            for l in f:
                s = l.strip()
                if s and not s.startswith("#"):
                    existentes.add(s)

    adicionadas = []
    duplicadas = []
    rejeitadas = []
    detalhes = []

    print(f"Recebidas {len(candidatas)} lista(s) do painel do celular. Testando servidores...")
    for url in candidatas:
        label = mascarar_url_log(url)
        if url in existentes:
            duplicadas.append(label)
            detalhes.append({"servidor": label, "status": "duplicada", "detalhe": "Já está na lista mestre"})
            print(f"  [DUPLICADA] {label}")
            continue

        ok, motivo, lat_ms = testar_servidor_lista(url)
        if ok:
            existentes.add(url)
            adicionadas.append(url)
            detalhes.append({"servidor": label, "status": "adicionada", "detalhe": motivo, "latencia_ms": round(lat_ms, 1)})
            print(f"  [OK -> ADICIONADA] {label} - {motivo}")
        else:
            rejeitadas.append(label)
            detalhes.append({"servidor": label, "status": "offline", "detalhe": motivo})
            print(f"  [REJEITADA] {label} - {motivo}")

    if adicionadas:
        os.makedirs(os.path.dirname(caminho_extras), exist_ok=True)
        precisa_quebra = False
        if os.path.exists(caminho_extras):
            with open(caminho_extras, "rb") as fb:
                conteudo = fb.read()
                precisa_quebra = len(conteudo) > 0 and not conteudo.endswith(b"\n")
        with open(caminho_extras, "a", encoding="utf-8") as f:
            if precisa_quebra:
                f.write("\n")
            for u in adicionadas:
                f.write(u + "\n")
        print(f"Adicionada(s) {len(adicionadas)} nova(s) lista(s) ativa(s) em data/listas_extras.txt.")

    from datetime import datetime, timezone
    resumo = {
        "data": datetime.now(timezone.utc).isoformat(),
        "testadas": len(candidatas),
        "adicionadas": len(adicionadas),
        "duplicadas": len(duplicadas),
        "rejeitadas": len(rejeitadas),
        "detalhes": detalhes,
    }
    caminho_resumo = os.path.join(REPO_ROOT, "data", "ultimo_envio_painel.json")
    os.makedirs(os.path.dirname(caminho_resumo), exist_ok=True)
    with open(caminho_resumo, "w", encoding="utf-8") as f:
        json.dump(resumo, f, indent=2, ensure_ascii=False)


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
                import hashlib
                cache_key = hashlib.md5(f"{base}|{user}".encode("utf-8")).hexdigest()[:10]
                cache_file = os.path.join(REPO_ROOT, "data", f"xtream_cache_{cache_key}.json")
                legacy_cache = os.path.join(REPO_ROOT, "data", "xtream_cache.json")
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
                    fallback_cache = cache_file if os.path.exists(cache_file) else (legacy_cache if os.path.exists(legacy_cache) else None)
                    if fallback_cache:
                        print(f"Aviso: Não foi possível obter canais online de {base}. Usando cache local permanente ({fallback_cache})...")
                        try:
                            with open(fallback_cache, "r", encoding="utf-8") as cf:
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
                        # Exclui completamente conteúdos gay e trans da lista adulta
                        if eh_conteudo_gay_ou_trans(nome_canal):
                            continue
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
                    is_leg = eh_fonte_legendada(nome_canal)
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
                                "qualidade": "",
                                "legendado": is_leg
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
                                "qualidade": "",
                                "legendado": is_leg
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
                        # Exclui completamente conteúdos gay e trans da lista adulta
                        if eh_conteudo_gay_ou_trans(nome_canal):
                            continue
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
            if not eh_conteudo_gay_ou_trans(canal_atual):
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
        # Preservar a ordem original do catalogo em vez de alfabetica
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
    """
    Teste de disponibilidade e medição precisa de latência (TTFB / velocidade)
    com headers otimizados para servidores IPTV e streams HLS/TS.
    """
    url = f.get("url", "").strip()
    ref = f.get("referer", "").strip()
    headers = {
        "User-Agent": "VLC/3.0.18 LibVLC/3.0.18",
        "Accept": "*/*",
        "Range": "bytes=0-1024",
        "Connection": "close"
    }
    if ref:
        headers["Referer"] = ref
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.0) as r:
            chunk = r.read(256)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if r.status < 400 and (chunk or r.status in (200, 206)):
                return (True, elapsed_ms)
    except Exception:
        # Fallback rápido com método HEAD
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VLC/3.0.18 LibVLC/3.0.18"}, method="HEAD")
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=2.5) as r:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                if r.status < 400:
                    return (True, elapsed_ms)
        except Exception:
            pass
    return (False, 99999.0)

def eh_fonte_legendada(nome):
    """Retorna True se o nome original indicar áudio em inglês / legendado ([LEG], ENG, SAP, etc.)."""
    nl = (nome or "").lower()
    return bool(re.search(r"\[leg\]|\(leg\)|\b(?:leg|legendado|eng|english|sap|original)\b", nl))


def eh_canal_portugal(nome, categoria=""):
    """Identifica canais de Portugal (ex.: 'PT - SportTV 1', 'PT - Benfica TV', 'RTP 1', etc.) para exclusão."""
    n = (nome or "").strip()
    c = (categoria or "").strip().lower()
    nl = n.lower()
    if re.match(r"^\s*(?:\[\s*pt\s*\]|\(\s*pt\s*\)|pt\s*[-:|/]|pt\s+)", nl):
        return True
    if "portugal" in nl or "portugal" in c or re.search(r"\bcanais\s+pt\b", c):
        return True
    if re.search(r"\b(?:rtp\s*[123]|sporttv\s*\d|benfica\s*tv|porto\s*canal|a\s*bola\s*tv|canal\s*11|sic\s+not[ií]cias|tvi\s+reality)\b", nl):
        return True
    return False


def validar_canais(canais_por_cat, canais_adultos=None):
    """
    Testa os links dos canais ao vivo e adultos, mede latência (ping/velocidade) e coloca
    o link mais rápido em primeiro lugar (Opção 1 / Principal).
    Remove canais 100% fora do ar (0 links ativos), canais de Portugal (PT) e conteúdo gay/trans.
    """
    if canais_adultos is None:
        canais_adultos = []

    # Remover canais de Portugal (PT) de todas as categorias
    removidos_pt = 0
    for cat in list(canais_por_cat.keys()):
        antes = len(canais_por_cat[cat])
        canais_por_cat[cat] = [
            ch for ch in canais_por_cat[cat]
            if not eh_canal_portugal(ch.get("nome", ""), cat)
        ]
        removidos_pt += antes - len(canais_por_cat[cat])
    if removidos_pt:
        print(f"Filtro Portugal (PT): {removidos_pt} canal(is) PT removido(s).")

    # Filtrar gay/trans e PT da lista adulta
    canais_adultos = [
        ch for ch in canais_adultos
        if not eh_conteudo_gay_ou_trans(ch.get("nome", ""))
        and not eh_canal_portugal(ch.get("nome", ""), "Adulto")
    ]

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
    
    def ordenar_lista(lista_canais):
        for ch in lista_canais:
            def sort_key(f):
                chave = (f["url"], f.get("referer", ""))
                res = resultado.get(chave, (False, 99999.0))
                # 1o: ativo; 2o: audio Portugues/Dublado antes de [LEG]/Ingles; 3o: menor latencia (ms)
                return (0 if res[0] else 1, 1 if f.get("legendado") else 0, res[1])

            ch["fontes"].sort(key=sort_key)

            for f in ch["fontes"]:
                chave = (f["url"], f.get("referer", ""))
                res = resultado.get(chave, (False, 99999.0))
                f["ativo"] = res[0]
                f["latencia_ms"] = round(res[1], 1)

            if vivos > 0:
                ativas = [f for f in ch["fontes"] if f.get("ativo")]
                if ativas:
                    ch["fontes"] = ativas

    for lista in canais_por_cat.values():
        ordenar_lista(lista)

    ordenar_lista(canais_adultos)

    # Remover canais 100% fora do ar (0 links ativos) quando a rede estiver saudável
    removidos_mortos = 0
    if vivos > 0 and taxa >= 0.25:
        for cat in list(canais_por_cat.keys()):
            antes = len(canais_por_cat[cat])
            canais_por_cat[cat] = [
                ch for ch in canais_por_cat[cat]
                if any(f.get("ativo") for f in ch.get("fontes", []))
            ]
            removidos_mortos += antes - len(canais_por_cat[cat])
        antes_ad = len(canais_adultos)
        canais_adultos = [
            ch for ch in canais_adultos
            if any(f.get("ativo") for f in ch.get("fontes", []))
        ]
        removidos_mortos += antes_ad - len(canais_adultos)
        if removidos_mortos:
            print(f"Limpeza automática: {removidos_mortos} canal(is) 100% fora do ar removido(s).")

    # Salva relatório de saúde dos links para aviso no painel.html
    try:
        from datetime import datetime, timezone
        links_testados = len(resultado)
        links_caidos = max(0, links_testados - vivos)
        saude_path = os.path.join(REPO_ROOT, "data", "saude_links.json")
        os.makedirs(os.path.dirname(saude_path), exist_ok=True)
        with open(saude_path, "w", encoding="utf-8") as fp:
            json.dump({
                "data": datetime.now(timezone.utc).isoformat(),
                "links_testados": links_testados,
                "links_ativos": vivos,
                "links_caidos": links_caidos,
                "canais_removidos": removidos_mortos,
            }, fp, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Aviso ao salvar data/saude_links.json: {e}")

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
    canais = [ch for ch in sorted(canais_adultos, key=lambda x: x.get("ordem", 0)) if not eh_conteudo_gay_ou_trans(ch.get("nome", ""))]
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
    canais = [ch for ch in sorted(canais_adultos, key=lambda x: x.get("ordem", 0)) if not eh_conteudo_gay_ou_trans(ch.get("nome", ""))]
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
    canais = [ch for ch in sorted(canais_adultos, key=lambda x: x.get("ordem", 0)) if not eh_conteudo_gay_ou_trans(ch.get("nome", ""))]
    for ch in canais:
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
    processar_listas_recebidas_painel()
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
    processar_listas_recebidas_painel()
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

    # 9. Gera o catálogo VOD fatiado (pasta vod/) para o app TV & Filmes
    try:
        import gerar_vod
        gerar_vod.gerar_vod_fatiado(OUTPUT_VOD)
    except Exception as e:
        print(f"Aviso ao gerar catálogo VOD fatiado: {e}")

if __name__ == "__main__":
    if "--aovivo" in sys.argv:
        main_aovivo()
    else:
        main()
