# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
from scipy.ndimage import gaussian_filter
import geopandas as gpd
import os
from numba import njit, prange

@njit(parallel=True)
def lbm_solver(u, v, obstacles, relaxation_omega, num_iterations):
    nx, ny = u.shape
    weights = np.array([4/9, 1/9, 1/9, 1/9, 1/9, 1/36, 1/36, 1/36, 1/36], dtype=np.float32)
    c_i = np.array([[0,0], [0,1], [0,-1], [1,0], [-1,0], [1,1], [-1,1], [1,-1], [-1,-1]], dtype=np.int32)
    
    f = np.zeros((9, nx, ny), dtype=np.float32)
    for i in prange(9):
        f[i] = weights[i]

    for it in prange(num_iterations):
        for i in prange(9):
            f[i] = np.roll(np.roll(f[i], c_i[i,0], axis=0), c_i[i,1], axis=1)

        rho = np.sum(f, axis=0)
        ux = np.sum(f * c_i[:,0].reshape(9,1,1), axis=0) / rho
        uy = np.sum(f * c_i[:,1].reshape(9,1,1), axis=0) / rho

        ux[obstacles] = 0; uy[obstacles] = 0

        feq = np.zeros_like(f)
        for i in prange(9):
            cu = c_i[i,0] * ux + c_i[i,1] * uy
            feq[i] = weights[i] * rho * (1 + 3*cu + 4.5*cu**2 - 1.5*(ux**2+uy**2))
        
        f += relaxation_omega * (feq - f)

    return ux, uy

def main(config):
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru (LBM CFD) ---")
    paths, params, weather = config['paths'], config['params']['wind'], config['params']['wind']

    print("   Etap 1: Przygotowanie siatki i przeszkód...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale = src.res[0] / target_res
        w, h = int(src.width * scale), int(src.height * scale)
        transform = src.transform * src.transform.scale(1/scale, 1/scale)
        profile.update({'height': h, 'width': w, 'transform': transform, 'dtype': 'float32'})

    buildings_gdf = gpd.read_file(os.path.join(paths['bdot_extract'], params['bdot_building_file']))
    if not buildings_gdf.empty:
        # --- OSTATECZNA POPRAWKA: Zmiana dtype na 'uint8' ---
        obstacles_int = rasterio.features.rasterize(
            shapes=buildings_gdf.geometry,
            out_shape=(h, w),
            transform=transform,
            fill=0,
            default_value=1,
            dtype='uint8'  # Zmiana z np.bool_ na 'uint8'
        )
        obstacles = obstacles_int.astype(bool) # Konwersja do boolean dla Numba
    else:
        obstacles = np.zeros((h, w), dtype=bool)

    print("   Etap 2: Uruchamianie symulacji LBM CFD...")
    wind_dir_rad = np.deg2rad(270 - weather['wind_direction'])
    u_in = weather['wind_speed'] * np.cos(wind_dir_rad)
    v_in = weather['wind_speed'] * np.sin(wind_dir_rad)
    u = np.full((h, w), u_in, dtype=np.float32)
    v = np.full((h, w), v_in, dtype=np.float32)

    u, v = lbm_solver(u, v, obstacles, 1.0, 100)

    wind_speed = np.sqrt(u**2 + v**2)
    wind_direction_deg = (np.arctan2(v, u) * 180 / np.pi + 360) % 360

    print("   Etap 3: Zapisywanie wyników...")
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed, 1)
        
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(wind_direction_deg, 1)

    print(f"--- Skrypt 2 zakończony pomyślnie! ---")
    return paths['output_wind_speed_raster']
