# -*- coding: utf-8 -*-
# Wersja 3.0: Stabilny model potencjału przepływu (zamiennik dla LBM)
import numpy as np
import rasterio
from rasterio.enums import Resampling
import geopandas as gpd
import os
from scipy.ndimage import convolve

def align_raster(source_path, profile, resampling_method):
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

def main(config):
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru (Nowy Model Przepływowy) ---")
    paths, params, weather = config['paths'], config['params']['wind'], config['params']['wind']

    print("   Etap 1: Przygotowanie siatki i przeszkód 3D...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale = src.res[0] / target_res
        w, h = int(src.width * scale), int(src.height * scale)
        transform = src.transform * src.transform.scale(1/scale, 1/scale)
        profile.update({'height': h, 'width': w, 'transform': transform, 'dtype': 'float32'})

    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    nmt = align_raster(paths['nmt'], profile, 'bilinear')
    
    building_height = np.maximum(0, nmpt - nmt)
    obstacles = (building_height > params['building_threshold']).astype(np.float32)

    print("   Etap 2: Uruchamianie iteracyjnej symulacji przepływu...")
    wind_dir_rad = np.deg2rad(270 - weather['wind_direction'])
    u = np.full((h, w), weather['wind_speed'] * np.cos(wind_dir_rad), dtype=np.float32)
    v = np.full((h, w), weather['wind_speed'] * np.sin(wind_dir_rad), dtype=np.float32)
    
    pressure = np.zeros_like(u)
    divergence = np.zeros_like(u)

    # Kernel do obliczania dywergencji i gradientu
    kernel_x = np.array([[0,0,0],[-1,0,1],[0,0,0]]) / (2 * target_res)
    kernel_y = np.array([[0,-1,0],[0,0,0],[0,1,0]]) / (2 * target_res)

    num_iterations = 50 # Ilość iteracji dla stabilizacji pola wiatru
    for it in range(num_iterations):
        # Zeruj prędkość wewnątrz budynków
        u[obstacles == 1] = 0
        v[obstacles == 1] = 0
        
        # Oblicz dywergencję (źródła i ujścia przepływu)
        div_u = convolve(u, kernel_x)
        div_v = convolve(v, kernel_y)
        divergence = div_u + div_v
        
        # Oblicz pole ciśnienia na podstawie dywergencji
        # To jest relaksacja Jacobiego dla równania Poissona - standardowa metoda
        pressure[1:-1, 1:-1] = 0.25 * (pressure[1:-1, :-2] + pressure[1:-1, 2:] + 
                                       pressure[:-2, 1:-1] + pressure[2:, 1:-1] - 
                                       divergence[1:-1, 1:-1] * target_res**2)

        # Skoryguj pole prędkości na podstawie gradientu ciśnienia
        grad_px = convolve(pressure, kernel_x)
        grad_py = convolve(pressure, kernel_y)
        
        u -= grad_px
        v -= grad_py

    wind_speed = np.sqrt(u**2 + v**2)
    # Zabezpieczenie przed dzieleniem przez zero
    wind_speed[wind_speed < 1e-6] = 1e-6
    # Normalizacja dla lepszego wyglądu
    wind_speed = (wind_speed / wind_speed.max()) * weather['wind_speed'] * 1.5

    wind_direction_deg = (np.arctan2(v, u) * 180 / np.pi + 360) % 360

    print("   Etap 3: Zapisywanie wyników...")
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed, 1)
        
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(wind_direction_deg, 1)

    print(f"--- Skrypt 2 zakończony pomyślnie! ---")
    return paths['output_wind_speed_raster']
