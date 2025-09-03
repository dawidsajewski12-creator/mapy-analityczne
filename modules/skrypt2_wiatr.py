# -*- coding: utf-8 -*-
# modules/skrypt2_wiatr.py - Wersja 6.0: Zaawansowana symulacja CFD z turbulencją
import numpy as np
import rasterio
from rasterio.enums import Resampling
from numba import njit, prange
import os

def align_raster(source_path, profile, resampling_method):
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

@njit(parallel=True)
def cfd_wind_simulation(u_init, v_init, height_field, landcover, dx, dt, 
                       base_wind_speed, wind_direction_rad, num_iterations):
    """Uproszczona symulacja CFD z turbulencją"""
    ny, nx = u_init.shape
    u, v = np.copy(u_init), np.copy(v_init)
    u_new, v_new = np.zeros_like(u), np.zeros_like(v)
    
    # Parametry fizyczne
    rho = 1.225  # gęstość powietrza [kg/m³]
    nu = 1.5e-5  # kinematic viscosity
    
    for iteration in prange(num_iterations):
        for i in prange(1, ny-1):
            for j in prange(1, nx-1):
                # Obecne prędkości
                u_curr, v_curr = u[i, j], v[i, j]
                
                # Gradienty wysokości (przeszkody)
                dh_dx = (height_field[i, j+1] - height_field[i, j-1]) / (2 * dx)
                dh_dy = (height_field[i+1, j] - height_field[i-1, j]) / (2 * dx)
                
                # Chropowatość na podstawie pokrycia terenu
                z0 = 0.1  # domyślna chropowatość
                if landcover[i, j] == 1: z0 = 0.01    # nawierzchnie
                elif landcover[i, j] == 2: z0 = 1.0   # budynki
                elif landcover[i, j] == 3: z0 = 0.8   # lasy
                elif landcover[i, j] == 5: z0 = 0.05  # trawa
                elif landcover[i, j] == 6: z0 = 0.03  # gleba
                elif landcover[i, j] == 7: z0 = 0.001 # woda
                
                # Profil logarytmiczny wiatru
                height_agl = max(1.0, height_field[i, j] + 10.0)  # 10m nad powierzchnią
                log_factor = np.log(height_agl / z0) / np.log(10.0 / z0)
                target_speed = base_wind_speed * log_factor
                
                # Kierunek bazowy
                u_target = target_speed * np.cos(wind_direction_rad)
                v_target = target_speed * np.sin(wind_direction_rad)
                
                # Efekt przeszkód - defleksja wiatru
                obstacle_factor = 1.0
                if height_field[i, j] > height_field[i-1, j] + 2.0:  # przeszkoda z południa
                    u_target *= 0.3; v_target *= 1.2
                    obstacle_factor = 0.6
                elif height_field[i, j] > height_field[i+1, j] + 2.0:  # przeszkoda z północy
                    u_target *= 0.3; v_target *= 1.2
                    obstacle_factor = 0.6
                
                if height_field[i, j] > height_field[i, j-1] + 2.0:  # przeszkoda z zachodu
                    u_target *= 1.2; v_target *= 0.3
                    obstacle_factor = 0.6
                elif height_field[i, j] > height_field[i, j+1] + 2.0:  # przeszkoda ze wschodu
                    u_target *= 1.2; v_target *= 0.3
                    obstacle_factor = 0.6
                
                # Dyfuzja i adwekcja (uproszczona)
                # Laplacjan dla dyfuzji
                d2u_dx2 = (u[i, j+1] - 2*u[i, j] + u[i, j-1]) / (dx*dx)
                d2u_dy2 = (u[i+1, j] - 2*u[i, j] + u[i-1, j]) / (dx*dx)
                d2v_dx2 = (v[i, j+1] - 2*v[i, j] + v[i, j-1]) / (dx*dx)
                d2v_dy2 = (v[i+1, j] - 2*v[i, j] + v[i-1, j]) / (dx*dx)
                
                # Adwekcja (transport przez przepływ)
                du_dx = (u[i, j+1] - u[i, j-1]) / (2 * dx)
                du_dy = (u[i+1, j] - u[i-1, j]) / (2 * dx)
                dv_dx = (v[i, j+1] - v[i, j-1]) / (2 * dx)
                dv_dy = (v[i+1, j] - v[i-1, j]) / (2 * dx)
                
                # Równania NS (bardzo uproszczone)
                advection_u = -(u_curr * du_dx + v_curr * du_dy)
                advection_v = -(u_curr * dv_dx + v_curr * dv_dy)
                
                diffusion_u = nu * (d2u_dx2 + d2u_dy2)
                diffusion_v = nu * (d2v_dx2 + d2v_dy2)
                
                # Siła przywracająca do wiatru bazowego
                restore_u = 0.1 * (u_target - u_curr)
                restore_v = 0.1 * (v_target - v_curr)
                
                # Efekt topografii
                topo_u = -0.5 * dh_dx * obstacle_factor
                topo_v = -0.5 * dh_dy * obstacle_factor
                
                # Aktualizacja prędkości
                u_new[i, j] = u_curr + dt * (advection_u + diffusion_u + restore_u + topo_u)
                v_new[i, j] = v_curr + dt * (advection_v + diffusion_v + restore_v + topo_v)
                
                # Ograniczenia fizyczne
                speed = np.sqrt(u_new[i, j]**2 + v_new[i, j]**2)
                if speed > base_wind_speed * 3.0:  # max 3x bazowa prędkość
                    factor = (base_wind_speed * 3.0) / speed
                    u_new[i, j] *= factor
                    v_new[i, j] *= factor
        
        u, v = u_new.copy(), v_new.copy()
        
        # Warunki brzegowe
        u[0, :] = u[1, :]
        u[-1, :] = u[-2, :]
        u[:, 0] = u[:, 1]
        u[:, -1] = u[:, -2]
        
        v[0, :] = v[1, :]
        v[-1, :] = v[-2, :]
        v[:, 0] = v[:, 1]
        v[:, -1] = v[:, -2]
    
    return u, v

def main(config):
    print("\n--- Uruchamianie Skryptu 2: Zaawansowana Analiza Wiatru ---")
    paths = config['paths']
    params = config['params']['wind']
    weather = config['params']['wind']

    print("-> Przygotowanie siatki obliczeniowej...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale = src.res[0] / target_res
        w, h = int(src.width * scale), int(src.height * scale)
        transform = src.transform * src.transform.scale(1/scale, 1/scale)
        profile.update({
            'height': h, 'width': w, 'transform': transform, 'dtype': 'float32'
        })

    # Wczytaj dane
    nmt = align_raster(paths['nmt'], profile, 'bilinear')
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    
    # Pole wysokości (budynki + teren)
    height_field = np.maximum(nmt, nmpt)
    
    print("-> Inicjalizacja pola wiatru...")
    wind_direction_rad = np.deg2rad(weather['wind_direction'])
    base_speed = weather['wind_speed']
    
    # Inicjalne pole prędkości (jednorodne)
    u_init = np.full((h, w), base_speed * np.cos(wind_direction_rad), dtype=np.float32)
    v_init = np.full((h, w), base_speed * np.sin(wind_direction_rad), dtype=np.float32)
    
    print("-> Symulacja CFD...")
    dt = 0.1  # krok czasowy
    num_iterations = 50  # liczba iteracji
    
    u_final, v_final = cfd_wind_simulation(
        u_init, v_init, height_field, landcover, 
        target_res, dt, base_speed, wind_direction_rad, num_iterations
    )
    
    # Oblicz prędkość i kierunek wynikowy
    wind_speed = np.sqrt(u_final**2 + v_final**2)
    wind_direction = np.rad2deg(np.arctan2(v_final, u_final)) % 360
    
    print("-> Zapisywanie wyników...")
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed.astype(np.float32), 1)

    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(wind_direction.astype(np.float32), 1)

    print(f"--- Zaawansowana symulacja wiatru zakończona! Max prędkość: {np.max(wind_speed):.1f} m/s ---")
    return paths['output_wind_speed_raster']
