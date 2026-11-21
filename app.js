// Estado Global da Aplicação
const state = {
  canais: [],
  destaques: [],
  favoritos: JSON.parse(localStorage.getItem('play_favs') || '[]'),
  categoriaAtiva: 'Todos',
  abaAtiva: 'canais',
  buscaQuery: '',
  canalAtual: null,
  fonteIndex: 0,
  hlsInstance: null
};

// Elementos DOM
const dom = {
  tabCanais: document.getElementById('tabCanais'),
  tabFilmes: document.getElementById('tabFilmes'),
  tabFavoritos: document.getElementById('tabFavoritos'),
  channelsGrid: document.getElementById('channelsGrid'),
  destaquesContainer: document.getElementById('destaquesContainer'),
  favoritesGrid: document.getElementById('favoritesGrid'),
  categoriesBar: document.getElementById('categoriesBar'),
  searchInput: document.getElementById('searchInput'),
  clearSearch: document.getElementById('clearSearch'),
  canaisCount: document.getElementById('canaisCount'),
  favsCount: document.getElementById('favsCount'),
  playerModal: document.getElementById('playerModal'),
  videoElement: document.getElementById('videoElement'),
  playerTitle: document.getElementById('playerTitle'),
  playerSourceBadge: document.getElementById('playerSourceBadge'),
  playerLoader: document.getElementById('playerLoader'),
  loaderMessage: document.getElementById('loaderMessage'),
  playerToast: document.getElementById('playerToast'),
  btnSources: document.getElementById('btnSources'),
  sourcesMenu: document.getElementById('sourcesMenu'),
  btnFavModal: document.getElementById('btnFavModal'),
  btnClosePlayer: document.getElementById('btnClosePlayer'),
  btnPip: document.getElementById('btnPip'),
  btnFullscreen: document.getElementById('btnFullscreen')
};

// Inicialização
document.addEventListener('DOMContentLoaded', async () => {
  setupTabs();
  setupSearch();
  setupPlayerControls();
  await carregarDados();
});

// 1. Carregamento de Dados
async function carregarDados() {
  try {
    const [resCanais, resDest] = await Promise.all([
      fetch('data/canais.json'),
      fetch('data/destaques.json')
    ]);
    state.canais = await resCanais.json();
    state.destaques = await resDest.json();

    renderCategories();
    renderCanais();
    renderFilmes();
    renderFavoritos();
  } catch (err) {
    console.error("Erro ao carregar dados:", err);
    dom.channelsGrid.innerHTML = `<p class="empty-state">Erro ao carregar lista de canais. Verifique sua conexão.</p>`;
  }
}

// 2. Navegação entre Abas
function setupTabs() {
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.tab;
      state.abaAtiva = target;

      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      if (target === 'canais') dom.tabCanais.classList.add('active');
      if (target === 'filmes') dom.tabFilmes.classList.add('active');
      if (target === 'favoritos') {
        dom.tabFavoritos.classList.add('active');
        renderFavoritos();
      }
    });
  });
}

// 3. Categorias (Pílulas)
function renderCategories() {
  const catsSet = new Set(['Todos']);
  state.canais.forEach(c => {
    if (c.categoria) catsSet.add(c.categoria);
  });

  dom.categoriesBar.innerHTML = '';
  catsSet.forEach(cat => {
    const btn = document.createElement('button');
    btn.className = `category-pill ${cat === state.categoriaAtiva ? 'active' : ''}`;
    btn.textContent = cat;
    btn.addEventListener('click', () => {
      document.querySelectorAll('.category-pill').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      state.categoriaAtiva = cat;
      renderCanais();
    });
    dom.categoriesBar.appendChild(btn);
  });
}

// 4. Renderizar Canais
function renderCanais() {
  let filtrados = state.canais;

  if (state.categoriaAtiva !== 'Todos') {
    filtrados = filtrados.filter(c => c.categoria === state.categoriaAtiva);
  }

  if (state.buscaQuery) {
    const q = state.buscaQuery.toLowerCase();
    filtrados = filtrados.filter(c => c.nome.toLowerCase().includes(q) || (c.categoria && c.categoria.toLowerCase().includes(q)));
  }

  dom.canaisCount.textContent = `${filtrados.length} canais`;
  dom.channelsGrid.innerHTML = '';

  if (filtrados.length === 0) {
    dom.channelsGrid.innerHTML = `<p class="empty-state">Nenhum canal encontrado.</p>`;
    return;
  }

  filtrados.forEach(c => {
    const isFav = state.favoritos.includes(c.id);
    const card = document.createElement('div');
    card.className = 'channel-card';
    card.innerHTML = `
      <button class="channel-fav-btn ${isFav ? 'active' : ''}" title="Favoritar">★</button>
      <div class="channel-logo-wrapper">
        <img class="channel-logo" src="${c.logo}" alt="${c.nome}" loading="lazy" onerror="this.src='https://cdn-icons-png.flaticon.com/512/3075/3075908.png'">
      </div>
      <div class="channel-name" title="${c.nome}">${c.nome}</div>
      <div class="channel-category">${c.categoria || 'Geral'}</div>
    `;

    card.querySelector('.channel-fav-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleFavorito(c.id);
      renderCanais();
    });

    card.addEventListener('click', () => abrirPlayer(c, 0));
    dom.channelsGrid.appendChild(card);
  });
}

// 5. Renderizar Filmes e Séries (Carrosséis)
function renderFilmes() {
  dom.destaquesContainer.innerHTML = '';
  if (!state.destaques || state.destaques.length === 0) {
    dom.destaquesContainer.innerHTML = `<p class="empty-state">Carregando catálogo de filmes...</p>`;
    return;
  }

  state.destaques.forEach(secao => {
    const row = document.createElement('div');
    row.className = 'movie-row';
    row.innerHTML = `
      <h2 class="movie-row-title">${secao.nome}</h2>
      <div class="movie-carousel">
        ${secao.itens.map(item => `
          <div class="movie-card" data-titulo="${item.titulo}">
            <div class="movie-poster-box">
              <img class="movie-poster" src="${item.poster}" alt="${item.titulo}" loading="lazy">
              ${item.ano ? `<span class="movie-year-badge">${item.ano}</span>` : ''}
            </div>
            <div class="movie-card-title" title="${item.titulo}">${item.titulo}</div>
          </div>
        `).join('')}
      </div>
    `;

    // Click em um filme
    row.querySelectorAll('.movie-card').forEach(card => {
      card.addEventListener('click', () => {
        const tit = card.dataset.titulo;
        abrirFilme(tit);
      });
    });

    dom.destaquesContainer.appendChild(row);
  });
}

// 6. Player Inteligente com FAILOVER AUTOMÁTICO
function abrirPlayer(item, fonteIndex = 0) {
  state.canalAtual = item;
  state.fonteIndex = fonteIndex;
  dom.playerTitle.textContent = item.nome;
  dom.playerModal.classList.remove('hidden');

  atualizarMenuFontes();
  atualizarBotaoFavModal();
  iniciarReproducao();
}

function iniciarReproducao() {
  const item = state.canalAtual;
  const fontes = item.fontes || [];

  if (fontes.length === 0) {
    mostrarToast("Nenhuma fonte cadastrada para este canal.");
    return;
  }

  if (state.fonteIndex >= fontes.length) {
    state.fonteIndex = 0; // Volta para o início se esgotou
  }

  const fonte = fontes[state.fonteIndex];
  const url = fonte.url;
  const rotuloFonte = fonte.nome || `Fonte ${state.fonteIndex + 1}`;
  dom.playerSourceBadge.textContent = `${rotuloFonte} ${state.fonteIndex === 0 ? '(Principal)' : '(Reserva)'}`;

  dom.playerLoader.classList.remove('hidden');
  dom.loaderMessage.textContent = `Conectando à ${rotuloFonte}...`;

  // Limpa HLS anterior
  if (state.hlsInstance) {
    state.hlsInstance.destroy();
    state.hlsInstance = null;
  }

  const video = dom.videoElement;

  // Se o browser suportar HLS nativo (Safari no iPhone)
  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
    video.play().catch(e => console.log("Play aguardando interação:", e));

    video.onerror = () => {
      tentarProximaFonte("Falha na reprodução nativa");
    };

    video.onloadeddata = () => {
      dom.playerLoader.classList.add('hidden');
    };
  } else if (Hls.isSupported()) {
    // Usar Hls.js no Chrome, Firefox, Android, TV
    const hls = new Hls({
      enableWorker: true,
      manifestLoadingTimeOut: 6000,
      manifestLoadingMaxRetry: 1,
      levelLoadingTimeOut: 6000
    });
    state.hlsInstance = hls;

    hls.loadSource(url);
    hls.attachMedia(video);

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      dom.playerLoader.classList.add('hidden');
      video.play().catch(e => console.log("Play bloqueado pelo navegador:", e));
    });

    hls.on(Hls.Events.ERROR, (event, data) => {
      if (data.fatal) {
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            tentarProximaFonte("Fonte instável ou erro 404/403");
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            hls.recoverMediaError();
            break;
          default:
            tentarProximaFonte("Erro crítico na transmissão");
            break;
        }
      }
    });
  } else {
    // MP4 direto ou vídeo padrão
    video.src = url;
    video.play();
  }
}

// Algoritmo de Troca Automática de Fonte (Failover)
function tentarProximaFonte(motivo) {
  const item = state.canalAtual;
  const fontes = item.fontes || [];

  if (fontes.length <= 1) {
    mostrarToast(`⚠️ O sinal está fora do ar no momento.`);
    dom.playerLoader.classList.add('hidden');
    return;
  }

  state.fonteIndex++;
  if (state.fonteIndex >= fontes.length) {
    mostrarToast(`⚠️ Todas as fontes deste canal falharam no momento.`);
    dom.playerLoader.classList.add('hidden');
    state.fonteIndex = 0;
    return;
  }

  const proxima = fontes[state.fonteIndex].nome || `Fonte ${state.fonteIndex + 1}`;
  mostrarToast(`⚡ ${motivo}. Alternando automaticamente para ${proxima}...`);

  setTimeout(() => {
    iniciarReproducao();
    atualizarMenuFontes();
  }, 1000);
}

// Menu de Seleção Manual de Fontes
function atualizarMenuFontes() {
  const fontes = state.canalAtual?.fontes || [];
  dom.sourcesMenu.innerHTML = '';

  fontes.forEach((f, idx) => {
    const btn = document.createElement('button');
    btn.className = `dropdown-item ${idx === state.fonteIndex ? 'active' : ''}`;
    btn.textContent = `${f.nome || `Fonte ${idx + 1}`} ${idx === state.fonteIndex ? '✓' : ''}`;
    btn.addEventListener('click', () => {
      state.fonteIndex = idx;
      dom.sourcesMenu.classList.add('hidden');
      iniciarReproducao();
    });
    dom.sourcesMenu.appendChild(btn);
  });
}

// Notificação Flutuante (Toast)
let toastTimer = null;
function mostrarToast(msg) {
  clearTimeout(toastTimer);
  dom.playerToast.textContent = msg;
  dom.playerToast.classList.remove('hidden');
  toastTimer = setTimeout(() => {
    dom.playerToast.classList.add('hidden');
  }, 4000);
}

// 7. Controles do Player
function setupPlayerControls() {
  dom.btnClosePlayer.addEventListener('click', fecharPlayer);

  dom.btnSources.addEventListener('click', (e) => {
    e.stopPropagation();
    dom.sourcesMenu.classList.toggle('hidden');
  });

  document.addEventListener('click', () => {
    dom.sourcesMenu.classList.add('hidden');
  });

  dom.btnPip.addEventListener('click', async () => {
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
      } else if (dom.videoElement.requestPictureInPicture) {
        await dom.videoElement.requestPictureInPicture();
      }
    } catch (e) {
      console.log("PiP não suportado:", e);
    }
  });

  dom.btnFullscreen.addEventListener('click', () => {
    if (!document.fullscreenElement) {
      dom.playerModal.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  dom.btnFavModal.addEventListener('click', () => {
    if (state.canalAtual) {
      toggleFavorito(state.canalAtual.id);
      atualizarBotaoFavModal();
      renderCanais();
    }
  });
}

function fecharPlayer() {
  dom.playerModal.classList.add('hidden');
  if (state.hlsInstance) {
    state.hlsInstance.destroy();
    state.hlsInstance = null;
  }
  dom.videoElement.pause();
  dom.videoElement.src = '';
}

// 8. Busca
function setupSearch() {
  dom.searchInput.addEventListener('input', (e) => {
    state.buscaQuery = e.target.value.trim();
    dom.clearSearch.classList.toggle('hidden', !state.buscaQuery);
    renderCanais();
  });

  dom.clearSearch.addEventListener('click', () => {
    dom.searchInput.value = '';
    state.buscaQuery = '';
    dom.clearSearch.classList.add('hidden');
    renderCanais();
  });
}

// 9. Favoritos
function toggleFavorito(id) {
  const idx = state.favoritos.indexOf(id);
  if (idx >= 0) {
    state.favoritos.splice(idx, 1);
  } else {
    state.favoritos.push(id);
  }
  localStorage.setItem('play_favs', JSON.stringify(state.favoritos));
}

function atualizarBotaoFavModal() {
  const isFav = state.canalAtual && state.favoritos.includes(state.canalAtual.id);
  dom.btnFavModal.style.color = isFav ? '#ffd700' : '#ffffff';
}

function renderFavoritos() {
  const favs = state.canais.filter(c => state.favoritos.includes(c.id));
  dom.favsCount.textContent = `${favs.length} itens`;
  dom.favoritesGrid.innerHTML = '';

  if (favs.length === 0) {
    dom.favoritesGrid.innerHTML = `<p class="empty-state">Nenhum favorito salvo ainda. Clique na estrela (★) para favoritar!</p>`;
    return;
  }

  favs.forEach(c => {
    const card = document.createElement('div');
    card.className = 'channel-card';
    card.innerHTML = `
      <button class="channel-fav-btn active" title="Remover dos favoritos">★</button>
      <div class="channel-logo-wrapper">
        <img class="channel-logo" src="${c.logo}" alt="${c.nome}" loading="lazy">
      </div>
      <div class="channel-name" title="${c.nome}">${c.nome}</div>
      <div class="channel-category">${c.categoria || 'Geral'}</div>
    `;

    card.querySelector('.channel-fav-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleFavorito(c.id);
      renderFavoritos();
      renderCanais();
    });

    card.addEventListener('click', () => abrirPlayer(c, 0));
    dom.favoritesGrid.appendChild(card);
  });
}

// Reproduzir filme pelo título
function abrirFilme(titulo) {
  // Abrir modal com busca pelo stream ou direto
  abrirPlayer({
    id: 'vod_' + Date.now(),
    nome: titulo,
    categoria: 'Filmes',
    logo: 'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba',
    fontes: [
      { nome: 'Opção 1', url: `https://saimo-tv.pages.dev/api/stream?tipo=movie&id=602223&versao=dub` }
    ]
  }, 0);
}
