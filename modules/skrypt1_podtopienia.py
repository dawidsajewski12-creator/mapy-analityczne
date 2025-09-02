# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
from numba import njit, prange
import os

def align_raster(source_path, profile, resampling_method):
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

@njit
def green_ampt_infiltration(Ks, psi, theta_diff, cumulative_infiltrated):
    if cumulative_infiltrated == 0:
        return Ks * (1 + (psi * theta_diff) / 1e-9)
    return Ks * (1 + (psi * theta_diff) / cumulative_infiltrated)

@njit(parallel=True)
def run_stable_hydraulic_simulation(manning, water_depth, rainfall_intensity_ms,
                                    total_time_s, dt_s, dx, psi, theta_diff, Ks, slope_x, slope_y):
    max_water_depth = np.copy(water_depth)
    cumulative_infiltrated = np.zeros_like(water_depth, dtype=np.float32)
    
    slope = np.sqrt(slope_x**2 + slope_y**2)
    for i in prange(slope.shape[0]):
        for j in range(slope.shape[1]):
            if slope[i, j] < 1e-6: slope[i, j] = 1e-6
    
    conveyance_factor = np.sqrt(slope) / manning
    num_steps = int(total_time_s / dt_s)

    for t_step in prange(num_steps):
        if (t_step * dt_s) < (2.0 * 3600):
            water_depth += rainfall_intensity_ms * dt_s

        for i in range(water_depth.shape[0]):
            for j in range(water_depth.shape[1]):
                 if water_depth[i, j] > 0:
                    potential_infiltration = green_ampt_infiltration(Ks[i,j], psi[i,j], theta_diff[i,j], t_step * dt_s, cumulative_infiltrated[i, j]) * dt_s
                    actual_infiltration = min(potential_infiltration, water_depth[i, j])
                    water_depth[i, j] -= actual_infiltration
                    cumulative_infiltrated[i, j] += actual_infiltration
        
        new_water_depth = np.copy(water_depth)
        q_x = conveyance_factor * water_depth**(5.0/3.0) * np.sign(slope_x)
        q_y = conveyance_factor * water_depth**(5.0/3.0) * np.sign(slope_y)

        for r in prange(1, water_depth.shape[0] - 1):
            for c in prange(1, water_depth.shape[1] - 1):
                outflow_x = max(0, q_x[r, c]) + abs(min(0, q_x[r, c+1]))
                outflow_y = max(0, q_y[r, c]) + abs(min(0, q_y[r+1, c]))
                inflow_x = abs(min(0, q_x[r, c])) + max(0, q_x[r, c-1])
                inflow_y = abs(min(0, q_y[r, c])) + max(0, q_y[r-1, c])
                new_water_depth[r, c] += (inflow_x - outflow_x + inflow_y - outflow_y) * (dt_s / dx)

        water_depth = np.maximum(0, new_water_depth)
        max_water_depth = np.maximum(max_water_depth, water_depth)
        
    return max_water_depth

def main(config):
    print("\n--- Uruchamianie Skryptu 1: Analiza Podtopień (Wersja Zoptymalizowana) ---")
    paths = config['paths']
    params = config['params']['flood']

    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale_factor = src.res[0] / target_res
        new_width = int(src.width * scale_factor)
        new_height = int(src.height * scale_factor)
        transform = src.transform * src.transform.scale(1/scale_factor, 1/scale_factor)
        profile.update({'height': new_height, 'width': new_width, 'transform': transform, 'dtype': 'float32'})
        nmt = src.read(1, out_shape=(new_height, new_width), resampling=Resampling.bilinear)

    print("-> Przygotowywanie danych wejściowych...")
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    manning = np.full(nmt.shape, params['manning_map']['default'], dtype=np.float32)
    for lc_class, man_val in params['manning_map'].items():
        if lc_class != 'default': manning[landcover == lc_class] = man_val

    Ks = np.full(nmt.shape, 1e-6, dtype=np.float32)
    psi = np.full(nmt.shape, 0.1, dtype=np.float32)
    theta_diff = np.full(nmt.shape, 0.4, dtype=np.float32)
    Ks[landcover == 3] = 5e-5; Ks[landcover == 5] = 1e-5; Ks[landcover == 6] = 2e-6
    Ks[(landcover == 1) | (landcover == 2) | (landcover == 7)] = 1e-9
    
    rainfall_intensity_ms = (params['total_rainfall_mm'] / 1000) / (params['rainfall_duration_h'] * 3600)
    water_depth_init = np.zeros_like(nmt, dtype=np.float32)

    print("-> Obliczanie nachylenia terenu...")
    slope_y, slope_x = np.gradient(nmt, target_res)

    print("-> Rozpoczynanie dynamicznej symulacji hydraulicznej...")
    # --- POPRAWKA: Konwersja wszystkich tablic na float32 ---
    max_depth = run_stable_hydraulic_simulation(
        manning.astype(np.float32), 
        water_depth_init.astype(np.float32), 
        rainfall_intensity_ms,
        params['simulation_duration_h'] * 3600, 
        params['dt_s'],
        target_res, 
        psi.astype(np.float32), 
        theta_diff.astype(np.float32), 
        Ks.astype(np.float32), 
        slope_x.astype(np.float32), 
        slope_y.astype(np.float32)
    )
    
    print("-> Zapisywanie wyniku...")
    output_path = paths['output_flood_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(max_depth, 1)

    print(f"--- Skrypt 1 zakończony pomyślnie! Wynik: {output_path} ---")
    return output_path
