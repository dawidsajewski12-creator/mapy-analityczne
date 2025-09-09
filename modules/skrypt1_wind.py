# /modules/skrypt1_wind.py
import os
import json
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_origin
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
        
        # Oblicz przepływ z obszarem buforowym
        u_field, v_field, speed_field, transform, crs, bounds = compute_flow_field_with_buffer(
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
        import traceback
        traceback.print_exc()
        return False

def compute_flow_field_with_buffer(config, wind_speed, wind_dir):
    """Oblicza pole przepływu z obszarem buforowym dla natarcia wiatru"""
    print("-> Przygotowywanie danych z jednolitą rozdzielczością...")
    
    # Używamy NMT jako bazowego rastra (lepsza rozdzielczość)
    with rasterio.open(config['paths']['nmt']) as nmt_src:
        nmt_data = nmt_src.read(1)
        base_transform = nmt_src.transform
        base_crs = nmt_src.crs
        base_height = nmt_src.height
        base_width = nmt_src.width
        resolution = base_transform.a  # rozdzielczość piksela
        
    print(f"  -> Bazowa rozdzielczość: {resolution:.2f}m, wymiary: {base_height}x{base_width}")
    
    # Wczytaj i dopasuj budynki do rozdzielczości NMT
    buildings_resampled = np.zeros((base_height, base_width), dtype=np.float32)
    
    if os.path.exists(config['paths']['output_buildings_raster']):
        with rasterio.open(config['paths']['output_buildings_raster']) as bld_src:
            buildings_orig = bld_src.read(1)
            
            # Resampling budynków do rozdzielczości NMT
            reproject(
                source=buildings_orig,
                destination=buildings_resampled,
                src_transform=bld_src.transform,
                src_crs=bld_src.crs,
                dst_transform=base_transform,
                dst_crs=base_crs,
                resampling=Resampling.bilinear
            )
            print(f"  -> Dopasowano budynki z {buildings_orig.shape} do {buildings_resampled.shape}")
    
    # OBSZAR BUFOROWY DLA NATARCIA WIATRU
    # Dodajemy 20% obszaru z każdej strony
    buffer_percent = 0.2
    buffer_y = int(base_height * buffer_percent)
    buffer_x = int(base_width * buffer_percent)
    
    # Nowe wymiary z buforem
    total_height = base_height + 2 * buffer_y
    total_width = base_width + 2 * buffer_x
    
    print(f"  -> Obszar symulacji z buforem: {total_height}x{total_width}")
    print(f"     (bufor: {buffer_y} pikseli w pionie, {buffer_x} w poziomie)")
    
    # Rozszerz rastry o obszar buforowy
    nmt_extended = np.pad(nmt_data, ((buffer_y, buffer_y), (buffer_x, buffer_x)), 
                          mode='edge')  # rozszerzenie wartościami brzegowymi
    
    buildings_extended = np.pad(buildings_resampled, ((buffer_y, buffer_y), (buffer_x, buffer_x)), 
                                mode='constant', constant_values=0)  # brak budynków w buforze
    
    # Przeskaluj dla wydajności obliczeń (co 4 piksel)
    scale = 4
    h_scaled = total_height // scale
    w_scaled = total_width // scale
    
    nmt_scaled = nmt_extended[::scale, ::scale]
    buildings_scaled = buildings_extended[::scale, ::scale]
    
    # Normalizacja terenu
    terrain_min = np.min(nmt_scaled)
    terrain_max = np.max(nmt_scaled)
    if terrain_max > terrain_min:
        # Teren wpływa na prędkość (wyżej = szybciej)
        terrain_factor = 0.7 + 0.3 * (nmt_scaled - terrain_min) / (terrain_max - terrain_min)
    else:
        terrain_factor = np.ones((h_scaled, w_scaled))
    
    # Przeszkody
    obstacles = buildings_scaled > 0
    
    # INICJALIZACJA PÓL PRĘDKOŚCI
    u = np.ones((h_scaled, w_scaled)) * wind_speed * np.cos(wind_dir)
    v = np.ones((h_scaled, w_scaled)) * wind_speed * np.sin(wind_dir)
    
    # Modulacja terenu
    u *= terrain_factor
    v *= terrain_factor
    
    # WARUNKI BRZEGOWE - natarcie wiatru
    # Określ z której strony wieje wiatr
    wind_from_west = np.cos(wind_dir) > 0
    wind_from_east = np.cos(wind_dir) < 0
    wind_from_south = np.sin(wind_dir) > 0
    wind_from_north = np.sin(wind_dir) < 0
    
    print(f"  -> Kierunek wiatru: ", end="")
    if wind_from_west: print("z zachodu", end=" ")
    if wind_from_east: print("ze wschodu", end=" ")
    if wind_from_south: print("z południa", end=" ")
    if wind_from_north: print("z północy", end=" ")
    print()
    
    # SOLVER CFD
    print("  -> Rozwiązywanie równań przepływu (50 iteracji)...")
    
    for iteration in range(50):
        u_old = u.copy()
        v_old = v.copy()
        
        # Dyfuzja numeryczna (lepkość)
        u = ndimage.gaussian_filter(u, sigma=0.8)
        v = ndimage.gaussian_filter(v, sigma=0.8)
        
        # Warunki brzegowe - stałe natarcie wiatru
        boundary_width = 5  # grubość warstwy brzegowej
        
        if wind_from_west:
            u[:, :boundary_width] = wind_speed * np.cos(wind_dir) * terrain_factor[:, :boundary_width]
        if wind_from_east:
            u[:, -boundary_width:] = wind_speed * np.cos(wind_dir) * terrain_factor[:, -boundary_width:]
        if wind_from_south:
            v[:boundary_width, :] = wind_speed * np.sin(wind_dir) * terrain_factor[:boundary_width, :]
        if wind_from_north:
            v[-boundary_width:, :] = wind_speed * np.sin(wind_dir) * terrain_factor[-boundary_width:, :]
        
        # Zeruj prędkość na przeszkodach
        u[obstacles] = 0
        v[obstacles] = 0
        
        # TURBULENCJE I WIRY
        for i in range(1, h_scaled-1):
            for j in range(1, w_scaled-1):
                if obstacles[i, j]:
                    h_obs = buildings_scaled[i, j] / 10  # normalizacja wysokości
                    
                    # Strefa recyrkulacji za budynkiem
                    if wind_from_west and j < w_scaled - 5:
                        for k in range(1, min(5, w_scaled-j)):
                            if not obstacles[i, j+k]:
                                v[i, j+k] += np.sin(k * 0.3) * h_obs * 0.5
                                u[i, j+k] *= (0.3 + 0.7 * k/5)  # odbudowa prędkości
                    
                    if wind_from_east and j > 5:
                        for k in range(1, min(5, j)):
                            if not obstacles[i, j-k]:
                                v[i, j-k] += np.sin(k * 0.3) * h_obs * 0.5
                                u[i, j-k] *= (0.3 + 0.7 * k/5)
                    
                    # Przepływ boczny
                    if i > 0 and not obstacles[i-1, j]:
                        v[i-1, j] += h_obs * 0.1
                    if i < h_scaled-1 and not obstacles[i+1, j]:
                        v[i+1, j] -= h_obs * 0.1
        
        # Zachowanie masy (równanie ciągłości)
        div = np.gradient(u, axis=1) + np.gradient(v, axis=0)
        
        # Korekcja ciśnienia
        pressure = ndimage.gaussian_filter(div, sigma=1.5)
        u -= np.gradient(pressure, axis=1) * 0.3
        v -= np.gradient(pressure, axis=0) * 0.3
        
        # Relaksacja (stabilizacja)
        alpha = 0.8
        u = alpha * u + (1 - alpha) * u_old
        v = alpha * v + (1 - alpha) * v_old
    
    # Przeskaluj do pełnej rozdzielczości
    u_full = zoom(u, scale, order=1)[:total_height, :total_width]
    v_full = zoom(v, scale, order=1)[:total_height, :total_width]
    
    # WYTNIJ OBSZAR WŁAŚCIWY (bez bufora)
    u_final = u_full[buffer_y:buffer_y+base_height, buffer_x:buffer_x+base_width]
    v_final = v_full[buffer_y:buffer_y+base_height, buffer_x:buffer_x+base_width]
    
    # Wygładź końcowe pole
    u_final = ndimage.gaussian_filter(u_final, sigma=1.0)
    v_final = ndimage.gaussian_filter(v_final, sigma=1.0)
    
    # Oblicz prędkość wypadkową
    speed_final = np.sqrt(u_final**2 + v_final**2)
    
    print(f"  -> Zakres prędkości: {np.min(speed_final):.1f} - {np.max(speed_final):.1f} m/s")
    print(f"  -> Średnia prędkość: {np.mean(speed_final[speed_final > 0]):.1f} m/s")
    
    # Bounds dla zapisu
    bounds = rasterio.transform.array_bounds(base_height, base_width, base_transform)
    
    return u_final, v_final, speed_final, base_transform, base_crs, bounds

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

def generate_arrows(u, v, speed, transform, config, density=30):
    """Generuje punkty strzałek dla wizualizacji na mapie"""
    h, w = u.shape
    arrows = []
    
    # Siatka punktów adaptacyjna
    for i in range(density//2, h-density//2, density):
        for j in range(density//2, w-density//2, density):
            # Średnia lokalna dla stabilności
            local_u = np.mean(u[max(0,i-2):min(h,i+3), max(0,j-2):min(w,j+3)])
            local_v = np.mean(v[max(0,i-2):min(h,i+3), max(0,j-2):min(w,j+3)])
            local_speed = np.sqrt(local_u**2 + local_v**2)
            
            if local_speed > 0.5:  # Tylko znaczące przepływy
                lon, lat = transform * (j, i)
                
                arrows.append({
                    'x': float(lon),
                    'y': float(lat),
                    'u': float(local_u),
                    'v': float(local_v),
                    'speed': float(local_speed),
                    'direction': float(np.degrees(np.arctan2(local_v, local_u)))
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
        vmax = np.percentile(valid_speeds, 95)
    else:
        vmin, vmax = 0, 10
    
    # Normalizacja
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    
    # Colormap - gradient profesjonalny
    colors = ['#2166ac', '#4393c3', '#92c5de', '#d1e5f0', 
              '#fddbc7', '#f4a582', '#d6604d', '#b2182b']
    n_bins = 256
    cmap = mcolors.LinearSegmentedColormap.from_list('wind_speed', colors, N=n_bins)
    
    # Aplikuj colormap
    mapper = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    rgba = mapper.to_rgba(speed, bytes=True)
    
    # Przezroczystość adaptacyjna
    alpha = np.zeros_like(speed)
    alpha[speed > 0.1] = 50 + 205 * np.clip(speed[speed > 0.1] / vmax, 0, 1)
    rgba[:, :, 3] = alpha.astype('uint8')
    
    # Przeorganizuj wymiary
    rgba_reshaped = np.moveaxis(rgba, -1, 0)
    
    # Zapisz RGBA
    profile.update(count=4, dtype='uint8', nodata=None)
    
    with rasterio.open(output_rgba, 'w', **profile) as dst:
        dst.write(rgba_reshaped)
    
    print(f"  -> Utworzono wizualizację (vmax={vmax:.1f} m/s)")
    
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
        print("  -> Nie udało się wygenerować kafelków")
    
    return vmin, vmax
