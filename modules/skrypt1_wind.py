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


def _crop_to_min_shape(arrays):
    """Przytnij listę tablic do najmniejszego wspólnego kształtu (po przekątnej).
    Zwraca nowe (przycięte) tablice w takiej samej kolejności.
    """
    shapes = [a.shape for a in arrays]
    min_h = min(s[0] for s in shapes)
    min_w = min(s[1] for s in shapes)
    cropped = [a[:min_h, :min_w] for a in arrays]
    return cropped


def compute_flow_field_with_buffer(config, wind_speed, wind_dir):
    """Oblicza pole przepływu z obszarem buforowym dla natarcia wiatru

    Zabezpieczenia:
    - Przycinanie skalowanych rastrów do najmniejszego wspólnego kształtu, aby
      uniknąć błędów broadcastingu (ValueError: operands could not be broadcast).
    - Jeżeli końcowe powiększone pole jest mniejsze niż obszar bazowy -> przycinamy
      wynik do dostępnego rozmiaru i zgłaszamy ostrzeżenie.
    """
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
    
    # Przeskaluj dla wydajności obliczeń (co scale pikseli)
    scale = 4

    # Skalowanie przez wybieranie co 'scale'-tego piksela (szybsze), ale może dawać
    # różne wymiary (ceil vs floor). Budujemy zabezpieczenie — przycinamy do najmniejszego kształtu.
    nmt_scaled = nmt_extended[::scale, ::scale]
    buildings_scaled = buildings_extended[::scale, ::scale]

    # Przytnij do najmniejszego wspólnego kształtu, żeby uniknąć broadcasting error
    nmt_scaled, buildings_scaled = _crop_to_min_shape([nmt_scaled, buildings_scaled])

    h_scaled, w_scaled = nmt_scaled.shape
    print(f"  -> Scaled shapes: {h_scaled}x{w_scaled} (scale={scale})")

    # Normalizacja terenu
    terrain_min = np.min(nmt_scaled)
    terrain_max = np.max(nmt_scaled)
    if terrain_max > terrain_min:
        terrain_factor = 0.7 + 0.3 * (nmt_scaled - terrain_min) / (terrain_max - terrain_min)
    else:
        terrain_factor = np.ones((h_scaled, w_scaled))

    # Przeszkody
    obstacles = buildings_scaled > 0

    # INICJALIZACJA PÓL PRĘDKOŚCI
    u = np.ones((h_scaled, w_scaled)) * wind_speed * np.cos(wind_dir)
    v = np.ones((h_scaled, w_scaled)) * wind_speed * np.sin(wind_dir)

    # Upewnij się, że terrain_factor pasuje do u/v (dodatkowe zabezpieczenie)
    if terrain_factor.shape != u.shape:
        print("  -> Uwaga: dopasowuję kształt terrain_factor do pól prędkości (przycinanie).")
        terrain_factor = terrain_factor[:u.shape[0], :u.shape[1]]

    # Modulacja terenu
    u *= terrain_factor
    v *= terrain_factor
    
    # WARUNKI BRZEGOWE - natarcie wiatru
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
    u_full = zoom(u, scale, order=1)
    v_full = zoom(v, scale, order=1)

    # Bezpieczne przycinanie/obsługa, jeśli powiększone pole jest mniejsze niż oczekiwane
    available_h, available_w = u_full.shape
    if available_h < total_height or available_w < total_width:
        print("  -> Uwaga: powiększone pole ma mniejsze wymiary niż oczekiwano po skalowaniu.")
        print(f"     oczekiwano: {total_height}x{total_width}, dostępne: {available_h}x{available_w}")

    # Wyznacz rozmiary, które da się wyciąć (przycinamy do dostępnego)
    usable_h = min(available_h, total_height)
    usable_w = min(available_w, total_width)

    # Przytnij u_full/v_full do 'usable' (zachowując lewy-górny układ zgodny z paddingiem)
    u_full = u_full[:usable_h, :usable_w]
    v_full = v_full[:usable_h, :usable_w]

    # WYTNIJ OBSZAR WŁAŚCIWY (bez bufora) - przycinamy, jeżeli trzeba
    start_y = buffer_y
    start_x = buffer_x
    end_y = start_y + base_height
    end_x = start_x + base_width

    # Jeśli nie możemy w pełni wyciąć oryginalnego obszaru, przycinamy do dostępnego
    if end_y > u_full.shape[0] or end_x > u_full.shape[1]:
        max_h_possible = max(0, u_full.shape[0] - start_y)
        max_w_possible = max(0, u_full.shape[1] - start_x)
        print(f"  -> Przycinam wynik do mniejszego obszaru: {max_h_possible}x{max_w_possible} (z powodu ograniczeń skalowania)")
        end_y = start_y + max_h_possible
        end_x = start_x + max_w_possible

    u_final = u_full[start_y:end_y, start_x:end_x]
    v_final = v_full[start_y:end_y, start_x:end_x]

    # Jeżeli wynik jest pusty (np. błąd skalowania), zwróć zero-macierz i zgłoś błąd
    if u_final.size == 0 or v_final.size == 0:
        raise RuntimeError("Końcowe pola prędkości są puste po operacjach skalowania/przycinania.")

    # Wygładź końcowe pole
    u_final = ndimage.gaussian_filter(u_final, sigma=1.0)
    v_final = ndimage.gaussian_filter(v_final, sigma=1.0)
    
    # Oblicz prędkość wypadkową
    speed_final = np.sqrt(u_final**2 + v_final**2)
    
    print(f"  -> Zakres prędkości: {np.min(speed_final):.1f} - {np.max(speed_final):.1f} m/s")
    nonzero_mask = speed_final > 0
    if np.any(nonzero_mask):
        print(f"  -> Średnia prędkość: {np.mean(speed_final[nonzero_mask]):.1f} m/s")
    else:
        print("  -> Średnia prędkość: 0.0 m/s (brak niezerowych pikseli)")

    # Bounds dla zapisu -- dostosowane do wymiarów zwróconych pól
    bounds = rasterio.transform.array_bounds(u_final.shape[0], u_final.shape[1], base_transform)
    
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
    except Exception:
        print("  -> Nie udało się wygenerować kafelków")
    
    return vmin, vmax
