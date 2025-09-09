# /modules/skrypt1_wind.py
import os
import json
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
import scipy.ndimage as ndimage
from scipy.ndimage import zoom
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

def main(config):
    """Główna funkcja obliczająca przepływ wiatru"""
    print("\n--- Uruchamianie Skryptu 1: Symulacja Przepływu Wiatru ---")
    
    try:
        # Wczytaj dane pogodowe
        weather_path = os.path.join(config['paths']['local_repo'], 'wyniki/weather.json')
        if os.path.exists(weather_path):
            with open(weather_path, 'r') as f:
                weather = json.load(f)
            wind_speed = weather['wind_speed']
            wind_dir = np.radians(weather['wind_direction'])
            print(f"-> Parametry wiatru: {wind_speed:.1f} m/s, kierunek {weather['wind_direction']}°")
        else:
            wind_speed = 5.0
            wind_dir = np.radians(270)
            print("-> Używam domyślnych parametrów wiatru: 5.0 m/s, 270°")
        
        # Oblicz przepływ
        u_field, v_field, speed_field, transform, crs = compute_flow_field(
            config, wind_speed, wind_dir
        )
        
        # Zapisz wyniki
        save_wind_raster(u_field, v_field, speed_field, transform, crs, config)
        
        # Generuj strzałki
        generate_arrows(u_field, v_field, speed_field, transform, config)
        
        # Koloruj i kafelkuj
        create_wind_visualization(config)
        
        print("--- Skrypt 1 (Wind Flow) zakończony pomyślnie ---")
        return True
        
    except Exception as e:
        print(f"BŁĄD w symulacji wiatru: {e}")
        return False

def compute_flow_field(config, wind_speed, wind_dir):
    """Oblicza pole przepływu metodą uproszczonej CFD"""
    print("-> Obliczanie pola przepływu...")
    
    # Wczytaj przeszkody
    buildings_path = config['paths']['output_buildings_raster']
    nmt_path = config['paths']['nmt']
    
    if os.path.exists(buildings_path):
        with rasterio.open(buildings_path) as src:
            buildings = src.read(1)
            transform = src.transform
            crs = src.crs
    else:
        # Fallback - użyj NMT
        with rasterio.open(nmt_path) as src:
            buildings = np.zeros(src.shape)
            transform = src.transform
            crs = src.crs
    
    # Przeskaluj dla wydajności
    scale = 4
    h, w = buildings.shape
    ny, nx = h // scale, w // scale
    
    buildings_scaled = buildings[::scale, ::scale]
    
    # Normalizuj wysokości terenu jeśli dostępne
    if os.path.exists(nmt_path):
        with rasterio.open(nmt_path) as src:
            terrain = src.read(1)
            terrain_scaled = terrain[::scale, ::scale]
            # Uwzględnij teren jako modyfikator przepływu
            terrain_factor = 1.0 - (terrain_scaled - np.min(terrain_scaled)) / (np.max(terrain_scaled) - np.min(terrain_scaled)) * 0.3
    else:
        terrain_factor = np.ones((ny, nx))
    
    # Przeszkody (budynki + strome zbocza)
    obstacles = buildings_scaled > 0
    
    # Inicjalizacja pól prędkości
    u = np.ones((ny, nx)) * wind_speed * np.cos(wind_dir) * terrain_factor
    v = np.ones((ny, nx)) * wind_speed * np.sin(wind_dir) * terrain_factor
    
    print("  -> Rozwiązywanie równań przepływu...")
    
    # Iteracyjny solver (simplified Navier-Stokes)
    for iteration in range(50):
        # Krok 1: Dyfuzja (lepkość)
        u_old = u.copy()
        v_old = v.copy()
        
        u = ndimage.gaussian_filter(u, sigma=0.8)
        v = ndimage.gaussian_filter(v, sigma=0.8)
        
        # Krok 2: Warunki brzegowe na przeszkodach
        u[obstacles] = 0
        v[obstacles] = 0
        
        # Krok 3: Turbulencje za budynkami (von Karman vortices)
        for i in range(1, ny-1):
            for j in range(1, nx-1):
                if obstacles[i, j]:
                    # Wysokość przeszkody
                    h_obs = buildings_scaled[i, j]
                    
                    # Strefa recyrkulacji za budynkiem
                    wake_length = min(int(h_obs / 10), nx - j - 1)
                    if wake_length > 0 and j < nx - wake_length:
                        # Dodaj wiry
                        for k in range(1, wake_length):
                            if not obstacles[i, j+k]:
                                # Oscylacje poprzeczne
                                v[i, j+k] += np.sin(k * 0.5) * h_obs * 0.01
                                # Redukcja prędkości w cieniu
                                u[i, j+k] *= 0.9
                    
                    # Przepływ boczny wokół budynku
                    if i > 0 and not obstacles[i-1, j]:
                        v[i-1, j] += h_obs * 0.02
                    if i < ny-1 and not obstacles[i+1, j]:
                        v[i+1, j] -= h_obs * 0.02
        
        # Krok 4: Wymuszenie nieściśliwości (continuity equation)
        div = np.gradient(u, axis=1) + np.gradient(v, axis=0)
        
        # Korekcja ciśnienia (simplified pressure projection)
        pressure = ndimage.gaussian_filter(div, sigma=1.0)
        
        u -= np.gradient(pressure, axis=1) * 0.5
        v -= np.gradient(pressure, axis=0) * 0.5
        
        # Krok 5: Relaksacja
        alpha = 0.7
        u = alpha * u + (1 - alpha) * u_old
        v = alpha * v + (1 - alpha) * v_old
    
    # Przeskaluj do oryginalnej rozdzielczości
    u_full = zoom(u, scale, order=1)[:h, :w]
    v_full = zoom(v, scale, order=1)[:h, :w]
    
    # Oblicz prędkość wypadkową
    speed_full = np.sqrt(u_full**2 + v_full**2)
    
    # Wygładzenie końcowe
    u_full = ndimage.gaussian_filter(u_full, sigma=1.0)
    v_full = ndimage.gaussian_filter(v_full, sigma=1.0)
    speed_full = ndimage.gaussian_filter(speed_full, sigma=1.0)
    
    print(f"  -> Zakres prędkości: {np.min(speed_full):.1f} - {np.max(speed_full):.1f} m/s")
    
    return u_full, v_full, speed_full, transform, crs

def save_wind_raster(u, v, speed, transform, crs, config):
    """Zapisuje pole wiatru jako raster wielokanałowy"""
    output_path = os.path.join(config['paths']['local_repo'], 'wyniki/rastry/wind_flow.tif')
    
    h, w = u.shape
    profile = {
        'driver': 'GTiff',
        'height': h,
        'width': w,
        'count': 3,
        'dtype': 'float32',
        'transform': transform,
        'crs': crs,
        'compress': 'lzw'
    }
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(u.astype('float32'), 1)
        dst.write(v.astype('float32'), 2)
        dst.write(speed.astype('float32'), 3)
    
    print(f"  -> Zapisano pole wiatru: {output_path}")

def generate_arrows(u, v, speed, transform, config, density=25):
    """Generuje punkty strzałek dla wizualizacji na mapie"""
    h, w = u.shape
    arrows = []
    
    # Siatka punktów
    for i in range(density//2, h, density):
        for j in range(density//2, w, density):
            if speed[i, j] > 0.5:  # Tylko znaczące przepływy
                # Konwersja do współrzędnych geograficznych
                lon, lat = transform * (j, i)
                
                arrows.append({
                    'x': float(lon),
                    'y': float(lat),
                    'u': float(u[i, j]),
                    'v': float(v[i, j]),
                    'speed': float(speed[i, j]),
                    'direction': float(np.degrees(np.arctan2(v[i, j], u[i, j])))
                })
    
    # Zapisz jako JSON
    output_path = os.path.join(config['paths']['local_repo'], 'wyniki/wind_arrows.json')
    with open(output_path, 'w') as f:
        json.dump(arrows, f, indent=2)
    
    print(f"  -> Wygenerowano {len(arrows)} strzałek wiatru")

def create_wind_visualization(config):
    """Tworzy kolorową wizualizację prędkości wiatru"""
    input_path = os.path.join(config['paths']['local_repo'], 'wyniki/rastry/wind_flow.tif')
    output_rgba = os.path.join(config['paths']['local_repo'], 'wyniki/rastry_kolorowe/wind_rgba.tif')
    output_tiles = os.path.join(config['paths']['local_repo'], 'wyniki/kafelki/wind')
    
    os.makedirs(os.path.dirname(output_rgba), exist_ok=True)
    
    # Wczytaj prędkość
    with rasterio.open(input_path) as src:
        speed = src.read(3)
        profile = src.profile.copy()
    
    # Określ zakres kolorowania
    valid_speeds = speed[speed > 0]
    if len(valid_speeds) > 0:
        vmin = 0
        vmax = np.percentile(valid_speeds, 98)
    else:
        vmin, vmax = 0, 10
    
    # Normalizacja
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    
    # Colormap - gradient od niebieskiego przez żółty do czerwonego
    colors = ['#1e3c72', '#2e7cd6', '#5ca0d9', '#90c6e4', '#ffd23f', '#ff8c42', '#ff3c38', '#a0262c']
    n_bins = len(colors)
    cmap = mcolors.LinearSegmentedColormap.from_list('wind', colors, N=n_bins)
    
    # Aplikuj colormap
    mapper = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    rgba = mapper.to_rgba(speed, bytes=True)
    
    # Przezroczystość gdzie brak wiatru
    mask = speed < 0.1
    rgba[mask, 3] = 0
    
    # Gradient przezroczystości dla słabych wiatrów
    weak_wind = (speed >= 0.1) & (speed < 2.0)
    rgba[weak_wind, 3] = (rgba[weak_wind, 3] * (speed[weak_wind] / 2.0)).astype('uint8')
    
    # Przeorganizuj wymiary
    rgba_reshaped = np.moveaxis(rgba, -1, 0)
    
    # Zapisz RGBA
    profile.update(count=4, dtype='uint8', nodata=None)
    
    with rasterio.open(output_rgba, 'w', **profile) as dst:
        dst.write(rgba_reshaped)
    
    print(f"  -> Utworzono wizualizację prędkości wiatru")
    
    # Generuj kafelki
    if os.path.exists(output_tiles):
        import shutil
        shutil.rmtree(output_tiles)
    
    os.makedirs(output_tiles)
    
    try:
        import subprocess
        subprocess.run([
            'gdal2tiles.py', '--profile', 'mercator', 
            '-z', '11-16', '-w', 'none', 
            '--s_srs', profile['crs'].to_string(),
            output_rgba, output_tiles
        ], check=True, capture_output=True, text=True)
        print("  -> Wygenerowano kafelki wiatru")
    except:
        print("  -> Nie udało się wygenerować kafelków (brak gdal2tiles)")
    
    return vmin, vmax
