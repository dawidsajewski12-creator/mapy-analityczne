# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from scipy.ndimage import gaussian_filter
import os

def align_raster(source_path, profile, resampling_method):
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(resampling_method, resampling_method))
    return array

def main(config):
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru (Nowe Podejście CFD-Like) ---")
    paths = config['paths']
    params = config['params']['wind']
    weather = config['params']['wind']

    print("   Etap 1: Przygotowanie danych...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale_factor = src.res[0] / target_res
        new_width = int(src.width * scale_factor)
        new_height = int(src.height * scale_factor)
        transform = src.transform * src.transform.scale(1/scale_factor, 1/scale_factor)
        profile.update({'height': new_height, 'width': new_width, 'transform': transform, 'dtype': 'float32'})

    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    nmt = align_raster(paths['nmt'], profile, 'bilinear')
    
    building_height = np.maximum(0, nmpt - nmt)
    buildings = (building_height > params['building_threshold']).astype(np.float32)

    print("   Etap 2: Obliczanie pola prędkości i kierunku wiatru...")
    # Ustawienia początkowe
    wind_dir_rad = np.deg2rad(weather['wind_direction'] - 180) # Kierunek Z którego wieje
    base_u = weather['wind_speed'] * np.cos(wind_dir_rad) # Składowa U (zachód-wschód)
    base_v = weather['wind_speed'] * np.sin(wind_dir_rad) # Składowa V (południe-północ)

    u_field = np.full(nmt.shape, base_u, dtype=np.float32)
    v_field = np.full(nmt.shape, base_v, dtype=np.float32)

    # Wpływ budynków - iteracyjna propagacja zaburzeń
    for _ in range(15): # Im więcej iteracji, tym "dalszy" wpływ budynków
        u_field_old, v_field_old = u_field.copy(), v_field.copy()
        
        # Rozprzestrzenianie się pola wiatru (uproszczona adwekcja i dyfuzja)
        u_field[1:,:] = 0.5 * (u_field_old[:-1,:] + u_field_old[1:,:])
        v_field[1:,:] = 0.5 * (v_field_old[:-1,:] + v_field_old[1:,:])
        
        # Wygładzanie (lepkość)
        u_field = gaussian_filter(u_field, sigma=1.5)
        v_field = gaussian_filter(v_field, sigma=1.5)

        # Warunek brzegowy: zerowa prędkość na ścianach budynków
        u_field[buildings > 0] = 0
        v_field[buildings > 0] = 0

    wind_speed = np.sqrt(u_field**2 + v_field**2)
    # Zapisz kierunek w stopniach dla wizualizacji
    wind_direction_deg = (np.arctan2(v_field, u_field) * 180 / np.pi) % 360

    print("   Etap 3: Zapisywanie wyników...")
    # Zapisz raster prędkości
    with rasterio.open(paths['output_wind_raster'], 'w', **profile) as dst:
        dst.write(wind_speed, 1)
        
    # Zapisz raster kierunku
    dir_profile = profile.copy()
    dir_profile.update({'dtype': 'float32'})
    # NOWOŚĆ: Zapisujemy drugi raster z kierunkiem
    wind_dir_path = os.path.join(os.path.dirname(paths['output_wind_raster']), 'kierunek_wiatru.tif')
    with rasterio.open(wind_dir_path, 'w', **dir_profile) as dst:
        dst.write(wind_direction_deg, 1)

    print(f"--- Skrypt 2 zakończony pomyślnie! Wynik: {paths['output_wind_raster']} ---")
    return paths['output_wind_raster']
