# -*- coding: utf-8 -*-
# Wersja 4.0: Implementacja profesjonalnego modelu Pywind
import numpy as np
import rasterio
from rasterio.enums import Resampling
import os
from wind_sim import WindSimulation # POPRAWIONY IMPORT

def align_raster(source_path, profile, resampling_method):
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

def main(config):
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru (Model Wind-Sim) ---")
    paths, params, weather = config['paths'], config['params']['wind'], config['params']['wind']

    print("   Etap 1: Przygotowanie danych wejściowych dla modelu...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale = src.res[0] / target_res
        w, h = int(src.width * scale), int(src.height * scale)
        transform = src.transform * src.transform.scale(1/scale, 1/scale)
        profile.update({'height': h, 'width': w, 'transform': transform, 'dtype': 'float32'})

    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    
    print("   Etap 2: Uruchamianie symulacji wiatru w Wind-Sim...")
    sim = WindSimulation.from_numpy(nmpt, resolution=target_res)
    
    sim.run(wind_speed=weather['wind_speed'], wind_direction=weather['wind_direction'])
    
    wind_speed, wind_direction = sim.get_wind_field(height=params['analysis_height'])

    print("   Etap 3: Zapisywanie wyników...")
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed.astype(np.float32), 1)
        
    dir_profile = profile.copy(); dir_profile.update({'dtype': 'float32'})
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **dir_profile) as dst:
        dst.write(wind_direction.astype(np.float32), 1)

    print(f"--- Skrypt 2 zakończony pomyślnie! ---")
    return paths['output_wind_speed_raster']
