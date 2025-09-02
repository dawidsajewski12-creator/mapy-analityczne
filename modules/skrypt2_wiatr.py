# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
# NOWY IMPORT
from scipy.ndimage import gaussian_filter, shift
import os

def align_raster(source_path, profile, resampling_method):
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

def main(config):
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru (Wersja Ulepszona) ---")
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
    
    base_wind_speed = weather['wind_speed']
    wind_field = np.full(nmt.shape, base_wind_speed, dtype=np.float32)
    
    print("   Etap 2: Obliczenia fizyczne z wygładzaniem...")
    building_height = np.maximum(0, nmpt - nmt)
    building_mask = (building_height > params['building_threshold']).astype(np.float32)
    
    # NOWOŚĆ: Wygładzamy maskę budynków, aby uzyskać płynne przejścia
    smoothed_buildings = gaussian_filter(building_mask, sigma=3)
    
    # Redukcja prędkości wiatru proporcjonalna do "gęstości" zabudowy
    wind_field *= (1 - smoothed_buildings * 0.9) # Redukcja do 90% wewnątrz budynków

    # NOWOŚĆ: Ulepszony cień aerodynamiczny
    shadow_length_pixels = (building_height * 10 / target_res) # Długość cienia = 10x wysokość
    wind_dir_rad = np.deg2rad(270 - weather['wind_direction'])
    
    # Przesuwamy wygładzoną maskę budynków, aby stworzyć miękki cień
    dy, dx = shadow_length_pixels * np.sin(wind_dir_rad), shadow_length_pixels * np.cos(wind_dir_rad)
    
    # Zamiast skomplikowanego przesuwania, zastosujemy prostsze, ale efektywne globalne rozmycie cienia
    # To da bardziej naturalny efekt niż twarde krawędzie cienia.
    shadow_mask = np.zeros_like(wind_field)
    
    # Prosta pętla symulująca "przesuwanie" cienia
    temp_mask = building_mask.copy()
    for _ in range(int(np.mean(shadow_length_pixels[shadow_length_pixels > 0]))):
        temp_mask = shift(temp_mask, [np.sin(wind_dir_rad), np.cos(wind_dir_rad)], order=0)
        shadow_mask += temp_mask

    shadow_mask = gaussian_filter(shadow_mask.astype(float), sigma=10)
    shadow_mask /= np.max(shadow_mask) # Normalizacja
    
    wind_field *= (1 - shadow_mask * 0.5) # Redukcja w cieniu o max 50%
    wind_field = np.maximum(wind_field, 0) # Prędkość nie może być ujemna

    print("   Etap 3: Zapisywanie wyniku...")
    output_path = paths['output_wind_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(wind_field, 1)

    print(f"--- Skrypt 2 zakończony pomyślnie! Wynik: {output_path} ---")
    return output_path
