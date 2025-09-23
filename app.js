/*
================================================================================
APLIKACJA WIZUALIZACJI SYMULACJI WIATRU - POPRAWIONA WERSJA
================================================================================
Kompatybilna z poprawionymi danymi współrzędnych z symulacji
Zawiera dynamiczne streamlines z animacjami strzałek
*/

// ================================================================================
// GLOBALNE ZMIENNE I INICJALIZACJA
// ================================================================================

let windSimulationData = null;
let maps = {}; // POPRAWKA: Inicjalizacja obiektu maps
let currentTheme = localStorage.getItem('theme') || 'dark';

// ================================================================================
// PODSTAWOWE FUNKCJE ŁADOWANIA DANYCH
// ================================================================================

async function loadWindSimulationData() {
    try {
        const response = await fetch('data/wind_simulation_results.json');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        windSimulationData = await response.json();
        console.log('Dane symulacji wiatru załadowane:', windSimulationData.metadata);
        
        // NOWE: Walidacja danych
        if (!windSimulationData.spatial_reference) {
            console.warn('Brak informacji spatial_reference w danych!');
        } else {
            console.log('CRS danych:', windSimulationData.spatial_reference.crs);
            console.log('Bounds WGS84:', windSimulationData.spatial_reference.bounds_wgs84);
        }
        
        // POPRAWKA: Sprawdź czy vector_field ma współrzędne geograficzne
        if (windSimulationData.vector_field && windSimulationData.vector_field.length > 0) {
            const firstPoint = windSimulationData.vector_field[0];
            if (firstPoint.longitude !== undefined && firstPoint.latitude !== undefined) {
                console.log('✅ Dane zawierają współrzędne geograficzne');
                console.log('Przykładowy punkt - pixel:', firstPoint.pixel_x, firstPoint.pixel_y, 
                           'geo:', firstPoint.longitude.toFixed(6), firstPoint.latitude.toFixed(6));
            } else {
                console.warn('⚠️ Brak współrzędnych geograficznych w danych wektorowych');
            }
        }
        
        // DEBUGOWANIE: Sprawdź strukturę danych
        console.log('Loaded data structure:', {
            hasVectorField: !!windSimulationData.vector_field,
            vectorFieldLength: windSimulationData.vector_field?.length || 0,
            hasParticles: !!windSimulationData.particles,
            particlesLength: windSimulationData.particles?.length || 0,
            hasStreamlines: !!windSimulationData.streamlines,
            streamlinesLength: windSimulationData.streamlines?.length || 0,
            hasSpatialRef: !!windSimulationData.spatial_reference
        });
        
    } catch (error) {
        console.error('Błąd ładowania danych symulacji wiatru:', error);
        showNotification('Błąd ładowania danych symulacji', 'error');
    }
}

// ================================================================================
// ADAPTER DANYCH WIATROWYCH - POPRAWIONY
// ================================================================================

function createWindDataAdapter(rawWindData) {
    if (!rawWindData) {
        console.error('Brak danych wejściowych dla adaptera');
        return null;
    }
    
    // POPRAWKA: Sprawdź czy spatial_reference istnieje
    if (!rawWindData.spatial_reference || !rawWindData.spatial_reference.bounds_wgs84) {
        console.error('Brak informacji o bounds_wgs84 w danych');
        return null;
    }
    
    const bounds_wgs84 = rawWindData.spatial_reference.bounds_wgs84;
    
    // POPRAWKA: Tworzenie bounds bezpośrednio jako obiekt L.latLngBounds
    const bounds = L.latLngBounds(
        [bounds_wgs84.south, bounds_wgs84.west], // SW corner
        [bounds_wgs84.north, bounds_wgs84.east]  // NE corner
    );
    
    console.log('✅ Bounds utworzone:', bounds.toString());
    
    return {
        // Podstawowe informacje
        metadata: rawWindData.metadata || {},
        performance: rawWindData.performance || {},
        bounds: bounds,
        
        // Siatka wielkości (dla heatmapy prędkości)
        magnitudeGrid: rawWindData.magnitude_grid || [],
        
        // POPRAWKA: Wektory z właściwą nazwą pola
        vectors: (rawWindData.vector_field || []).map(vector => ({
            ...vector,
            lat: vector.latitude,
            lng: vector.longitude,
            speed: vector.magnitude,
            direction: Math.atan2(vector.vy, vector.vx) * 180 / Math.PI
        })),
        
        // POPRAWKA: Streamlines z współrzędnymi geograficznymi
        streamlines: (rawWindData.streamlines || []).map(streamline => 
            streamline.map(point => ({
                ...point,
                lat: point.latitude,
                lng: point.longitude
            }))
        ),
        
        // POPRAWKA: Particles z poprawną strukturą
        particles: rawWindData.particles && rawWindData.particles.length > 0 ? 
            rawWindData.particles.flatMap(path => path.map(particle => ({
                ...particle,
                lat: particle.latitude,
                lng: particle.longitude
            }))) : [],
        
        // Statystyki przepływu
        flowStatistics: rawWindData.flow_statistics || {}
    };
}

// ================================================================================
// ZAAWANSOWANA WARSTWA PRĘDKOŚCI - POPRAWIONA
// ================================================================================

const AdvancedVelocityLayer = L.Layer.extend({
    initialize: function(data, bounds) {
        this.data = data;
        this.bounds = bounds; // POPRAWKA: Nie wywołuj L.latLngBounds() ponownie
        this.canvas = null;
        this.ctx = null;
        this.animationFrame = null;
    },

    onAdd: function(map) {
        this._map = map;
        this.createCanvas();
        this.draw();
        
        // Event listeners dla zmian mapy
        map.on('viewreset', this.redraw, this);
        map.on('zoom', this.redraw, this);
        map.on('move', this.redraw, this);
    },

    onRemove: function(map) {
        if (this.canvas && this.canvas.parentNode) {
            this.canvas.parentNode.removeChild(this.canvas);
        }
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
        map.off('viewreset', this.redraw, this);
        map.off('zoom', this.redraw, this);
        map.off('move', this.redraw, this);
    },

    createCanvas: function() {
        const size = this._map.getSize();
        this.canvas = L.DomUtil.create('canvas', 'velocity-layer');
        this.canvas.width = size.x;
        this.canvas.height = size.y;
        this.canvas.style.position = 'absolute';
        this.canvas.style.top = '0';
        this.canvas.style.left = '0';
        this.canvas.style.pointerEvents = 'none';
        this.canvas.style.zIndex = 200;
        
        this._map.getPanes().overlayPane.appendChild(this.canvas);
        this.ctx = this.canvas.getContext('2d');
    },

    draw: function() {
        if (!this.data.magnitudeGrid || !this.bounds) {
            console.warn('Brak danych do rysowania velocity layer');
            return;
        }
        
        // POPRAWKA: Walidacja bounds
        if (!this.bounds.isValid || !this.bounds.isValid()) {
            console.error('Invalid bounds in VelocityLayer:', this.bounds);
            return;
        }
        
        const ctx = this.ctx;
        const canvas = this.canvas;
        const map = this._map;
        
        // Wyczyść canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Sprawdź czy siatka ma dane
        const grid = this.data.magnitudeGrid;
        if (!Array.isArray(grid) || grid.length === 0) {
            console.warn('Pusta siatka magnitudeGrid');
            return;
        }
        
        const h = grid.length;
        const w = grid[0] ? grid[0].length : 0;
        
        if (w === 0) {
            console.warn('Nieprawidłowa struktura siatki');
            return;
        }
        
        // Oblicz rozmiary komórek
        const cellWidth = (this.bounds.getEast() - this.bounds.getWest()) / w;
        const cellHeight = (this.bounds.getNorth() - this.bounds.getSouth()) / h;
        
        // Rysuj heatmapę prędkości
        for (let row = 0; row < h; row++) {
            for (let col = 0; col < w; col++) {
                const magnitude = grid[row][col];
                if (magnitude == null || magnitude === 0) continue;
                
                // Współrzędne geograficzne komórki
                const lat = this.bounds.getNorth() - (row + 0.5) * cellHeight;
                const lng = this.bounds.getWest() + (col + 0.5) * cellWidth;
                
                // Konwersja do pikseli mapy
                const point = map.latLngToContainerPoint([lat, lng]);
                
                // Kolor na podstawie prędkości
                const color = this.getVelocityColor(magnitude);
                const alpha = Math.min(magnitude / 5.0, 0.8); // Maksymalna przezroczystość przy 5 m/s
                
                ctx.fillStyle = color;
                ctx.globalAlpha = alpha;
                
                // Rozmiar pikseli dla komórki
                const pixelSize = Math.max(2, map.getZoom() - 10);
                ctx.fillRect(point.x - pixelSize/2, point.y - pixelSize/2, pixelSize, pixelSize);
            }
        }
        
        ctx.globalAlpha = 1.0;
    },

    getVelocityColor: function(magnitude) {
        // Kolorowa skala dla prędkości wiatru
        if (magnitude < 1.0) return 'rgba(0, 255, 255, 0.6)';      // Cyan - bardzo słaby
        if (magnitude < 2.0) return 'rgba(0, 255, 0, 0.7)';       // Zielony - słaby  
        if (magnitude < 4.0) return 'rgba(255, 255, 0, 0.8)';     // Żółty - umiarkowany
        if (magnitude < 6.0) return 'rgba(255, 165, 0, 0.9)';     // Pomarańczowy - silny
        return 'rgba(255, 0, 0, 1.0)';                            // Czerwony - bardzo silny
    },

    redraw: function() {
        if (this.canvas) {
            const size = this._map.getSize();
            this.canvas.width = size.x;
            this.canvas.height = size.y;
            this.draw();
        }
    }
});

// ================================================================================
// DYNAMICZNA WARSTWA STREAMLINES Z ANIMOWANYMI STRZAŁKAMI - NOWA
// ================================================================================

const DynamicStreamlinesLayer = L.Layer.extend({
    initialize: function(data, bounds) {
        this.data = data;
        this.bounds = bounds;
        this.canvas = null;
        this.ctx = null;
        this.animationFrame = null;
        this.animationTime = 0;
        this.isAnimating = false;
        
        // Parametry animacji
        this.arrowSpeed = 50; // pikseli na sekundę
        this.arrowSpacing = 30; // odstęp między strzałkami w pikselach
        this.arrowSize = 8; // rozmiar strzałki
        this.streamlineOpacity = 0.6;
        this.arrowOpacity = 0.9;
    },

    onAdd: function(map) {
        this._map = map;
        this.createCanvas();
        this.startAnimation();
        
        // Event listeners
        map.on('viewreset', this.redraw, this);
        map.on('zoom', this.redraw, this);
        map.on('move', this.redraw, this);
    },

    onRemove: function(map) {
        this.stopAnimation();
        if (this.canvas && this.canvas.parentNode) {
            this.canvas.parentNode.removeChild(this.canvas);
        }
        map.off('viewreset', this.redraw, this);
        map.off('zoom', this.redraw, this);  
        map.off('move', this.redraw, this);
    },

    createCanvas: function() {
        const size = this._map.getSize();
        this.canvas = L.DomUtil.create('canvas', 'streamlines-layer');
        this.canvas.width = size.x;
        this.canvas.height = size.y;
        this.canvas.style.position = 'absolute';
        this.canvas.style.top = '0';
        this.canvas.style.left = '0';
        this.canvas.style.pointerEvents = 'none';
        this.canvas.style.zIndex = 300;
        
        this._map.getPanes().overlayPane.appendChild(this.canvas);
        this.ctx = this.canvas.getContext('2d');
    },

    startAnimation: function() {
        if (this.isAnimating) return;
        this.isAnimating = true;
        this.animationTime = 0;
        this.animate();
    },

    stopAnimation: function() {
        this.isAnimating = false;
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }
    },

    animate: function() {
        if (!this.isAnimating) return;
        
        const now = performance.now();
        const deltaTime = this.lastTime ? now - this.lastTime : 0;
        this.lastTime = now;
        
        this.animationTime += deltaTime;
        this.draw();
        
        this.animationFrame = requestAnimationFrame(() => this.animate());
    },

    draw: function() {
        if (!this.data.streamlines || !this.bounds) {
            return;
        }
        
        const ctx = this.ctx;
        const canvas = this.canvas;
        const map = this._map;
        
        // Wyczyść canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Rysuj każdą streamline
        this.data.streamlines.forEach((streamline, index) => {
            if (!streamline || streamline.length < 2) return;
            
            // Konwertuj punkty streamline do współrzędnych pikseli
            const pixelPoints = streamline.map(point => {
                if (!point.lat || !point.lng) return null;
                return map.latLngToContainerPoint([point.lat, point.lng]);
            }).filter(p => p !== null);
            
            if (pixelPoints.length < 2) return;
            
            // Rysuj linię streamline
            this.drawStreamline(ctx, pixelPoints, index);
            
            // Rysuj animowane strzałki
            this.drawAnimatedArrows(ctx, pixelPoints, streamline, index);
        });
    },

    drawStreamline: function(ctx, points, streamlineIndex) {
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        
        for (let i = 1; i < points.length; i++) {
            ctx.lineTo(points[i].x, points[i].y);
        }
        
        // Gradient kolorów na podstawie indeksu
        const hue = (streamlineIndex * 137.5) % 360; // Golden angle dla równomiernego rozkładu
        ctx.strokeStyle = `hsla(${hue}, 70%, 60%, ${this.streamlineOpacity})`;
        ctx.lineWidth = 2;
        ctx.stroke();
    },

    drawAnimatedArrows: function(ctx, points, streamline, streamlineIndex) {
        if (points.length < 2) return;
        
        // Oblicz całkowitą długość streamline
        let totalLength = 0;
        const segmentLengths = [];
        
        for (let i = 1; i < points.length; i++) {
            const dx = points[i].x - points[i-1].x;
            const dy = points[i].y - points[i-1].y;
            const length = Math.sqrt(dx*dx + dy*dy);
            segmentLengths.push(length);
            totalLength += length;
        }
        
        if (totalLength === 0) return;
        
        // Oblicz pozycje strzałek na podstawie czasu animacji
        const animationOffset = (this.animationTime * this.arrowSpeed / 1000) % (this.arrowSpacing * 2);
        const numArrows = Math.floor(totalLength / this.arrowSpacing) + 2;
        
        for (let arrowIndex = 0; arrowIndex < numArrows; arrowIndex++) {
            const targetDistance = (arrowIndex * this.arrowSpacing + animationOffset) % totalLength;
            
            // Znajdź segment i pozycję strzałki
            let currentDistance = 0;
            let segmentIndex = 0;
            
            while (segmentIndex < segmentLengths.length && 
                   currentDistance + segmentLengths[segmentIndex] < targetDistance) {
                currentDistance += segmentLengths[segmentIndex];
                segmentIndex++;
            }
            
            if (segmentIndex >= segmentLengths.length) continue;
            
            // Interpoluj pozycję w segmencie
            const segmentProgress = (targetDistance - currentDistance) / segmentLengths[segmentIndex];
            const point1 = points[segmentIndex];
            const point2 = points[segmentIndex + 1];
            
            const arrowX = point1.x + (point2.x - point1.x) * segmentProgress;
            const arrowY = point1.y + (point2.y - point1.y) * segmentProgress;
            
            // Oblicz kierunek strzałki
            const dx = point2.x - point1.x;
            const dy = point2.y - point1.y;
            const angle = Math.atan2(dy, dx);
            
            // Pobierz prędkość z danych streamline
            const dataPoint = streamline[Math.min(segmentIndex, streamline.length - 1)];
            const speed = dataPoint.speed || 1.0;
            
            // Rysuj strzałkę
            this.drawArrow(ctx, arrowX, arrowY, angle, speed, streamlineIndex);
        }
    },

    drawArrow: function(ctx, x, y, angle, speed, streamlineIndex) {
        // Kolor na podstawie prędkości i indeksu streamline
        const hue = (streamlineIndex * 137.5) % 360;
        const lightness = Math.min(50 + speed * 10, 90); // Jaśniejsze dla większych prędkości
        
        ctx.fillStyle = `hsla(${hue}, 70%, ${lightness}%, ${this.arrowOpacity})`;
        ctx.strokeStyle = `hsla(${hue}, 70%, ${lightness - 20}%, 1.0)`;
        
        // Rozmiar strzałki proporcjonalny do prędkości
        const size = this.arrowSize * Math.sqrt(speed / 2.0);
        
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(angle);
        
        // Rysuj strzałkę
        ctx.beginPath();
        ctx.moveTo(size, 0);
        ctx.lineTo(-size/2, -size/2);
        ctx.lineTo(-size/4, 0);
        ctx.lineTo(-size/2, size/2);
        ctx.closePath();
        
        ctx.fill();
        ctx.lineWidth = 1;
        ctx.stroke();
        
        ctx.restore();
    },

    redraw: function() {
        if (this.canvas) {
            const size = this._map.getSize();
            this.canvas.width = size.x;
            this.canvas.height = size.y;
            this.draw();
        }
    },

    // Metody kontroli animacji
    toggleAnimation: function() {
        if (this.isAnimating) {
            this.stopAnimation();
        } else {
            this.startAnimation();
        }
    },

    setAnimationSpeed: function(speed) {
        this.arrowSpeed = speed;
    }
});

// ================================================================================
// ZAAWANSOWANA WARSTWA ANIMACJI WIATRU - POPRAWIONA
// ================================================================================

const AdvancedWindAnimationLayer = L.Layer.extend({
    initialize: function(data, bounds) {
        this.data = data;
        this.bounds = bounds; // POPRAWKA: Nie wywołuj L.latLngBounds() ponownie
        this.particles = [];
        this.animationFrame = null;
        this.canvas = null;
        this.ctx = null;
        this.isAnimating = false;
        
        // Parametry animacji
        this.particleCount = 2000;
        this.particleLife = 300;  // frames
        this.particleSpeed = 0.8;
        this.particleOpacity = 0.8;
    },

    onAdd: function(map) {
        this._map = map;
        this.createCanvas();
        this.initializeParticles();
        this.startAnimation();
        
        map.on('viewreset', this.redraw, this);
        map.on('zoom', this.redraw, this);
    },

    onRemove: function(map) {
        this.stopAnimation();
        if (this.canvas && this.canvas.parentNode) {
            this.canvas.parentNode.removeChild(this.canvas);
        }
        map.off('viewreset', this.redraw, this);
        map.off('zoom', this.redraw, this);
    },

    createCanvas: function() {
        const size = this._map.getSize();
        this.canvas = L.DomUtil.create('canvas', 'wind-animation-layer');
        this.canvas.width = size.x;
        this.canvas.height = size.y;
        this.canvas.style.position = 'absolute';
        this.canvas.style.top = '0';
        this.canvas.style.left = '0';
        this.canvas.style.pointerEvents = 'none';
        this.canvas.style.zIndex = 400;
        
        this._map.getPanes().overlayPane.appendChild(this.canvas);
        this.ctx = this.canvas.getContext('2d');
    },

    initializeParticles: function() {
        this.particles = [];
        const bounds = this._map.getBounds();
        
        for (let i = 0; i < this.particleCount; i++) {
            this.particles.push(this.createRandomParticle(bounds));
        }
    },

    createRandomParticle: function(bounds) {
        const lat = bounds.getSouth() + Math.random() * (bounds.getNorth() - bounds.getSouth());
        const lng = bounds.getWest() + Math.random() * (bounds.getEast() - bounds.getWest());
        
        return {
            lat: lat,
            lng: lng,
            age: Math.random() * this.particleLife,
            maxAge: this.particleLife,
            vx: 0,
            vy: 0
        };
    },

    startAnimation: function() {
        if (this.isAnimating) return;
        this.isAnimating = true;
        this.animate();
    },

    stopAnimation: function() {
        this.isAnimating = false;
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
    },

    animate: function() {
        if (!this.isAnimating) return;
        
        this.updateParticles();
        this.draw();
        
        this.animationFrame = requestAnimationFrame(() => this.animate());
    },

    updateParticles: function() {
        const mapBounds = this._map.getBounds();
        
        this.particles.forEach(particle => {
            // Znajdź najbliższy wektor wiatru
            const windVector = this.getWindVector(particle.lat, particle.lng);
            
            if (windVector) {
                particle.vx = windVector.vx * this.particleSpeed;
                particle.vy = windVector.vy * this.particleSpeed;
            }
            
            // Aktualizuj pozycję
            particle.lat += particle.vy * 0.0001;
            particle.lng += particle.vx * 0.0001;
            
            // Aktualizuj wiek
            particle.age++;
            
            // Reset cząstki jeśli za stara lub poza granicami
            if (particle.age > particle.maxAge || 
                !mapBounds.contains([particle.lat, particle.lng])) {
                const newParticle = this.createRandomParticle(mapBounds);
                Object.assign(particle, newParticle);
            }
        });
    },

    getWindVector: function(lat, lng) {
        // Znajdź najbliższy wektor z danych
        let closestVector = null;
        let minDistance = Infinity;
        
        this.data.vectors.forEach(vector => {
            const distance = Math.sqrt(
                Math.pow(vector.lat - lat, 2) + Math.pow(vector.lng - lng, 2)
            );
            
            if (distance < minDistance) {
                minDistance = distance;
                closestVector = vector;
            }
        });
        
        return closestVector;
    },

    draw: function() {
        const ctx = this.ctx;
        const canvas = this.canvas;
        
        // Wyczyść canvas z fadingiem
        ctx.globalCompositeOperation = 'destination-out';
        ctx.globalAlpha = 0.05;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        
        ctx.globalCompositeOperation = 'source-over';
        ctx.globalAlpha = this.particleOpacity;
        
        // Rysuj cząstki
        this.particles.forEach(particle => {
            const point = this._map.latLngToContainerPoint([particle.lat, particle.lng]);
            
            if (point.x >= 0 && point.x <= canvas.width && 
                point.y >= 0 && point.y <= canvas.height) {
                
                const opacity = (1 - particle.age / particle.maxAge);
                ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
                ctx.beginPath();
                ctx.arc(point.x, point.y, 1, 0, 2 * Math.PI);
                ctx.fill();
            }
        });
        
        ctx.globalAlpha = 1.0;
    },

    redraw: function() {
        if (this.canvas) {
            const size = this._map.getSize();
            this.canvas.width = size.x;
            this.canvas.height = size.y;
            this.initializeParticles();
        }
    }
});

// ================================================================================
// INICJALIZACJA MAPY WIATRU - POPRAWIONA
// ================================================================================

async function initializeWindMap() {
    console.log('🌪️ Inicjalizacja mapy wiatru...');
    
    // Sprawdź czy dane są załadowane
    if (!windSimulationData) {
        console.log('Ładowanie danych symulacji wiatru...');
        await loadWindSimulationData();
    }
    
    if (!windSimulationData) {
        console.error('Nie udało się załadować danych symulacji wiatru');
        showNotification('Błąd ładowania danych symulacji', 'error');
        return;
    }
    
    // Utwórz adapter danych
    const windData = createWindDataAdapter(windSimulationData);
    if (!windData) {
        console.error('Nie udało się utworzyć adaptera danych wiatru');
        return;
    }
    
    console.log('✅ Adapter danych utworzony:', windData);
    
    // Sprawdź czy mapa już istnieje
    if (maps.wind) {
        console.log('Usuwanie istniejącej mapy wiatru...');
        maps.wind.remove();
    }
    
    // Utwórz mapę z centrum na obszarze symulacji
    const center = windData.bounds.getCenter();
    maps.wind = L.map('wind-map', {
        center: [center.lat, center.lng],
        zoom: 15,
        zoomControl: true
    });
    
    console.log('🗺️ Mapa utworzona z centrum:', center.lat, center.lng);
    
    // Dodaj podkład mapy
    const baseLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors',
        maxZoom: 19
    }).addTo(maps.wind);
    
    // NOWE: Dodaj kontrolkę warstw
    const overlayLayers = {};
    
    // Warstwa prędkości (heatmapa)
    if (windData.magnitudeGrid && windData.magnitudeGrid.length > 0) {
        const velocityLayer = new AdvancedVelocityLayer(windData, windData.bounds);
        overlayLayers['🌡️ Pole prędkości'] = velocityLayer;
        velocityLayer.addTo(maps.wind);
        console.log('✅ Warstwa prędkości dodana');
    }
    
    // NOWE: Dynamiczna warstwa streamlines
    if (windData.streamlines && windData.streamlines.length > 0) {
        const streamlinesLayer = new DynamicStreamlinesLayer(windData, windData.bounds);
        overlayLayers['🌊 Dynamiczne streamlines'] = streamlinesLayer;
        streamlinesLayer.addTo(maps.wind);
        console.log('✅ Warstwa dynamicznych streamlines dodana');
    }
    
    // Warstwa animacji cząstek
    if (windData.particles && windData.particles.length > 0) {
        const animationLayer = new AdvancedWindAnimationLayer(windData, windData.bounds);
        overlayLayers['💫 Animacja cząstek'] = animationLayer;
        // Nie dodawaj domyślnie - może być włączana przez użytkownika
        console.log('✅ Warstwa animacji cząstek przygotowana');
    }
    
    // Dodaj kontrolkę warstw
    L.control.layers(null, overlayLayers, {
        collapsed: false,
        position: 'topright'
    }).addTo(maps.wind);
    
    // Ustaw widok na obszar symulacji
    maps.wind.fitBounds(windData.bounds, { padding: [20, 20] });
    
    // Dodaj informacje o symulacji
    addWindSimulationInfo(maps.wind, windData);
    
    console.log('🎉 Mapa wiatru została pomyślnie zainicjalizowana!');
    showNotification('Mapa wiatru załadowana pomyślnie', 'success');
}

// ================================================================================
// PANEL INFORMACYJNY O SYMULACJI
// ================================================================================

function addWindSimulationInfo(map, windData) {
    const info = L.control({ position: 'bottomleft' });
    
    info.onAdd = function() {
        const div = L.DomUtil.create('div', 'wind-info-panel');
        div.style.backgroundColor = 'rgba(0, 0, 0, 0.8)';
        div.style.color = 'white';
        div.style.padding = '10px';
        div.style.borderRadius = '5px';
        div.style.fontSize = '12px';
        div.style.lineHeight = '1.4';
        
        const stats = windData.flowStatistics;
        const metadata = windData.metadata;
        
        div.innerHTML = `
            <h4 style="margin: 0 0 8px 0; color: #4fc3f7;">📊 Statystyki przepływu</h4>
            <div><strong>Średnia prędkość:</strong> ${stats.mean_magnitude?.toFixed(2) || 'N/A'} m/s</div>
            <div><strong>Maks. prędkość:</strong> ${stats.max_magnitude?.toFixed(2) || 'N/A'} m/s</div>
            <div><strong>Intensywność turbulencji:</strong> ${(stats.turbulence_intensity * 100)?.toFixed(1) || 'N/A'}%</div>
            <div style="margin-top: 8px;"><strong>Wektory:</strong> ${windData.vectors?.length || 0}</div>
            <div><strong>Streamlines:</strong> ${windData.streamlines?.length || 0}</div>
            <div><strong>Cząstki:</strong> ${windData.particles?.length || 0}</div>
            ${metadata.computation_time ? 
                `<div style="margin-top: 8px; color: #81c784;"><strong>Czas obliczeń:</strong> ${metadata.computation_time}s</div>` : ''}
        `;
        
        return div;
    };
    
    info.addTo(map);
}

// ================================================================================
// SYSTEM NOTYFIKACJI
// ================================================================================

function showNotification(message, type = 'info') {
    // Znajdź lub utwórz kontener notyfikacji
    let container = document.getElementById('notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notification-container';
        container.style.position = 'fixed';
        container.style.top = '20px';
        container.style.right = '20px';
        container.style.zIndex = '10000';
        document.body.appendChild(container);
    }
    
    // Utwórz notyfikację
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.style.background = type === 'error' ? '#f44336' : 
                                   type === 'success' ? '#4caf50' : '#2196f3';
    notification.style.color = 'white';
    notification.style.padding = '12px 20px';
    notification.style.borderRadius = '5px';
    notification.style.marginBottom = '10px';
    notification.style.boxShadow = '0 2px 10px rgba(0,0,0,0.3)';
    notification.style.animation = 'slideInRight 0.3s ease-out';
    notification.textContent = message;
    
    container.appendChild(notification);
    
    // Automatyczne usunięcie po 5 sekundach
    setTimeout(() => {
        notification.style.animation = 'slideOutRight 0.3s ease-in';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 300);
    }, 5000);
}

// ================================================================================
// INICJALIZACJA APLIKACJI
// ================================================================================

document.addEventListener('DOMContentLoaded', function() {
    console.log('🚀 Inicjalizacja aplikacji wizualizacji wiatru...');
    
    // Inicjalizacja motywu
    applyTheme(currentTheme);
    
    // Dodaj CSS dla animacji notyfikacji
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideInRight {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOutRight {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(100%); opacity: 0; }
        }
        .wind-info-panel h4 {
            font-size: 14px !important;
            margin-bottom: 8px !important;
        }
        .leaflet-control-layers {
            background: rgba(0, 0, 0, 0.8) !important;
            color: white !important;
        }
        .leaflet-control-layers-title {
            color: white !important;
        }
        .leaflet-control-layers label {
            color: white !important;
        }
    `;
    document.head.appendChild(style);
    
    // Inicjalizuj mapę wiatru jeśli element istnieje
    if (document.getElementById('wind-map')) {
        initializeWindMap().catch(error => {
            console.error('Błąd inicjalizacji mapy wiatru:', error);
            showNotification('Błąd inicjalizacji mapy wiatru', 'error');
        });
    }
    
    console.log('✅ Aplikacja zainicjalizowana pomyślnie');
});

// ================================================================================
// FUNKCJE POMOCNICZE
// ================================================================================

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    currentTheme = theme;
}

// Eksportuj funkcje globalne jeśli potrzebne
if (typeof window !== 'undefined') {
    window.windVisualization = {
        initializeWindMap,
        loadWindSimulationData,
        showNotification
    };
}
