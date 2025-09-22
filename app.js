// Sample data from provided JSON
// === PODSTAWOWE FUNKCJE ŁADOWANIA DANYCH ===
let windSimulationData = null;

async function loadWindSimulationData() {
    try {
        const response = await fetch('data/wind_simulation_results.json');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        windSimulationData = await response.json();
        console.log('Dane symulacji wiatru załadowane:', windSimulationData.metadata);

        // Po załadowaniu danych, dodaj CSS i zainicjalizuj zaawansowaną wizualizację
        if (maps.wind) {
            addAdvancedWindCSS();
            initAdvancedWindVisualization();
        }

        return windSimulationData;
    } catch (error) {
        console.error('Błąd podczas ładowania danych symulacji wiatru:', error);
        return null;
    }
}


// === ZAAWANSOWANA WIZUALIZACJA WIATRU - INTEGRACJA Z DZIAŁAJĄCYM KODEM ===

// Parametry konfiguracyjne wizualizacji
const WIND_VIZ_CONFIG = {
    PARTICLE_COUNT: 6000,
    PARTICLE_SPEED_SCALE: 0.2,
    PARTICLE_LIFESPAN: 1000,
    PARTICLE_LINE_WIDTH: 1.6,
    PARTICLE_COLOR: "rgba(110, 190, 255, 0.8)",
    GLOW_COLOR: "rgba(110, 190, 255, 0.5)",
    GLOW_BLUR: 7
};

// Funkcja mapowania wartości na kolor (skala Viridis)
function getViridisColor(value, min, max) {
    const t = Math.max(0, Math.min(1, (value - min) / (max - min)));
    const r = Math.round(255 * (0.267004 + 1.15172 * t - 2.92336 * t**2 + 1.52013 * t**3));
    const g = Math.round(255 * (0.018623 + 2.75701 * t - 4.49472 * t**2 + 1.77533 * t**3));
    const b = Math.round(255 * (0.354456 - 2.11226 * t + 10.5126 * t**2 - 12.3881 * t**3 + 3.63582 * t**4));
    return `rgba(${r},${g},${b},0.6)`;
}

// Adapter danych - przekształca nasze dane na format oczekiwany przez wizualizację
function createWindDataAdapter(rawWindData) {
    if (!rawWindData) return null;
    
    // NOWE: Sprawdź czy dane zawierają współrzędne geograficzne
    const hasGeoCoords = rawWindData.vector_field && 
                        rawWindData.vector_field.length > 0 && 
                        rawWindData.vector_field[0].longitude !== undefined;
    
    if (!hasGeoCoords) {
        console.error('Dane symulacji nie zawierają współrzędnych geograficznych!');
        return null;
    }
    
    // NOWE: Użyj prawdziwych bounds z danych zamiast hardkodowania
    const bounds_wgs84 = rawWindData.spatial_reference?.bounds_wgs84;
    if (!bounds_wgs84) {
        console.error('Brak informacji o bounds_wgs84 w danych symulacji!');
        return null;
    }
    
    const bounds = [
        [bounds_wgs84.south, bounds_wgs84.west],   // SW corner
        [bounds_wgs84.north, bounds_wgs84.east]    // NE corner
    ];
    
    console.log('Używam prawdziwych bounds z danych:', bounds);
    
    const adapter = {
        // Format danych zgodny z oczekiwaniami wizualizacji
        magnitudeGrid: rawWindData.magnitude_grid,
        gridWidth: rawWindData.magnitude_grid[0].length,
        gridHeight: rawWindData.magnitude_grid.length,
        bounds: bounds,  // NOWE: prawdziwe bounds
        minMagnitude: rawWindData.flow_statistics.min_magnitude,
        maxMagnitude: rawWindData.flow_statistics.max_magnitude,
        
        // NOWE: Użyj prawdziwych współrzędnych geograficznych
        streamlines: rawWindData.streamlines.map(streamline => 
            streamline.map(point => ({
                ...point,
                lat: point.latitude,   // NOWE: użyj prawdziwych współrzędnych
                lng: point.longitude
            }))
        ),
        
        // NOWE: Użyj prawdziwych współrzędnych dla particles
        particles: rawWindData.particles.length > 0 ? 
            rawWindData.particles[0].map(particle => ({
                ...particle,
                lat: particle.latitude,   // NOWE: użyj prawdziwych współrzędnych
                lng: particle.longitude
            })) : [],
        
        // NOWE: Użyj prawdziwych współrzędnych dla vector field
        vectorField: rawWindData.vector_field.map(vector => ({
            ...vector,
            lat: vector.latitude,     // NOWE: użyj prawdziwych współrzędnych
            lng: vector.longitude
        })),
        
        // Metadane
        metadata: rawWindData.metadata,
        performance: rawWindData.performance,
        spatial_reference: rawWindData.spatial_reference  // NOWE: dodaj info o CRS
    };
    
    return adapter;
}

// === VelocityLayer - Warstwa pola prędkości ===
const AdvancedVelocityLayer = L.Layer.extend({
    initialize: function(data, bounds) {
        this._data = data;
        this._bounds = L.latLngBounds(bounds);
    },

    onAdd: function(map) {
        this._map = map;
        this._canvas = L.DomUtil.create('canvas', 'leaflet-zoom-animated velocity-canvas');
        this._canvas.style.position = 'absolute';
        map.getPanes().overlayPane.appendChild(this._canvas);
        this._ctx = this._canvas.getContext('2d');

        map.on('moveend zoomend resize', this._reset, this);
        this._reset();
    },

    onRemove: function(map) {
        map.getPanes().overlayPane.removeChild(this._canvas);
        map.off('moveend zoomend resize', this._reset, this);
    },

    _reset: function() {
        const topLeft = this._map.containerPointToLayerPoint([0, 0]);
        L.DomUtil.setPosition(this._canvas, topLeft);

        const size = this._map.getSize();
        this._canvas.width = size.x;
        this._canvas.height = size.y;
        this._canvas.style.width = size.x + 'px';
        this._canvas.style.height = size.y + 'px';

        this._draw();
    },

    _draw: function() {
        if (!this._data.magnitudeGrid) return;

        const ctx = this._ctx;
        ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);

        const grid = this._data.magnitudeGrid;
        const w = this._data.gridWidth;
        const h = this._data.gridHeight;
        const { minMagnitude, maxMagnitude } = this._data;

        const cellWidth = (this._bounds.getEast() - this._bounds.getWest()) / w;
        const cellHeight = (this._bounds.getNorth() - this._bounds.getSouth()) / h;

        for (let j = 0; j < h; j++) {
            for (let i = 0; i < w; i++) {
                const lat = this._bounds.getNorth() - ((j + 0.5) * cellHeight);
                const lon = this._bounds.getWest() + ((i + 0.5) * cellWidth);
                const point = this._map.latLngToContainerPoint([lat, lon]);

                const value = grid[j] && grid[j][i] !== undefined ? grid[j][i] : NaN;
                if (!isFinite(value)) continue;

                ctx.fillStyle = getViridisColor(value, minMagnitude, maxMagnitude);
                ctx.fillRect(Math.round(point.x - 2), Math.round(point.y - 2), 4, 4);
            }
        }
    }
});

// === WindAnimationLayer - Warstwa animacji cząstek ===
const AdvancedWindAnimationLayer = L.Layer.extend({
    initialize: function(data, bounds) {
        this._data = data;
        this._bounds = L.latLngBounds(bounds);
        this._particles = [];
        this._animationFrame = null;
    },

    onAdd: function(map) {
        this._map = map;
        this._canvas = L.DomUtil.create('canvas', 'leaflet-zoom-animated wind-canvas');
        this._canvas.id = 'wind-canvas';
        this._canvas.style.position = 'absolute';
        this._canvas.style.pointerEvents = 'none';

        map.getPanes().overlayPane.appendChild(this._canvas);
        this._ctx = this._canvas.getContext('2d');

        map.on('moveend zoomend resize', this._reset, this);
        this._reset();
        this._initializeParticles();
        this._animate();
    },

    onRemove: function(map) {
        if (this._animationFrame) {
            cancelAnimationFrame(this._animationFrame);
            this._animationFrame = null;
        }
        map.getPanes().overlayPane.removeChild(this._canvas);
        map.off('moveend zoomend resize', this._reset, this);
    },

    _reset: function() {
        const topLeft = this._map.containerPointToLayerPoint([0, 0]);
        L.DomUtil.setPosition(this._canvas, topLeft);

        const size = this._map.getSize();
        this._canvas.width = size.x;
        this._canvas.height = size.y;
        this._canvas.style.width = size.x + 'px';
        this._canvas.style.height = size.y + 'px';

        this._initializeParticles();
    },

    _initializeParticles: function() {
        this._particles = [];

        // Użyj rzeczywistych cząstek z danych jeśli są dostępne
        if (this._data.particles && this._data.particles.length > 0) {
            const sourceParticles = this._data.particles.slice(0, Math.min(WIND_VIZ_CONFIG.PARTICLE_COUNT, this._data.particles.length));

            sourceParticles.forEach(particle => {
                const point = this._map.latLngToContainerPoint([particle.lat, particle.lng]);
                if (point.x >= 0 && point.x < this._canvas.width && point.y >= 0 && point.y < this._canvas.height) {
                    this._particles.push({
                        x: point.x,
                        y: point.y,
                        vx: particle.vx * WIND_VIZ_CONFIG.PARTICLE_SPEED_SCALE,
                        vy: -particle.vy * WIND_VIZ_CONFIG.PARTICLE_SPEED_SCALE, // odwróć Y
                        age: Math.random() * WIND_VIZ_CONFIG.PARTICLE_LIFESPAN,
                        speed: particle.speed
                    });
                }
            });
        } else {
            // Fallback - generuj losowe cząstki
            for (let i = 0; i < WIND_VIZ_CONFIG.PARTICLE_COUNT; i++) {
                this._particles.push(this._createRandomParticle());
            }
        }
    },

    _createRandomParticle: function() {
        return {
            x: Math.random() * this._canvas.width,
            y: Math.random() * this._canvas.height,
            vx: (Math.random() - 0.5) * 4,
            vy: (Math.random() - 0.5) * 4,
            age: Math.random() * WIND_VIZ_CONFIG.PARTICLE_LIFESPAN,
            speed: Math.random() * 3 + 1
        };
    },

    _animate: function() {
        this._ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);

        // Ustawienia canvas dla efektu świecenia
        this._ctx.globalCompositeOperation = 'screen';
        this._ctx.lineWidth = WIND_VIZ_CONFIG.PARTICLE_LINE_WIDTH;

        this._particles.forEach((particle, index) => {
            // Aktualizuj pozycję
            const oldX = particle.x;
            const oldY = particle.y;

            particle.x += particle.vx;
            particle.y += particle.vy;
            particle.age++;

            // Sprawdź granice i resetuj cząstkę jeśli wyszła poza obszar lub jest za stara
            if (particle.x < 0 || particle.x > this._canvas.width || 
                particle.y < 0 || particle.y > this._canvas.height || 
                particle.age > WIND_VIZ_CONFIG.PARTICLE_LIFESPAN) {
                this._particles[index] = this._createRandomParticle();
                return;
            }

            // Narysuj ślad cząstki
            const alpha = Math.max(0, 1 - particle.age / WIND_VIZ_CONFIG.PARTICLE_LIFESPAN);
            this._ctx.strokeStyle = WIND_VIZ_CONFIG.PARTICLE_COLOR.replace('0.8', alpha.toString());

            this._ctx.beginPath();
            this._ctx.moveTo(oldX, oldY);
            this._ctx.lineTo(particle.x, particle.y);
            this._ctx.stroke();
        });

        this._animationFrame = requestAnimationFrame(() => this._animate());
    }
});

// === LegendControl - Kontrolka legendy ===
const AdvancedLegendControl = L.Control.extend({
    options: {
        position: 'bottomright'
    },

    onAdd: function(map) {
        this._container = L.DomUtil.create('div', 'leaflet-control legend-control');
        this.update();
        return this._container;
    },

    update: function(min = 0, max = 1) {
        const gradientColors = [];
        for (let i = 0; i <= 100; i += 10) {
            gradientColors.push(getViridisColor(min + (i/100)*(max-min), min, max));
        }

        this._container.innerHTML = `
            <h4>Prędkość wiatru [m/s]</h4>
            <div class="legend-gradient" style="background: linear-gradient(to right, ${gradientColors.join(', ')});"></div>
            <div class="legend-labels">
                <span>${min.toFixed(1)}</span>
                <span>${max.toFixed(1)}</span>
            </div>
        `;
    }
});

// === GŁÓWNA FUNKCJA ZAAWANSOWANEJ WIZUALIZACJI ===
function initAdvancedWindVisualization() {
    if (!maps.wind || !windSimulationData) {
        console.warn('Mapa wiatru lub dane nie są dostępne');
        return;
    }

    console.log('Inicjalizacja zaawansowanej wizualizacji wiatru...');

    // Przekształć dane na oczekiwany format
    const adaptedData = createWindDataAdapter(windSimulationData);
    if (!adaptedData) {
        console.error('Nie udało się zaadaptować danych');
        return;
    }

    // Wyczyść istniejące warstwy
    maps.wind.eachLayer(layer => {
        if (layer instanceof L.Marker || layer instanceof L.Polyline || layer instanceof L.Circle) {
            maps.wind.removeLayer(layer);
        }
    });

    // Usuń istniejącą warstwę heatmap
    if (window.currentHeatLayer) {
        maps.wind.removeLayer(window.currentHeatLayer);
    }

    // Usuń poprzednie zaawansowane warstwy jeśli istnieją
    if (window.advancedLayers) {
        window.advancedLayers.forEach(layer => {
            if (maps.wind.hasLayer(layer)) {
                maps.wind.removeLayer(layer);
            }
        });
    }

    // Utwórz nowe zaawansowane warstwy
    const velocityLayer = new AdvancedVelocityLayer(adaptedData, adaptedData.bounds);
    const windAnimLayer = new AdvancedWindAnimationLayer(adaptedData, adaptedData.bounds);
    const legend = new AdvancedLegendControl();

    // Dodaj warstwy do mapy
    velocityLayer.addTo(maps.wind);
    windAnimLayer.addTo(maps.wind);
    legend.addTo(maps.wind);
    legend.update(adaptedData.minMagnitude, adaptedData.maxMagnitude);

    // Zapisz referencje do warstw
    window.advancedLayers = [velocityLayer, windAnimLayer, legend];

    // Dodaj kontrolki warstw
    if (!window.advancedLayerControl) {
        const overlayMaps = {
            "Pola prędkości": velocityLayer,
            "Przepływ wiatru": windAnimLayer
        };

        window.advancedLayerControl = L.control.layers(null, overlayMaps, { 
            collapsed: false,
            position: 'topright'
        }).addTo(maps.wind);
    }

    // Ustaw mapę na właściwy obszar
    maps.wind.fitBounds(adaptedData.bounds);

    // Dodaj panel informacyjny
    addAdvancedInfoPanel(adaptedData);

    console.log('Zaawansowana wizualizacja wiatru zainicjalizowana');
}

// Funkcja dodania panelu informacyjnego
function addAdvancedInfoPanel(data) {
    // Usuń poprzedni panel jeśli istnieje
    if (window.advancedInfoPanel) {
        maps.wind.removeControl(window.advancedInfoPanel);
    }

    const infoControl = L.control({ position: 'topleft' });

    infoControl.onAdd = function(map) {
        const div = L.DomUtil.create('div', 'advanced-info-panel');
        div.style.background = 'rgba(40, 45, 50, 0.85)';
        div.style.backdropFilter = 'blur(5px)';
        div.style.color = '#f0f0f0';
        div.style.padding = '10px';
        div.style.borderRadius = '8px';
        div.style.boxShadow = '0 2px 10px rgba(0,0,0,0.3)';
        div.style.border = '1px solid rgba(255, 255, 255, 0.1)';
        div.style.maxWidth = '300px';
        div.style.fontSize = '12px';

        div.innerHTML = `
            <h3 style="margin: 0 0 8px 0; color: #ffffff; font-size: 16px;">Symulacja CFD - Suwałki</h3>
            <div><strong>Cząstki:</strong> ${WIND_VIZ_CONFIG.PARTICLE_COUNT.toLocaleString()}</div>
            <div><strong>Siatka:</strong> ${data.gridWidth} × ${data.gridHeight}</div>
            <div><strong>Prędkość min:</strong> ${data.minMagnitude.toFixed(2)} m/s</div>
            <div><strong>Prędkość max:</strong> ${data.maxMagnitude.toFixed(2)} m/s</div>
            <div><strong>Czas obliczeń:</strong> ${data.metadata.computation_time}s</div>
            <div style="margin-top: 8px; font-size: 11px; color: #bbbbbb;">
                Wizualizacja używa skalę kolorów Viridis i animowane cząstki.
            </div>
        `;

        return div;
    };

    infoControl.addTo(maps.wind);
    window.advancedInfoPanel = infoControl;
}

// === CSS Style dla zaawansowanej wizualizacji ===
const advancedWindCSS = `
.advanced-info-panel {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
}

.legend-control {
    background: rgba(40, 45, 50, 0.85) !important;
    backdrop-filter: blur(5px);
    padding: 10px;
    border-radius: 5px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    box-shadow: 0 1px 5px rgba(0,0,0,0.4);
    color: #f0f0f0;
    line-height: 1.2;
}

.legend-control h4 {
    margin: 0 0 5px 0;
    font-size: 14px;
    font-weight: bold;
    text-align: center;
}

.legend-gradient {
    height: 10px;
    width: 150px;
    border-radius: 5px;
}

.legend-labels {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin-top: 3px;
}

.leaflet-control-layers {
    background: rgba(40, 45, 50, 0.85) !important;
    backdrop-filter: blur(5px);
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    box-shadow: 0 1px 5px rgba(0,0,0,0.4);
    color: #f0f0f0 !important;
    border-radius: 5px;
}

.velocity-canvas {
    opacity: 0.8;
}

#wind-canvas {
    pointer-events: none;
}
`;

// Dodaj CSS do strony
function addAdvancedWindCSS() {
    if (!document.getElementById('advanced-wind-css')) {
        const style = document.createElement('style');
        style.id = 'advanced-wind-css';
        style.textContent = advancedWindCSS;
        document.head.appendChild(style);
    }
}

console.log('Zaawansowane komponenty wizualizacji wiatru załadowane');


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
    // Załaduj dane symulacji wiatru na początku
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

// === OBSŁUGA PRZEŁĄCZANIA MIĘDZY WIZUALIZACJAMI ===

// Funkcja przełączania na zaawansowaną wizualizację
function switchToAdvancedVisualization() {
    if (windSimulationData) {
        addAdvancedWindCSS();
        initAdvancedWindVisualization();
    } else {
        console.warn('Dane symulacji nie są załadowane');
    }
}

// Funkcja przełączania na podstawową wizualizację
function switchToBasicVisualization() {
    // Usuń zaawansowane warstwy
    if (window.advancedLayers) {
        window.advancedLayers.forEach(layer => {
            if (maps.wind.hasLayer(layer)) {
                maps.wind.removeLayer(layer);
            }
        });
    }

    // Usuń kontrolki
    if (window.advancedLayerControl) {
        maps.wind.removeControl(window.advancedLayerControl);
        window.advancedLayerControl = null;
    }

    if (window.advancedInfoPanel) {
        maps.wind.removeControl(window.advancedInfoPanel);
        window.advancedInfoPanel = null;
    }

    // Przywróć podstawową wizualizację
    updateWindVisualization();
}

// Globalne funkcje dostępne w konsoli
window.switchToAdvancedVisualization = switchToAdvancedVisualization;
window.switchToBasicVisualization = switchToBasicVisualization;

console.log('Integracja wizualizacji wiatru ukończona');
console.log('Dostępne funkcje:');
console.log('  - switchToAdvancedVisualization() - przełącz na zaawansowaną wizualizację');
console.log('  - switchToBasicVisualization() - przełącz na podstawową wizualizację');
