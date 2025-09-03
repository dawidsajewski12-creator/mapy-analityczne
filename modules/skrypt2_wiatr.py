# -*- coding: utf-8 -*-
# Wersja 5.0: Usunięto zależność od Pywind, zaimplementowano prosty model wiatru oparty na chropowatości terenu.

import numpy as np
import rasterio
from rasterio.enums import Resampling
import os

def align_raster(source_path, profile, resampling_method):
    """Dopasowuje raster do zadanego profilu."""
    with rasterio.open(source_path) as src:
        array = src.read(
            1,
            out_shape=(profile['height'], profile['width']),
            resampling=getattr(Resampling, resampling_method)
        )
    return array

def main(config):
    """
    Główna funkcja skryptu do analizy wiatru.
    """
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru (Prosty model) ---")
    paths = config['paths']
    params = config['params']['wind']
    weather = config['params']['wind']  # Pobieranie danych pogodowych z konfiguracji

    print("   Etap 1: Przygotowanie danych wejściowych...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale = src.res[0] / target_res
        w, h = int(src.width * scale), int(src.height * scale)
        transform = src.transform * src.transform.scale(1/scale, 1/scale)
        profile.update({
            'height': h,
            'width': w,
            'transform': transform,
            'dtype': 'float32'
        })

    # Wczytanie i dopasowanie rastrów
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')

    print("   Etap 2: Obliczanie prędkości wiatru w oparciu o chropowatość terenu...")

    # Mapa chropowatości terenu (współczynnik Hellmanna)
    # Źródło wartości: literatura przedmiotu, np. "Wind Power Meteorology"
    roughness_map = {
        1: 0.15,  # Nawierzchnie utwardzone
        2: 0.35,  # Budynki (obszary zurbanizowane)
        3: 0.35,  # Drzewa / Lasy
        5: 0.15,  # Trawa / Tereny zielone
        6: 0.10,  # Gleba / Nieużytki
        7: 0.10,  # Woda
        'default': 0.20
    }

    # Tworzenie rastra współczynnika chropowatości
    alpha = np.vectorize(roughness_map.get)(landcover, roughness_map['default'])

    # Wysokość referencyjna, na której mierzona jest prędkość wiatru (zwykle 10m)
    ref_height = 10.0
    # Wysokość analizy (z parametru w CONFIG)
    analysis_height = params.get('analysis_height', 10.0) # Domyślnie 10m, jeśli nie ma w configu

    # Obliczenie wysokości analizy nad ziemią
    height_above_ground = analysis_height + (nmpt - align_raster(paths['nmt'], profile, 'bilinear'))
    height_above_ground[height_above_ground < 1.0] = 1.0 # Uniknięcie wartości bliskich zeru

    # Prawo potęgowe profilu wiatru
    wind_speed = weather['wind_speed'] * (height_above_ground / ref_height)**alpha

    # Kierunek wiatru jest stały, pobierany z danych pogodowych
    wind_direction = np.full(wind_speed.shape, weather['wind_direction'], dtype=np.float32)

    print("   Etap 3: Zapisywanie wyników...")
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed.astype(np.float32), 1)

    dir_profile = profile.copy()
    dir_profile.update({'dtype': 'float32'})
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **dir_profile) as dst:
        dst.write(wind_direction.astype(np.float32), 1)

    print("--- Skrypt 2 zakończony pomyślnie! ---")
    return paths['output_wind_speed_raster']
