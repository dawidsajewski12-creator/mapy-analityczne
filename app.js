// Sample data from provided JSON

// POPRAWIONA WERSJA - Funkcje do wizualizacji danych symulacji wiatru z prawidłowymi współrzędnymi

// Zmienna globalna dla danych symulacji wiatru
let windSimulationData = null;

// WSPÓŁRZĘDNE SUWAŁK (gdzie wykonano symulację)
const SIMULATION_CENTER = {
    lat: 54.1118,
    lng: 22.9309
};

// Rozmiar obszaru symulacji w stopniach (dostosowane do rzeczywistego rozmiaru symulacji)
const SIMULATION_BOUNDS = {
    width: 0.02,  // ~2.2 km w Suwałkach  
    height: 0.01  // ~1.1 km w Suwałkach
};

// Funkcja do wczytania danych symulacji wiatru z GitHub
async function loadWindSimulationData() {
    try {
        const response = await fetch('data/wind_simulation_results.json');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        windSimulationData = await response.json();
        console.log('Dane symulacji wiatru załadowane:', windSimulationData.metadata);

        // Po załadowaniu danych, zaktualizuj mapę na Suwałki i pokaż wizualizację
        if (maps.wind) {
            // Ustaw mapę na Suwałki gdzie wykonano symulację
            maps.wind.setView([SIMULATION_CENTER.lat, SIMULATION_CENTER.lng], 15);
            updateWindVisualizationWithRealData();
        }

        return windSimulationData;
    } catch (error) {
        console.error('Błąd podczas ładowania danych symulacji wiatru:', error);
        return null;
    }
}

// Funkcja do konwersji współrzędnych lokalnych na geograficzne (Suwałki)
function localToGeo(x, y) {
    // Normalizuj do zakresu 0-1
    const normalizedX = x / 20.0; // max X w danych
    const normalizedY = y / 10.66; // max Y w danych

    // Mapuj na obszar wokół Suwałk
    const lat = SIMULATION_CENTER.lat - SIMULATION_BOUNDS.height/2 + normalizedY * SIMULATION_BOUNDS.height;
    const lng = SIMULATION_CENTER.lng - SIMULATION_BOUNDS.width/2 + normalizedX * SIMULATION_BOUNDS.width;

    return [lat, lng];
}

// Główna funkcja wizualizacji z rzeczywistymi danymi
function updateWindVisualizationWithRealData() {
    if (!maps.wind || !windSimulationData) return;

    console.log('Aktualizacja wizualizacji wiatru z rzeczywistymi danymi...');

    // Wyczyść istniejące warstwy
    maps.wind.eachLayer(layer => {
        if (layer instanceof L.Marker || layer instanceof L.Polyline || layer instanceof L.Circle) {
            maps.wind.removeLayer(layer);
        }
    });

    // Usuń istniejącą warstwę heatmap jeśli istnieje
    if (window.currentHeatLayer) {
        maps.wind.removeLayer(window.currentHeatLayer);
    }

    // Dodaj kontinuous heatmap prędkości wiatru
    addContinuousWindHeatmap();

    // Dodaj streamlines
    addStreamlines();

    // Dodaj particles jeśli włączone
    const showParticlesCheckbox = document.getElementById('show-particles');
    if (showParticlesCheckbox && showParticlesCheckbox.checked) {
        addWindParticles();
    }

    // Dodaj wektory jeśli włączone
    const showVectorsCheckbox = document.getElementById('show-vectors');
    if (showVectorsCheckbox && showVectorsCheckbox.checked) {
        addWindVectors();
    }

    // Dodaj informację o lokalizacji
    addLocationInfo();
}

// KONTINUOUS HEATMAP - prawdziwa mapa cieplna
function addContinuousWindHeatmap() {
    const magnitudeGrid = windSimulationData.magnitude_grid;
    const gridHeight = magnitudeGrid.length;
    const gridWidth = magnitudeGrid[0].length;

    console.log(`Tworzenie kontinuous heatmap ${gridWidth}x${gridHeight}...`);

    // Przygotuj punkty dla leaflet heatmap
    const heatPoints = [];

    for (let i = 0; i < gridHeight; i++) {
        for (let j = 0; j < gridWidth; j++) {
            // Mapuj indeksy siatki na współrzędne lokalne
            const localX = (j / (gridWidth - 1)) * 20.0;
            const localY = (i / (gridHeight - 1)) * 10.66;

            // Konwertuj na współrzędne geograficzne
            const [lat, lng] = localToGeo(localX, localY);

            const magnitude = magnitudeGrid[i][j];

            // Normalizuj intensywność (0-1) względem maksymalnej prędkości
            const maxMagnitude = windSimulationData.flow_statistics.max_magnitude;
            const intensity = Math.min(magnitude / maxMagnitude, 1.0);

            heatPoints.push([lat, lng, intensity]);
        }
    }

    // Stwórz kontinuous heatmap z leaflet-heat
    if (typeof L.heatLayer !== 'undefined') {
        const heatOptions = {
            radius: 30,           // Większy promień dla płynniejszego przejścia
            blur: 25,            // Więcej rozmycia dla kontinuous efektu
            maxZoom: 18,
            max: 1.0,
            gradient: {
                0.0: '#0000FF',   // Niebieski - niska prędkość
                0.2: '#00FFFF',   // Cyan
                0.4: '#00FF00',   // Zielony  
                0.6: '#FFFF00',   // Żółty
                0.8: '#FF8000',   // Pomarańczowy
                1.0: '#FF0000'    // Czerwony - wysoka prędkość
            }
        };

        window.currentHeatLayer = L.heatLayer(heatPoints, heatOptions).addTo(maps.wind);

        console.log(`Kontinuous heatmap utworzona z ${heatPoints.length} punktów`);
    } else {
        console.error('Plugin leaflet-heat nie jest załadowany!');
        // Fallback - pokolorowane kółka (ale mniejsze i z większą przezroczystością)
        addFallbackHeatmap(heatPoints);
    }
}

// Fallback jeśli nie ma pluginu leaflet-heat
function addFallbackHeatmap(heatPoints) {
    console.log('Używam fallback heatmap...');

    for (let i = 0; i < heatPoints.length; i += 4) { // Co 4-ty punkt żeby nie przeciążać
        const [lat, lng, intensity] = heatPoints[i];

        const color = getWindColorFromIntensity(intensity);

        L.circle([lat, lng], {
            color: color,
            fillColor: color,
            fillOpacity: 0.4,
            weight: 0,
            radius: 15
        }).addTo(maps.wind);
    }
}

// Funkcja dla streamlines z prawidłowymi współrzędnymi
function addStreamlines() {
    console.log(`Dodaję ${windSimulationData.streamlines.length} streamlines...`);

    windSimulationData.streamlines.forEach((streamline, index) => {
        const latlngs = streamline.map(point => localToGeo(point.x, point.y));

        // Różne kolory dla różnych streamlines
        const hue = (index * 40) % 360;

        const polyline = L.polyline(latlngs, {
            color: `hsl(${hue}, 70%, 50%)`,
            weight: 3,
            opacity: 0.9
        }).addTo(maps.wind);

        polyline.bindPopup(`
            <strong>Streamline ${index + 1}</strong><br>
            Punkty: ${streamline.length}<br>
            Prędkość średnia: ${(streamline.reduce((sum, p) => sum + p.speed, 0) / streamline.length).toFixed(2)} m/s
        `);
    });
}

// Funkcja dla cząstek
function addWindParticles() {
    if (windSimulationData.particles.length > 0) {
        console.log('Dodaję cząstki wiatru...');

        // Użyj pierwszej grupy cząstek
        const particleGroup = windSimulationData.particles[0];

        particleGroup.forEach((particle, index) => {
            const [lat, lng] = localToGeo(particle.x, particle.y);

            const marker = L.circleMarker([lat, lng], {
                radius: 4,
                fillColor: getParticleColor(particle.speed),
                color: '#000',
                weight: 1,
                opacity: 1,
                fillOpacity: 0.8
            }).bindPopup(`
                <strong>Cząstka ${index + 1}</strong><br>
                Prędkość: ${particle.speed.toFixed(2)} m/s<br>
                Wiek: ${particle.age}<br>
                Pozycja: (${particle.x.toFixed(2)}, ${particle.y.toFixed(2)})
            `).addTo(maps.wind);
        });
    }
}

// Funkcja dla wektorów wiatru
function addWindVectors() {
    console.log('Dodaję wektory wiatru...');

    windSimulationData.vector_field.forEach((vector, index) => {
        const [lat, lng] = localToGeo(vector.x, vector.y);

        // Oblicz końcowy punkt wektora (skalowanie dla wizualizacji)
        const scale = 0.0005;
        const endLat = lat + vector.vy * scale;
        const endLng = lng + vector.vx * scale;

        // Strzałka wektora
        const arrow = L.polyline([[lat, lng], [endLat, endLng]], {
            color: getWindColorFromMagnitude(vector.magnitude),
            weight: 3,
            opacity: 0.9
        }).bindPopup(`
            <strong>Wektor wiatru</strong><br>
            Składowa X: ${vector.vx.toFixed(2)} m/s<br>
            Składowa Y: ${vector.vy.toFixed(2)} m/s<br>
            Magnitude: ${vector.magnitude.toFixed(2)} m/s<br>
            Pozycja: (${vector.x}, ${vector.y})
        `).addTo(maps.wind);

        // Punkt początku wektora
        L.circleMarker([lat, lng], {
            radius: 3,
            fillColor: '#000',
            color: '#fff',
            weight: 1,
            opacity: 1,
            fillOpacity: 1
        }).addTo(maps.wind);
    });
}

// Dodaj informację o lokalizacji i danych
function addLocationInfo() {
    const infoControl = L.control({ position: 'topright' });

    infoControl.onAdd = function(map) {
        const div = L.DomUtil.create('div', 'wind-info-panel');
        div.style.background = 'rgba(255, 255, 255, 0.9)';
        div.style.padding = '10px';
        div.style.borderRadius = '5px';
        div.style.boxShadow = '0 2px 5px rgba(0,0,0,0.2)';
        div.style.fontSize = '12px';
        div.style.maxWidth = '250px';

        const stats = windSimulationData.flow_statistics;

        div.innerHTML = `
            <h4 style="margin: 0 0 8px 0; color: #333;">Symulacja CFD - Suwałki</h4>
            <div><strong>Lokalizacja:</strong> ${SIMULATION_CENTER.lat.toFixed(4)}°N, ${SIMULATION_CENTER.lng.toFixed(4)}°E</div>
            <div><strong>Czas obliczeń:</strong> ${windSimulationData.metadata.computation_time}s</div>
            <div><strong>Prędkość min:</strong> ${stats.min_magnitude.toFixed(2)} m/s</div>
            <div><strong>Prędkość max:</strong> ${stats.max_magnitude.toFixed(2)} m/s</div>
            <div><strong>Prędkość średnia:</strong> ${stats.mean_magnitude.toFixed(2)} m/s</div>
            <div><strong>Turbulencja:</strong> ${(stats.turbulence_intensity * 100).toFixed(1)}%</div>
        `;

        return div;
    };

    infoControl.addTo(maps.wind);
}

// Funkcje pomocnicze dla kolorów
function getWindColorFromIntensity(intensity) {
    // Gradient kolorów dla intensywności 0-1
    if (intensity < 0.2) return '#0000FF';
    if (intensity < 0.4) return '#00FFFF'; 
    if (intensity < 0.6) return '#00FF00';
    if (intensity < 0.8) return '#FFFF00';
    return '#FF0000';
}

function getWindColorFromMagnitude(magnitude) {
    const maxMag = windSimulationData ? windSimulationData.flow_statistics.max_magnitude : 5;
    const intensity = Math.min(magnitude / maxMag, 1);
    return getWindColorFromIntensity(intensity);
}

function getParticleColor(speed) {
    const maxSpeed = windSimulationData ? windSimulationData.flow_statistics.max_magnitude : 5;
    const intensity = Math.min(speed / maxSpeed, 1);
    const hue = (1 - intensity) * 240; // Od niebieskiego do czerwonego
    return `hsl(${hue}, 70%, 50%)`;
}


const sampleData = {
  weatherData: {
    temperature: 22.5,
    humidity: 65,
    pressure: 1013.2,
    windSpeed: 12.8,
    windDirection: 245,
    location: "Warszawa",
    description: "Pochmurno z przelotnymi opadami"
  },
  floodData: {
    scenarios: [
      {
        id: 1,
        name: "Opady 50mm/h",
        duration: 120,
        maxDepth: 1.5,
        affectedArea: 850,
        coordinates: [
          {lat: 52.2297, lng: 21.0122, depth: 0.8, time: 30},
          {lat: 52.2305, lng: 21.0135, depth: 1.2, time: 45},
          {lat: 52.2312, lng: 21.0118, depth: 0.6, time: 60},
          {lat: 52.2285, lng: 21.0140, depth: 0.9, time: 75},
          {lat: 52.2320, lng: 21.0100, depth: 1.1, time: 90}
        ]
      },
      {
        id: 2,
        name: "Opady ekstremalne 100mm/h", 
        duration: 180,
        maxDepth: 2.8,
        affectedArea: 1200,
        coordinates: [
          {lat: 52.2290, lng: 21.0115, depth: 1.5, time: 20},
          {lat: 52.2298, lng: 21.0128, depth: 2.2, time: 35},
          {lat: 52.2310, lng: 21.0105, depth: 2.0, time: 50},
          {lat: 52.2275, lng: 21.0145, depth: 2.5, time: 65}
        ]
      }
    ]
  },
  windData: {
    scenarios: [
      {
        name: "Wiatr miejski - dzień",
        windSpeed: 8.5,
        direction: 225,
        turbulence: 0.3,
        particles: [
          {x: 100, y: 150, vx: 2.5, vy: -0.8},
          {x: 120, y: 140, vx: 3.1, vy: -1.2},
          {x: 140, y: 135, vx: 2.8, vy: -0.9}
        ]
      },
      {
        name: "Silny wiatr - burza",
        windSpeed: 25.2,
        direction: 270,
        turbulence: 0.8,
        particles: [
          {x: 80, y: 160, vx: 8.2, vy: -2.5},
          {x: 110, y: 145, vx: 9.1, vy: -3.8}
        ]
      }
    ]
  },
  thermalComfort: {
    zones: [
      {lat: 52.2297, lng: 21.0122, pmv: -0.5, ppd: 12, utci: 24.2, comfort: "Komfortowo"},
      {lat: 52.2305, lng: 21.0135, pmv: 1.2, ppd: 28, utci: 29.1, comfort: "Ciepło"},
      {lat: 52.2312, lng: 21.0118, pmv: -1.8, ppd: 45, utci: 18.5, comfort: "Chłodno"},
      {lat: 52.2285, lng: 21.0140, pmv: 0.2, ppd: 8, utci: 25.1, comfort: "Komfortowo"},
      {lat: 52.2320, lng: 21.0100, pmv: 2.1, ppd: 55, utci: 32.8, comfort: "Gorąco"}
    ],
    parameters: {
      airTemp: 26.5,
      humidity: 55,
      airVelocity: 0.8,
      meanRadiantTemp: 28.2
    }
  },
  projects: [
    {
      id: 1,
      title: "Analiza Zagrożenia Powodziowego - Centrum Warszawy",
      type: "Symulacja Powodzi",
      date: "2024-08-15",
      location: "Warszawa",
      description: "Kompleksowa analiza ryzyka powodziowego dla śródmieścia Warszawy z uwzględnieniem infrastruktury miejskiej.",
      image: "https://via.placeholder.com/400x300/1e40af/ffffff?text=Analiza+Powodzi",
      tags: ["HEC-RAS", "GIS", "Hydrologia"],
      results: "Zidentyfikowano 5 obszarów krytycznych",
      category: "flood"
    },
    {
      id: 2,
      title: "Optymalizacja Wentylacji Naturalnej - Kompleks Biurowy",
      type: "Analiza Wiatru",
      date: "2024-07-22",
      location: "Kraków",
      description: "Symulacja CFD przepływu powietrza wokół planowanego kompleksu biurowego w celu optymalizacji komfortu.",
      image: "https://via.placeholder.com/400x300/059669/ffffff?text=CFD+Analiza",
      tags: ["CFD", "ANSYS", "Aerodynamika"],
      results: "30% poprawa wentylacji naturalnej",
      category: "wind"
    },
    {
      id: 3,
      title: "Mapa Komfortu Termicznego - Park Miejski",
      type: "Komfort Termiczny",
      date: "2024-06-10",
      location: "Gdańsk",
      description: "Ocena bioklimatyczna przestrzeni publicznych z rekomendacjami zagospodarowania zieleni.",
      image: "https://via.placeholder.com/400x300/dc2626/ffffff?text=Komfort+Termiczny",
      tags: ["UTCI", "PMV", "Bioklimat"],
      results: "Plan nasadzeń zieleni wysokiej",
      category: "thermal"
    },
    {
      id: 4,
      title: "Modelowanie Rozprzestrzeniania Zanieczyszczeń",
      type: "Jakość Powietrza",
      date: "2024-05-18",
      location: "Wrocław",
      description: "Analiza dyspersji zanieczyszczeń atmosferycznych w rejonie dużego węzła komunikacyjnego.",
      image: "https://via.placeholder.com/400x300/7c3aed/ffffff?text=Jakość+Powietrza",
      tags: ["AERMOD", "Dispersion", "PM2.5"],
      results: "Rekomendacje lokalizacji monitoringu",
      category: "other"
    },
    {
      id: 5,
      title: "Symulacja Fal Upału - Wyspa Ciepła",
      type: "Klimat Miejski", 
      date: "2024-04-25",
      location: "Łódź",
      description: "Modelowanie temperatury powierzchni i powietrza podczas ekstremalnych upałów w centrum miasta.",
      image: "https://via.placeholder.com/400x300/ea580c/ffffff?text=Wyspa+Ciepła",
      tags: ["UHI", "Landsat", "Termografia"],
      results: "Mapa intensywności wyspy ciepła",
      category: "other"
    },
    {
      id: 6,
      title: "Ocena Ryzyka Osunięć Ziemi",
      type: "Geomechanika",
      date: "2024-03-12",
      location: "Zakopane",
      description: "Analiza stabilności stoków w rejonie zabudowy górskiej z uwzględnieniem zmian klimatycznych.",
      image: "https://via.placeholder.com/400x300/16a34a/ffffff?text=Stabilność+Stoków",
      tags: ["Slope Stability", "LIDAR", "Geotechnika"],
      results: "Klasyfikacja ryzyka geotechnicznego",
      category: "other"
    }
  ],
  blogPosts: [
    {
      id: 1,
      title: "Nowoczesne Metody Modelowania Powodzi Miejskich",
      excerpt: "Przegląd najnowszych technik symulacji hydraulicznej w środowisku zurbanizowanym, w tym modele 1D/2D i ich zastosowania praktyczne.",
      date: "2024-09-05",
      category: "Hydrologia",
      readTime: "8 min"
    },
    {
      id: 2,
      title: "CFD w Planowaniu Urbanistycznym - Case Study",
      excerpt: "Jak symulacje obliczeniowej mechaniki płynów mogą wspomóc projektowanie przestrzeni miejskich przyjaznych pieszym.",
      date: "2024-08-28",
      category: "Aerodynamika",
      readTime: "12 min"
    },
    {
      id: 3,
      title: "Wskaźniki Komfortu Bioklimatycznego - PMV vs UTCI",
      excerpt: "Porównanie różnych metod oceny komfortu termicznego człowieka w przestrzeniach zewnętrznych.",
      date: "2024-08-15",
      category: "Bioklimat",
      readTime: "6 min"
    },
    {
      id: 4,
      title: "Teledetekcja w Monitoringu Środowiska Miejskiego",
      excerpt: "Zastosowanie danych satelitarnych i LIDAR w analizach przestrzennych dla potrzeb zarządzania miastem.",
      date: "2024-07-30",
      category: "Geomatyka", 
      readTime: "10 min"
    }
  ]
};

// Global variables
let maps = {};
let animationPlaying = false;
let animationInterval = null;
let particles = [];
let windCanvas = null;
let windCtx = null;
let floodMarkers = [];
let thermalMarkers = [];

// Initialize the application
document.addEventListener('DOMContentLoaded', function() {
    // Załaduj dane symulacji wiatru z GitHub na początku
    loadWindSimulationData();


  initNavigation();
  initWeatherWidget();
  initMaps();
  initControls();
  initPortfolio();
  initBlog();
  initContactForm();
  initParticleSystem();
  initThemeToggle();
});

// Navigation functions
function initNavigation() {
  const navToggle = document.getElementById('nav-toggle');
  const navMenu = document.getElementById('nav-menu');
  const navLinks = document.querySelectorAll('.nav__link');

  // Mobile menu toggle
  if (navToggle) {
    navToggle.addEventListener('click', () => {
      navMenu.classList.toggle('active');
    });
  }

  // Smooth scrolling for navigation links
  navLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const targetId = link.getAttribute('href').substring(1);
      const targetSection = document.getElementById(targetId);
      
      if (targetSection) {
        const headerHeight = document.getElementById('header').offsetHeight;
        const targetPosition = targetSection.offsetTop - headerHeight;
        
        window.scrollTo({
          top: targetPosition,
          behavior: 'smooth'
        });
      }
      
      // Close mobile menu if open
      navMenu.classList.remove('active');
    });
  });

  // Quick access cards navigation
  const quickCards = document.querySelectorAll('.quick-card');
  quickCards.forEach(card => {
    card.addEventListener('click', (e) => {
      e.preventDefault();
      const targetId = card.getAttribute('href').substring(1);
      const targetSection = document.getElementById(targetId);
      
      if (targetSection) {
        const headerHeight = document.getElementById('header').offsetHeight;
        const targetPosition = targetSection.offsetTop - headerHeight;
        
        window.scrollTo({
          top: targetPosition,
          behavior: 'smooth'
        });
      }
    });
  });

  // Header scroll effect
  window.addEventListener('scroll', () => {
    const header = document.getElementById('header');
    if (window.scrollY > 100) {
      header.style.background = 'rgba(15, 23, 42, 0.95)';
    } else {
      header.style.background = 'rgba(15, 23, 42, 0.9)';
    }
  });
}

// Theme toggle
function initThemeToggle() {
  const themeToggle = document.getElementById('theme-toggle');
  const themeIcon = themeToggle.querySelector('i');
  
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      document.body.classList.toggle('light-theme');
      
      if (document.body.classList.contains('light-theme')) {
        themeIcon.className = 'fas fa-sun';
      } else {
        themeIcon.className = 'fas fa-moon';
      }
    });
  }
}

// Weather widget
function initWeatherWidget() {
  const weatherData = sampleData.weatherData;
  
  document.getElementById('weather-location').textContent = weatherData.location;
  document.getElementById('temperature').textContent = `${weatherData.temperature}°C`;
  document.getElementById('humidity').textContent = `${weatherData.humidity}%`;
  document.getElementById('pressure').textContent = `${weatherData.pressure} hPa`;
  document.getElementById('wind-speed').textContent = `${weatherData.windSpeed} km/h`;
}

// Map initialization
function initMaps() {
  // Main dashboard map
  initMainMap();
  
  // Flood simulation map
  initFloodMap();
  
  // Wind analysis map
  initWindMap();
  
  // Thermal comfort map
  initThermalMap();
  
  // Contact map
  initContactMap();
}

function initMainMap() {
  const mapContainer = document.getElementById('main-map');
  if (!mapContainer) return;

  maps.main = L.map('main-map').setView([52.2297, 21.0122], 11);
  
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
  }).addTo(maps.main);

  // Add sample markers for different data layers
  const floodMarker = L.marker([52.2297, 21.0122])
    .bindPopup('<b>Zagrożenie powodziowe</b><br>Głębokość: 0.8m')
    .addTo(maps.main);

  const thermalMarker = L.marker([52.2305, 21.0135])
    .bindPopup('<b>Komfort termiczny</b><br>PMV: -0.5 (Komfortowo)')
    .addTo(maps.main);

  // Layer control
  const layerControls = {
    'flood-layer': floodMarker,
    'thermal-layer': thermalMarker
  };

  Object.keys(layerControls).forEach(layerId => {
    const checkbox = document.getElementById(layerId);
    if (checkbox) {
      checkbox.addEventListener('change', (e) => {
        const layer = layerControls[layerId];
        if (e.target.checked) {
          maps.main.addLayer(layer);
        } else {
          maps.main.removeLayer(layer);
        }
      });
    }
  });
}

function initFloodMap() {
  const mapContainer = document.getElementById('flood-map');
  if (!mapContainer) return;

  maps.flood = L.map('flood-map').setView([52.2297, 21.0122], 13);
  
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
  }).addTo(maps.flood);

  updateFloodVisualization();
}

function initWindMap() {
  const mapContainer = document.getElementById('wind-map');
  if (!mapContainer) return;

  maps.wind = L.map('wind-map').setView([52.2297, 21.0122], 14);
  
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
  }).addTo(maps.wind);

  updateWindVisualization();
}

function initThermalMap() {
  const mapContainer = document.getElementById('thermal-map');
  if (!mapContainer) return;

  maps.thermal = L.map('thermal-map').setView([52.2297, 21.0122], 14);
  
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
  }).addTo(maps.thermal);

  updateThermalVisualization();
}

function initContactMap() {
  const mapContainer = document.getElementById('contact-map');
  if (!mapContainer) return;

  maps.contact = L.map('contact-map').setView([52.2297, 21.0122], 15);
  
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
  }).addTo(maps.contact);

  L.marker([52.2297, 21.0122])
    .bindPopup('<b>Biuro</b><br>ul. Naukowa 15/20<br>00-001 Warszawa')
    .addTo(maps.contact);
}

// Control initialization
function initControls() {
  // Flood simulation controls
  const floodScenario = document.getElementById('flood-scenario');
  const timeSlider = document.getElementById('time-slider');
  const timeValue = document.getElementById('time-value');
  const playButton = document.getElementById('play-animation');

  if (floodScenario) {
    floodScenario.addEventListener('change', updateFloodVisualization);
  }
  
  if (timeSlider && timeValue) {
    timeSlider.addEventListener('input', (e) => {
      timeValue.textContent = e.target.value;
      updateFloodVisualization();
    });
  }

  if (playButton) {
    playButton.addEventListener('click', toggleFloodAnimation);
  }

  // Wind analysis controls
  const windScenario = document.getElementById('wind-scenario');
  const windSpeedSlider = document.getElementById('wind-speed-slider');
  const windSpeedDisplay = document.getElementById('wind-speed-display');
  const showParticles = document.getElementById('show-particles');
  const showVectors = document.getElementById('show-vectors');

  if (windScenario) {
    windScenario.addEventListener('change', updateWindVisualization);
  }
  
  if (windSpeedSlider && windSpeedDisplay) {
    windSpeedSlider.addEventListener('input', (e) => {
      windSpeedDisplay.textContent = e.target.value;
      updateWindVisualization();
    });
  }

  if (showParticles) {
    showParticles.addEventListener('change', updateParticleVisibility);
  }

  if (showVectors) {
    showVectors.addEventListener('change', updateVectorVisibility);
  }

  // Thermal comfort controls
  const comfortIndex = document.getElementById('comfort-index');
  const airTempSlider = document.getElementById('air-temp-slider');
  const airTempDisplay = document.getElementById('air-temp-display');
  const airVelocitySlider = document.getElementById('air-velocity-slider');
  const airVelocityDisplay = document.getElementById('air-velocity-display');

  if (comfortIndex) {
    comfortIndex.addEventListener('change', updateThermalVisualization);
  }
  
  if (airTempSlider && airTempDisplay) {
    airTempSlider.addEventListener('input', (e) => {
      airTempDisplay.textContent = e.target.value;
      updateThermalVisualization();
    });
  }
  
  if (airVelocitySlider && airVelocityDisplay) {
    airVelocitySlider.addEventListener('input', (e) => {
      airVelocityDisplay.textContent = e.target.value;
      updateThermalVisualization();
    });
  }
}

// Flood simulation functions
function updateFloodVisualization() {
  if (!maps.flood) return;

  // Clear existing markers
  floodMarkers.forEach(marker => maps.flood.removeLayer(marker));
  floodMarkers = [];

  const scenarioSelect = document.getElementById('flood-scenario');
  const timeSlider = document.getElementById('time-slider');
  
  if (!scenarioSelect || !timeSlider) return;

  const scenarioIndex = parseInt(scenarioSelect.value);
  const currentTime = parseInt(timeSlider.value);
  const scenario = sampleData.floodData.scenarios[scenarioIndex];

  if (scenario) {
    scenario.coordinates.forEach(coord => {
      if (currentTime >= coord.time) {
        const color = getFloodColor(coord.depth);
        const radius = coord.depth * 50;

        const marker = L.circle([coord.lat, coord.lng], {
          color: color,
          fillColor: color,
          fillOpacity: 0.6,
          radius: radius
        }).bindPopup(`<b>Głębokość: ${coord.depth}m</b><br>Czas: ${coord.time} min`);
        
        marker.addTo(maps.flood);
        floodMarkers.push(marker);
      }
    });
  }
}

function getFloodColor(depth) {
  if (depth < 0.5) return '#3B82F6';
  if (depth < 1) return '#10B981';
  if (depth < 2) return '#F59E0B';
  return '#EF4444';
}

function toggleFloodAnimation() {
  const playButton = document.getElementById('play-animation');
  const timeSlider = document.getElementById('time-slider');

  if (!playButton || !timeSlider) return;

  if (animationPlaying) {
    clearInterval(animationInterval);
    animationPlaying = false;
    playButton.innerHTML = '<i class="fas fa-play"></i> Odtwórz';
  } else {
    animationPlaying = true;
    playButton.innerHTML = '<i class="fas fa-pause"></i> Zatrzymaj';
    
    animationInterval = setInterval(() => {
      let currentTime = parseInt(timeSlider.value);
      currentTime += 5;
      
      if (currentTime > 180) {
        currentTime = 0;
      }
      
      timeSlider.value = currentTime;
      document.getElementById('time-value').textContent = currentTime;
      updateFloodVisualization();
    }, 500);
  }
}

// Wind analysis functions
function updateWindVisualization() {
  if (!maps.wind) return;

  // Clear existing markers
  maps.wind.eachLayer(layer => {
    if (layer instanceof L.Marker && layer.options.icon && layer.options.icon.options.className === 'wind-marker') {
      maps.wind.removeLayer(layer);
    }
  });

  const scenarioSelect = document.getElementById('wind-scenario');
  if (!scenarioSelect) return;

  const scenarioIndex = parseInt(scenarioSelect.value);
  const scenario = sampleData.windData.scenarios[scenarioIndex];

  if (scenario) {
    // Add wind speed markers
    const markers = [
      {lat: 52.2297, lng: 21.0122, speed: scenario.windSpeed * 0.8},
      {lat: 52.2305, lng: 21.0135, speed: scenario.windSpeed * 1.2},
      {lat: 52.2312, lng: 21.0118, speed: scenario.windSpeed * 0.9}
    ];

    markers.forEach(marker => {
      const color = getWindColor(marker.speed);
      L.marker([marker.lat, marker.lng], {
        icon: L.divIcon({
          className: 'wind-marker',
          html: `<div style="background: ${color}; width: 12px; height: 12px; border-radius: 50%; border: 2px solid white;"></div>`,
          iconSize: [16, 16]
        })
      }).bindPopup(`<b>Prędkość wiatru: ${marker.speed.toFixed(1)} m/s</b>`)
        .addTo(maps.wind);
    });
  }

  updateParticles();
}

function getWindColor(speed) {
  if (speed < 5) return '#10B981';
  if (speed < 15) return '#F59E0B';
  return '#EF4444';
}

// Thermal comfort functions
function updateThermalVisualization() {
  if (!maps.thermal) return;

  // Clear existing markers
  thermalMarkers.forEach(marker => maps.thermal.removeLayer(marker));
  thermalMarkers = [];

  const indexSelect = document.getElementById('comfort-index');
  if (!indexSelect) return;

  const index = indexSelect.value;
  
  sampleData.thermalComfort.zones.forEach(zone => {
    let value, label;
    
    switch(index) {
      case 'pmv':
        value = zone.pmv;
        label = `PMV: ${value}`;
        break;
      case 'ppd':
        value = zone.ppd;
        label = `PPD: ${value}%`;
        break;
      case 'utci':
        value = zone.utci;
        label = `UTCI: ${value}°C`;
        break;
    }

    const color = getComfortColor(zone.comfort);
    
    const marker = L.circle([zone.lat, zone.lng], {
      color: color,
      fillColor: color,
      fillOpacity: 0.6,
      radius: 100
    }).bindPopup(`<b>${label}</b><br>${zone.comfort}`);
    
    marker.addTo(maps.thermal);
    thermalMarkers.push(marker);
  });
}

function getComfortColor(comfort) {
  switch(comfort) {
    case 'Chłodno': return '#3B82F6';
    case 'Komfortowo': return '#10B981';
    case 'Ciepło': return '#F59E0B';
    case 'Gorąco': return '#EF4444';
    default: return '#EF4444';
  }
}

// Particle system for wind visualization
function initParticleSystem() {
  windCanvas = document.getElementById('wind-particles');
  if (!windCanvas) return;

  windCtx = windCanvas.getContext('2d');
  resizeCanvas();
  
  window.addEventListener('resize', resizeCanvas);
  
  // Initialize particles
  for (let i = 0; i < 50; i++) {
    particles.push(createParticle());
  }
  
  animateParticles();
}

function resizeCanvas() {
  if (!windCanvas || !windCanvas.parentElement) return;
  
  windCanvas.width = windCanvas.parentElement.offsetWidth;
  windCanvas.height = windCanvas.parentElement.offsetHeight;
}

function createParticle() {
  return {
    x: Math.random() * (windCanvas?.width || 800),
    y: Math.random() * (windCanvas?.height || 500),
    vx: (Math.random() - 0.5) * 4,
    vy: (Math.random() - 0.5) * 2,
    life: Math.random() * 100 + 50,
    maxLife: 150
  };
}

function updateParticles() {
  const scenarioSelect = document.getElementById('wind-scenario');
  const windSpeedSlider = document.getElementById('wind-speed-slider');
  
  if (!scenarioSelect || !windSpeedSlider) return;

  const scenarioIndex = parseInt(scenarioSelect.value);
  const windSpeed = parseFloat(windSpeedSlider.value);
  
  // Update particle velocities based on wind
  particles.forEach(particle => {
    particle.vx = (Math.random() - 0.3) * windSpeed * 0.3;
    particle.vy = (Math.random() - 0.5) * windSpeed * 0.1;
  });
}

function animateParticles() {
  if (!windCtx || !windCanvas) return;
  
  windCtx.clearRect(0, 0, windCanvas.width, windCanvas.height);
  
  const showParticlesCheckbox = document.getElementById('show-particles');
  const showParticles = showParticlesCheckbox ? showParticlesCheckbox.checked : true;
  
  if (showParticles) {
    particles.forEach((particle, index) => {
      // Update position
      particle.x += particle.vx;
      particle.y += particle.vy;
      particle.life--;
      
      // Reset particle if it goes off screen or dies
      if (particle.x < 0 || particle.x > windCanvas.width || 
          particle.y < 0 || particle.y > windCanvas.height || 
          particle.life <= 0) {
        particles[index] = createParticle();
        return;
      }
      
      // Draw particle
      const alpha = particle.life / particle.maxLife;
      windCtx.fillStyle = `rgba(50, 184, 198, ${alpha})`;
      windCtx.beginPath();
      windCtx.arc(particle.x, particle.y, 2, 0, Math.PI * 2);
      windCtx.fill();
    });
  }
  
  requestAnimationFrame(animateParticles);
}

function updateParticleVisibility() {
  // Particles visibility is handled in animateParticles function
}

function updateVectorVisibility() {
  // Vector visibility would be implemented here for more advanced wind visualization
}

// Portfolio functions
function initPortfolio() {
  renderPortfolio();
  initPortfolioFilters();
  initProjectModal();
}

function renderPortfolio() {
  const portfolioGrid = document.getElementById('portfolio-grid');
  if (!portfolioGrid) return;

  portfolioGrid.innerHTML = sampleData.projects.map(project => `
    <div class="project-card" data-category="${project.category}" data-id="${project.id}">
      <img src="${project.image}" alt="${project.title}" loading="lazy">
      <div class="project-card__content">
        <div class="project-card__meta">
          <span>${project.type}</span>
          <span>${project.date}</span>
        </div>
        <h3>${project.title}</h3>
        <p>${project.description}</p>
        <div class="project-tags">
          ${project.tags.map(tag => `<span class="project-tag">${tag}</span>`).join('')}
        </div>
      </div>
    </div>
  `).join('');

  // Add click handlers
  document.querySelectorAll('.project-card').forEach(card => {
    card.addEventListener('click', () => {
      const projectId = parseInt(card.dataset.id);
      showProjectModal(projectId);
    });
  });
}

function initPortfolioFilters() {
  const filterButtons = document.querySelectorAll('.filter-btn');
  const projectCards = document.querySelectorAll('.project-card');

  filterButtons.forEach(button => {
    button.addEventListener('click', () => {
      const filter = button.dataset.filter;

      // Update active button
      filterButtons.forEach(btn => btn.classList.remove('active'));
      button.classList.add('active');

      // Filter projects
      projectCards.forEach(card => {
        if (filter === 'all' || card.dataset.category === filter) {
          card.style.display = 'block';
        } else {
          card.style.display = 'none';
        }
      });
    });
  });
}

function initProjectModal() {
  const modal = document.getElementById('project-modal');
  const overlay = document.getElementById('modal-overlay');
  const closeBtn = document.getElementById('modal-close');

  [overlay, closeBtn].forEach(element => {
    if (element) {
      element.addEventListener('click', () => {
        modal.classList.add('hidden');
      });
    }
  });
}

function showProjectModal(projectId) {
  const project = sampleData.projects.find(p => p.id === projectId);
  if (!project) return;

  const modalBody = document.getElementById('modal-body');
  modalBody.innerHTML = `
    <img src="${project.image}" alt="${project.title}" style="width: 100%; border-radius: 8px; margin-bottom: 16px;">
    <h2>${project.title}</h2>
    <div style="display: flex; justify-content: space-between; margin-bottom: 16px; font-size: 14px; color: var(--color-text-secondary);">
      <span>${project.type}</span>
      <span>${project.location}</span>
      <span>${project.date}</span>
    </div>
    <p>${project.description}</p>
    <div style="margin: 20px 0;">
      <h4>Wyniki:</h4>
      <p>${project.results}</p>
    </div>
    <div>
      <h4>Technologie:</h4>
      <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px;">
        ${project.tags.map(tag => `<span class="project-tag">${tag}</span>`).join('')}
      </div>
    </div>
  `;

  document.getElementById('project-modal').classList.remove('hidden');
}

// Blog functions
function initBlog() {
  const blogGrid = document.getElementById('blog-grid');
  if (!blogGrid) return;

  blogGrid.innerHTML = sampleData.blogPosts.map(post => `
    <article class="blog-card">
      <div class="blog-card__meta">
        <span class="blog-category">${post.category}</span>
        <span>${post.readTime}</span>
      </div>
      <h3>${post.title}</h3>
      <p>${post.excerpt}</p>
      <div style="margin-top: 16px; font-size: 14px; color: var(--color-text-secondary);">
        ${post.date}
      </div>
    </article>
  `).join('');
}

// Contact form
function initContactForm() {
  const form = document.getElementById('contact-form');
  
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      
      const formData = new FormData(form);
      const data = Object.fromEntries(formData);
      
      // Show loading
      showLoadingSpinner();
      
      // Simulate form submission
      setTimeout(() => {
        hideLoadingSpinner();
        alert('Dziękujemy za wiadomość! Skontaktujemy się wkrótce.');
        form.reset();
      }, 2000);
    });
  }
}

// Utility functions
function showLoadingSpinner() {
  const spinner = document.getElementById('loading-spinner');
  if (spinner) {
    spinner.classList.remove('hidden');
  }
}

function hideLoadingSpinner() {
  const spinner = document.getElementById('loading-spinner');
  if (spinner) {
    spinner.classList.add('hidden');
  }
}

// Resize maps on window resize
window.addEventListener('resize', () => {
  Object.values(maps).forEach(map => {
    setTimeout(() => {
      map.invalidateSize();
    }, 100);
  });
  
  resizeCanvas();
});

// Intersection Observer for animations
const observerOptions = {
  threshold: 0.1,
  rootMargin: '0px 0px -50px 0px'
};

const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.style.opacity = '1';
      entry.target.style.transform = 'translateY(0)';
    }
  });
}, observerOptions);

// Observe all sections for scroll animations
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.section').forEach(section => {
    section.style.opacity = '0';
    section.style.transform = 'translateY(30px)';
    section.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
    observer.observe(section);
  });
});

// Obsługa checkboxów dla cząstek i wektorów
document.addEventListener('DOMContentLoaded', function() {
    const showParticlesCheckbox = document.getElementById('show-particles');
    if (showParticlesCheckbox) {
        showParticlesCheckbox.addEventListener('change', function() {
            if (maps.wind && windSimulationData) {
                updateWindVisualizationWithRealData();
            }
        });
    }

    const showVectorsCheckbox = document.getElementById('show-vectors');
    if (showVectorsCheckbox) {
        showVectorsCheckbox.addEventListener('change', function() {
            if (maps.wind && windSimulationData) {
                updateWindVisualizationWithRealData();
            }
        });
    }
});
