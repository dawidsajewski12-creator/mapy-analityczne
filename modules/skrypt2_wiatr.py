# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
from scipy.ndimage import convolve, binary_dilation
import os

def align_raster(source_path, profile, resampling_method):
    """Dopasowuje raster do zadanego profilu."""
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

def main(config):
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru (Wersja Zoptymalizowana) ---")
    paths = config['paths']
    params = config['params']['wind']
    weather = config['params']['wind'] # Zakładamy, że dane pogodowe są już w configu
    
    print("   Etap 1: Przygotowanie danych...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale_factor = src.res[0] / target_res
        new_width = int(src.width * scale_factor)
        new_height = int(src.height * scale_factor)
        transform = src.transform * src.transform.scale(1/scale_factor, 1/scale_factor)
        profile.update({'height': new_height, 'width': new_width, 'transform': transform, 'dtype': 'float32'})
        nmt = src.read(1, out_shape=(new_height, new_width), resampling=Resampling.bilinear)
        
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    
    # Mapa szorstkości terenu (z0)
    z0 = np.full(nmt.shape, params['z0_map'][-1], dtype=np.float32)
    for lc_class, val in params['z0_map'].items():
        if lc_class != -1:
            z0[landcover == lc_class] = val

    print("   Etap 2: Obliczenia fizyczne...")
    # 1. Oblicz bazową prędkość wiatru na podstawie prawa potęgowego
    # W(z) = W_ref * (z / z_ref)^alpha. Alpha zależy od z0.
    # Uproszczenie: użyjemy logarytmicznego profilu wiatru
    # u(z) = u_star / k * ln((z - d) / z0), gdzie u_star to prędkość tarciowa
    # Dla uproszczenia, zamiast pełnego modelu, zrobimy modyfikację bazowej prędkości.
    base_wind_speed = weather['wind_speed'] * (params['analysis_height'] / 10.0)**0.2 # Proste prawo potęgowe
    
    wind_field = np.full(nmt.shape, base_wind_speed, dtype=np.float32)
    
    # 2. Modyfikacja pola wiatru przez szorstkość (redukcja prędkości)
    # Im większe z0, tym większa redukcja przy powierzchni
    wind_field *= (1 - np.log1p(z0) / np.log1p(np.max(z0)) * 0.8) # Redukcja do 80%

    # 3. Uproszczony model wpływu budynków
    building_mask = (nmpt - nmt) > params['building_threshold']
    building_height = np.maximum(0, nmpt - nmt)
    
    # Symulacja cienia aerodynamicznego
    # Długość cienia ~ 10-15 * wysokość budynku
    shadow_length_pixels = (building_height * 12 / target_res).astype(int)
    
    # Tworzymy "emiter cienia"
    shadow_emitter = np.zeros_like(wind_field)
    
    # Kierunek wiatru w radianach
    wind_dir_rad = np.deg2rad(270 - weather['wind_direction']) # Konwersja z meteorologicznej na matematyczną
    
    # Przesuwamy maskę budynków w kierunku wiatru, tworząc cień
    # To jest bardzo duże uproszczenie, ale szybkie
    from scipy.ndimage import shift
    for h in np.unique(shadow_length_pixels)[1:]: # pętla po unikalnych długościach cienia
        if h > 0:
            mask = shadow_length_pixels == h
            dx = h * np.cos(wind_dir_rad)
            dy = h * np.sin(wind_dir_rad)
            shifted_mask = shift(mask, [dy, dx], order=0, mode='constant', cval=0)
            shadow_emitter[shifted_mask] = np.maximum(shadow_emitter[shifted_mask], 0.7) # Redukcja o 70% w cieniu

    wind_field *= (1 - shadow_emitter)
    
    # Symulacja tunelowania
    # Zidentyfikuj wąskie przejścia między budynkami
    dilated_buildings = binary_dilation(building_mask, iterations=int(10/target_res)) # 10m
    gaps = dilated_buildings & ~building_mask
    
    # W miejscach przewężeń zwiększ prędkość
    wind_field[gaps] *= 1.4 

    print("   Etap 3: Zapisywanie wyniku...")
    output_path = paths['output_wind_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(wind_field, 1)

    print(f"--- Skrypt 2 zakończony pomyślnie! Wynik: {output_path} ---")
    return output_path
