# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
from numba import njit, prange
import os

def align_raster(source_path, profile, resampling_method):
    """Dopasowuje raster do zadanego profilu."""
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

@njit
def green_ampt_infiltration(Ks, psi, theta_diff, t, cumulative_infiltrated):
    """Model infiltracji Greena-Ampta."""
    if cumulative_infiltrated == 0:
        return Ks * (1 + (psi * theta_diff) / 1e-9) # Uniknięcie dzielenia przez zero
    return Ks * (1 + (psi * theta_diff) / cumulative_infiltrated)

@njit(parallel=True)
def run_kinematic_wave(nmt, manning, water_depth, rainfall_intensity_ms,
                       total_time_s, dt_s, dx, psi, theta_diff, Ks):
    """
    Zoptymalizowana symulacja spływu powierzchniowego modelem fali kinematycznej.
    """
    max_water_depth = np.copy(water_depth)
    cumulative_infiltrated = np.zeros_like(nmt, dtype=np.float32)
    
    # Współczynnik do obliczeń (S^1/2 / n)
    conveyance_factor = np.zeros_like(nmt, dtype=np.float32)
    
    # Oblicz nachylenie w kierunkach x i y
    slope_y, slope_x = np.gradient(nmt, dx)
    slope = np.sqrt(slope_x**2 + slope_y**2)
    
    # Uniknięcie dzielenia przez zero w miejscach płaskich
    slope[slope < 1e-6] = 1e-6 
    
    conveyance_factor = np.sqrt(slope) / manning

    num_steps = int(total_time_s / dt_s)
    
    for t_step in prange(num_steps):
        
        # 1. Dodaj wodę z opadu
        if (t_step * dt_s) < (2.0 * 3600): # Czas trwania opadu (2h)
            water_depth += rainfall_intensity_ms * dt_s

        # 2. Oblicz infiltrację
        infiltration_rate = np.zeros_like(nmt, dtype=np.float32)
        for i in range(nmt.shape[0]):
            for j in range(nmt.shape[1]):
                 if water_depth[i, j] > 0:
                    potential_infiltration = green_ampt_infiltration(Ks[i,j], psi[i,j], theta_diff[i,j], t_step * dt_s, cumulative_infiltrated[i, j]) * dt_s
                    actual_infiltration = min(potential_infiltration, water_depth[i, j])
                    water_depth[i, j] -= actual_infiltration
                    cumulative_infiltrated[i, j] += actual_infiltration
        
        # 3. Oblicz przepływ (Q = A * v = (h*dx) * (1/n * R^(2/3) * S^(1/2)))
        # Dla szerokiego kanału R ~= h, więc v = (1/n * h^(2/3) * S^(1/2))
        # Qx = h * dx * (1/n * h^(2/3) * S_x^(1/2))
        
        # Prędkość przepływu
        velocity_term = water_depth**(2.0/3.0) * conveyance_factor
        
        vx = velocity_term * np.sign(slope_x)
        vy = velocity_term * np.sign(slope_y)
        
        # Strumień wody
        flux_x = vx * water_depth * dt_s
        flux_y = vy * water_depth * dt_s
        
        # Aktualizuj głębokość wody na podstawie bilansu strumieni
        # Uproszczony schemat FTCS (Forward-Time Central-Space), niestabilny, ale szybki dla dema
        # W praktyce wymagałby stabilniejszego schematu (np. upwind)
        
        new_water_depth = np.copy(water_depth)
        
        # Wypływ z komórki
        new_water_depth -= (np.abs(flux_x) + np.abs(flux_y)) / dx
        
        # Wpływ do komórki (z sąsiadów)
        # X direction
        flux_x_in = np.roll(flux_x, 1, axis=1)
        flux_x_in[:, 0] = 0
        new_water_depth += np.abs(flux_x_in) / dx

        # Y direction
        flux_y_in = np.roll(flux_y, 1, axis=0)
        flux_y_in[0, :] = 0
        new_water_depth += np.abs(flux_y_in) / dx

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
        if lc_class != 'default':
            manning[landcover == lc_class] = man_val

    # Parametry dla modelu Greena-Ampta (uproszczone, zależne od landcover)
    Ks = np.full(nmt.shape, 1e-6, dtype=np.float32) # Przewodność hydrauliczna [m/s]
    psi = np.full(nmt.shape, 0.1, dtype=np.float32) # Potencjał ssania [m]
    theta_diff = np.full(nmt.shape, 0.4, dtype=np.float32) # Różnica wilgotności
    
    # Przypisz parametry na podstawie landcover
    Ks[landcover == 3] = 5e-5 # Lasy
    Ks[landcover == 5] = 1e-5 # Trawa
    Ks[landcover == 6] = 2e-6 # Gleba
    Ks[(landcover == 1) | (landcover == 2) | (landcover == 7)] = 1e-9 # Powierzchnie nieprzepuszczalne
    
    rainfall_intensity_ms = (params['total_rainfall_mm'] / 1000) / (params['rainfall_duration_h'] * 3600)
    water_depth_init = np.zeros_like(nmt, dtype=np.float32)

    print("-> Rozpoczynanie dynamicznej symulacji hydraulicznej...")
    max_depth = run_kinematic_wave(
        nmt, manning, water_depth_init, rainfall_intensity_ms,
        params['simulation_duration_h'] * 3600, params['dt_s'],
        target_res, psi, theta_diff, Ks
    )
    
    print("-> Zapisywanie wyniku...")
    output_path = paths['output_flood_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(max_depth, 1)

    print(f"--- Skrypt 1 zakończony pomyślnie! Wynik: {output_path} ---")
    return output_path
