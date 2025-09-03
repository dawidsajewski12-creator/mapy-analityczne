# -*- coding: utf-8 -*-
# modules/skrypt1_podtopienia.py - Wersja 4.1: Zoptymalizowane zużycie pamięci RAM
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
    if cumulative_infiltrated <= 0: return Ks
    return Ks * (1 + (psi * theta_diff) / cumulative_infiltrated)

@njit(parallel=True)
@njit(parallel=True)
def hydraulic_simulation_fixed(manning, water_depth, rainfall_intensity_ms,
                              total_time_s, dt_s, dx, psi, theta_diff, Ks, nmt):
    max_water_depth = np.copy(water_depth)
    cumulative_infiltrated = np.zeros_like(water_depth, dtype=np.float32)
    water_surface = nmt + water_depth

    num_steps = int(total_time_s / dt_s)

    for t_step in prange(num_steps):
        if (t_step * dt_s) < (2.0 * 3600):
            water_depth += rainfall_intensity_ms * dt_s

        for i in range(water_depth.shape[0]):
            for j in range(water_depth.shape[1]):
                if water_depth[i, j] > 0:
                    potential_inf = green_ampt_infiltration(Ks[i, j], psi[i, j], theta_diff[i, j], cumulative_infiltrated[i, j]) * dt_s
                    actual_inf = min(potential_inf, water_depth[i, j])
                    water_depth[i, j] -= actual_inf
                    cumulative_infiltrated[i, j] += actual_inf

        water_surface = nmt + water_depth
        new_water_depth = np.copy(water_depth)

        for r in prange(1, water_depth.shape[0] - 1):
            for c in prange(1, water_depth.shape[1] - 1):
                if water_depth[r, c] > 0.001:
                    neighbors = [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
                    
                    total_flow_out = 0.0
                    for nr, nc in neighbors:
                        if 0 <= nr < water_depth.shape[0] and 0 <= nc < water_depth.shape[1]:
                            dh = water_surface[r, c] - water_surface[nr, nc]
                            if dh > 0:
                                avg_depth = (water_depth[r, c] + water_depth[nr, nc]) / 2.0
                                if avg_depth > 0.001:
                                    avg_manning = (manning[r, c] + manning[nr, nc]) / 2.0
                                    # Bardziej stabilne obliczenie prędkości
                                    velocity = (avg_depth**(2.0 / 3.0) * np.sqrt(dh / dx)) / avg_manning
                                    
                                    # Objętość przepływu w kroku czasowym
                                    flow_volume = velocity * avg_depth * dt_s
                                    
                                    # **GŁÓWNA ZMIANA: Bardziej restrykcyjne ograniczenie przepływu**
                                    # Ograniczamy przepływ do małej części wody dostępnej w komórce,
                                    # aby zapobiec nagłym opróżnieniom i niestabilności.
                                    max_transferable_volume = water_depth[r, c] * dx * 0.1 # Max 10% objętości
                                    
                                    flow_volume = min(flow_volume, max_transferable_volume)
                                    
                                    if flow_volume > 0:
                                       # Aktualizuj głębokości wody w obu komórkach
                                       new_water_depth[r,c] -= flow_volume / dx
                                       new_water_depth[nr,nc] += flow_volume / dx


        water_depth = np.maximum(0.0, new_water_depth) # Upewnij się, że woda nie jest ujemna
        max_water_depth = np.maximum(max_water_depth, water_depth)
        
        # Opcjonalnie: Dodaj warunek sprawdzający, aby przerwać w razie problemów
        if np.isnan(water_depth).any() or np.isinf(water_depth).any():
            print(f"Wykryto niestabilność w kroku {t_step}! Przerywam symulację.")
            break

    return max_water_depth

def main(config):
    print("\n--- Uruchamianie Skryptu 1: Analiza Podtopień (Wersja 4.1) ---")
    paths, params = config['paths'], config['params']['flood']

    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale_factor = src.res[0] / target_res
        new_width = int(src.width * scale_factor); new_height = int(src.height * scale_factor)
        transform = src.transform * src.transform.scale(1/scale_factor, 1/scale_factor)
        profile.update({'height': new_height, 'width': new_width, 'transform': transform, 'dtype': 'float32', 'nodata': -9999})
        nmt = src.read(1, out_shape=(new_height, new_width), resampling=Resampling.bilinear)

    print("-> Przygotowywanie danych wejściowych...")
    landcover = align_raster(paths['landcover'], profile, 'nearest')

    # Manning coefficients
    manning = np.full(nmt.shape, params['manning_map']['default'], dtype=np.float32)
    for lc, val in params['manning_map'].items():
        if lc != 'default': manning[landcover == lc] = val

    # Parametry infiltracji - realistyczne wartości
    Ks = np.full(nmt.shape, 1e-6, dtype=np.float32)
    psi = np.full(nmt.shape, 0.1, dtype=np.float32)
    theta_diff = np.full(nmt.shape, 0.3, dtype=np.float32)

    # Dostosuj według pokrycia terenu
    Ks[landcover == 3] = 8e-5  # Lasy - wysoka infiltracja
    Ks[landcover == 5] = 3e-5  # Trawa - średnia infiltracja
    Ks[landcover == 6] = 1e-5  # Gleba - niska infiltracja
    Ks[(landcover == 1) | (landcover == 2)] = 1e-8  # Nieprzepuszczalne powierzchnie
    Ks[landcover == 7] = 1e-9  # Woda - praktycznie brak infiltracji

    # Parametry symulacji
    rainfall_intensity_ms = (params['total_rainfall_mm'] / 1000) / (params['rainfall_duration_h'] * 3600)
    water_depth_init = np.zeros_like(nmt, dtype=np.float32)

    print(f"-> Symulacja hydrauliczna: {params['total_rainfall_mm']}mm przez {params['rainfall_duration_h']}h...")
    max_depth = hydraulic_simulation_fixed(
        manning.astype(np.float32), water_depth_init.astype(np.float32),
        float(rainfall_intensity_ms), float(params['simulation_duration_h'] * 3600),
        float(params['dt_s']), float(target_res),
        psi.astype(np.float32), theta_diff.astype(np.float32), Ks.astype(np.float32),
        nmt.astype(np.float32)
    )

    # Usuń wartości poniżej progu (szum numeryczny)
    max_depth[max_depth < 0.01] = 0.0

    print("-> Zapisywanie wyniku...")
    profile.update(nodata=0.0)
    with rasterio.open(paths['output_flood_raster'], 'w', **profile) as dst:
        dst.write(max_depth, 1)

    print(f"--- Skrypt 1 zakończony! Max głębokość: {np.max(max_depth):.2f}m ---")
    return paths['output_flood_raster']
