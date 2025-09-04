# -*- coding: utf-8 -*-
# modules/skrypt2_wiatr.py - Stabilna symulacja CFD v8.1
import numpy as np
import rasterio
from rasterio.enums import Resampling
from numba import njit, prange
from scipy.ndimage import gaussian_filter
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import os
import json

def align_raster(source_path, profile, resampling_method):
    """Przeskalowanie rastra do docelowej siatki"""
    if not os.path.exists(source_path):
        return np.zeros((profile['height'], profile['width']), dtype=np.float32)
    with rasterio.open(source_path) as src:
        return src.read(1, out_shape=(profile['height'], profile['width']), 
                       resampling=getattr(Resampling, resampling_method))

@njit(parallel=True)
def simple_cfd_simulation(u, v, building_mask, u_inlet, v_inlet, dt, dx, dy, viscosity):
    """Uproszczona ale stabilna symulacja CFD"""
    ny, nx = u.shape
    u_new = np.copy(u)
    v_new = np.copy(v)
    
    for i in prange(1, ny-1):
        for j in prange(1, nx-1):
            if building_mask[i, j]:
                # Warunki no-slip dla budynków
                u_new[i, j] = 0.0
                v_new[i, j] = 0.0
                continue
            
            # Składowe adwekcji (upwind scheme)
            u_curr = u[i, j]
            v_curr = v[i, j]
            
            # Gradient prędkości
            if u_curr > 0:
                dudx = (u[i, j] - u[i, j-1]) / dx
            else:
                dudx = (u[i, j+1] - u[i, j]) / dx
                
            if v_curr > 0:
                dudy = (u[i, j] - u[i-1, j]) / dy
            else:
                dudy = (u[i+1, j] - u[i, j]) / dy
            
            if u_curr > 0:
                dvdx = (v[i, j] - v[i, j-1]) / dx
            else:
                dvdx = (v[i, j+1] - v[i, j]) / dx
                
            if v_curr > 0:
                dvdy = (v[i, j] - v[i-1, j]) / dy
            else:
                dvdy = (v[i+1, j] - v[i, j]) / dy
            
            # Laplacjan (dyfuzja)
            lap_u = (u[i, j+1] - 2*u[i, j] + u[i, j-1])/(dx*dx) + \
                    (u[i+1, j] - 2*u[i, j] + u[i-1, j])/(dy*dy)
            lap_v = (v[i, j+1] - 2*v[i, j] + v[i, j-1])/(dx*dx) + \
                    (v[i+1, j] - 2*v[i, j] + v[i-1, j])/(dy*dy)
            
            # Aktualizacja z kontrolą stabilności
            du_dt = -u_curr * dudx - v_curr * dudy + viscosity * lap_u
            dv_dt = -u_curr * dvdx - v_curr * dvdy + viscosity * lap_v
            
            # Ograniczenie zmian dla stabilności
            max_change = 0.1
            du_dt = max(-max_change, min(max_change, du_dt))
            dv_dt = max(-max_change, min(max_change, dv_dt))
            
            u_new[i, j] = u[i, j] + dt * du_dt
            v_new[i, j] = v[i, j] + dt * dv_dt
    
    # Warunki brzegowe - wlot
    for i in range(ny):
        if not building_mask[i, 0]:
            u_new[i, 0] = u_inlet
            v_new[i, 0] = v_inlet
    
    # Warunki brzegowe - pozostałe granice (gradient zero)
    u_new[:, -1] = u_new[:, -2]
    v_new[:, -1] = v_new[:, -2]
    u_new[0, :] = u_new[1, :]
    v_new[0, :] = v_new[1, :]
    u_new[-1, :] = u_new[-2, :]
    v_new[-1, :] = v_new[-2, :]
    
    return u_new, v_new

@njit(parallel=True)
def calculate_vorticity(u, v, dx, dy):
    """Oblicza wirowanie do wizualizacji przepływów"""
    ny, nx = u.shape
    vorticity = np.zeros_like(u)
    
    for i in prange(1, ny-1):
        for j in prange(1, nx-1):
            dvdx = (v[i, j+1] - v[i, j-1]) / (2 * dx)
            dudy = (u[i+1, j] - u[i-1, j]) / (2 * dy)
            vorticity[i, j] = dvdx - dudy
    
    return vorticity

def create_safe_visualization(u, v, vorticity, buildings, wind_speed, profile, output_path):
    """Bezpieczna wizualizacja CFD z obsługą NaN"""
    ny, nx = u.shape
    
    # Sprawdź i napraw wartości NaN
    u = np.nan_to_num(u, nan=0.0, posinf=0.0, neginf=0.0)
    v = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)
    vorticity = np.nan_to_num(vorticity, nan=0.0, posinf=0.0, neginf=0.0)
    
    # Setup siatki
    x = np.linspace(0, nx * profile['transform'][0], nx)
    y = np.linspace(0, ny * abs(profile['transform'][4]), ny)
    X, Y = np.meshgrid(x, y)
    
    # Utwórz figurę
    fig = plt.figure(figsize=(20, 12), facecolor='white')
    
    # 1. Pole prędkości z wektorami
    ax1 = plt.subplot(2, 3, 1)
    speed = np.sqrt(u**2 + v**2)
    speed_max = np.percentile(speed[speed > 0], 95) if np.any(speed > 0) else 1.0
    
    levels = np.linspace(0, speed_max, 20)
    try:
        im1 = ax1.contourf(X, Y, speed, levels=levels, cmap='viridis', alpha=0.8)
        plt.colorbar(im1, ax=ax1, label='Prędkość [m/s]')
    except:
        im1 = ax1.imshow(speed, cmap='viridis', alpha=0.8, extent=[x[0], x[-1], y[0], y[-1]])
        plt.colorbar(im1, ax=ax1, label='Prędkość [m/s]')
    
    # Strzałki przepływu
    skip = max(1, nx // 20)
    ax1.quiver(X[::skip, ::skip], Y[::skip, ::skip], 
               u[::skip, ::skip], v[::skip, ::skip], 
               scale=speed_max*20, alpha=0.7, color='white', width=0.003)
    
    # Budynki
    if np.any(buildings > 0.5):
        ax1.contour(X, Y, buildings, levels=[0.5], colors='red', linewidths=2)
    
    ax1.set_title('Pole prędkości z wektorami przepływu')
    ax1.set_aspect('equal')
    ax1.set_xlabel('Odległość [m]')
    ax1.set_ylabel('Odległość [m]')
    
    # 2. Wirowanie
    ax2 = plt.subplot(2, 3, 2)
    vort_max = np.percentile(np.abs(vorticity), 95) if np.any(vorticity != 0) else 0.1
    vort_levels = np.linspace(-vort_max, vort_max, 20)
    
    try:
        im2 = ax2.contourf(X, Y, vorticity, levels=vort_levels, cmap='RdBu_r', alpha=0.8)
        plt.colorbar(im2, ax=ax2, label='Wirowanie [1/s]')
    except:
        im2 = ax2.imshow(vorticity, cmap='RdBu_r', alpha=0.8, extent=[x[0], x[-1], y[0], y[-1]])
        plt.colorbar(im2, ax=ax2, label='Wirowanie [1/s]')
    
    if np.any(buildings > 0.5):
        ax2.contour(X, Y, buildings, levels=[0.5], colors='black', linewidths=1)
    ax2.set_title('Pole wirowania (turbulencje)')
    ax2.set_aspect('equal')
    ax2.set_xlabel('Odległość [m]')
    ax2.set_ylabel('Odległość [m]')
    
    # 3. Uproszczone linie prądu
    ax3 = plt.subplot(2, 3, 3)
    try:
        im3 = ax3.contourf(X, Y, speed, levels=15, cmap='plasma', alpha=0.6)
        
        # Proste linie prądu - zaczynające się z lewej strony
        y_seeds = np.linspace(y[ny//4], y[3*ny//4], 10)
        x_seed = x[5] if len(x) > 5 else x[0]
        
        for y_start in y_seeds:
            x_line = [x_seed]
            y_line = [y_start]
            
            x_curr, y_curr = x_seed, y_start
            for _ in range(min(50, nx//2)):
                i = int((y_curr - y[0]) / (y[-1] - y[0]) * (ny-1))
                j = int((x_curr - x[0]) / (x[-1] - x[0]) * (nx-1))
                
                if i < 0 or i >= ny or j < 0 or j >= nx:
                    break
                
                if buildings[i, j] > 0.5:
                    break
                
                step = min(abs(profile['transform'][0]), abs(profile['transform'][4]))
                x_curr += u[i, j] * step * 0.5
                y_curr += v[i, j] * step * 0.5
                
                if x_curr >= x[-1] or y_curr >= y[-1] or y_curr <= y[0]:
                    break
                
                x_line.append(x_curr)
                y_line.append(y_curr)
            
            if len(x_line) > 2:
                ax3.plot(x_line, y_line, 'white', alpha=0.8, linewidth=1.5)
    
    except Exception as e:
        print(f"Uwaga: Problem z liniami prądu: {e}")
        im3 = ax3.imshow(speed, cmap='plasma', alpha=0.6, extent=[x[0], x[-1], y[0], y[-1]])
    
    if np.any(buildings > 0.5):
        ax3.contour(X, Y, buildings, levels=[0.5], colors='red', linewidths=2)
    ax3.set_title('Linie prądu')
    ax3.set_aspect('equal')
    ax3.set_xlabel('Odległość [m]')
    ax3.set_ylabel('Odległość [m]')
    
    # 4. Symulacja dyfuzji
    ax4 = plt.subplot(2, 3, 4)
    # Uproszczona dyfuzja - gaussowska z kierunkiem wiatru
    tracer = np.zeros_like(u)
    tracer[:, :max(1, nx//10)] = 1.0  # Źródło
    
    # Symulacja adwekcji
    for _ in range(5):
        tracer_new = np.copy(tracer)
        for i in range(1, ny-1):
            for j in range(1, nx-1):
                if buildings[i, j] > 0.5:
                    tracer_new[i, j] = 0
                    continue
                
                # Upwind advection
                advection = 0
                if u[i, j] > 0 and j > 0:
                    advection += u[i, j] * tracer[i, j-1] * 0.1
                elif u[i, j] < 0 and j < nx-1:
                    advection += -u[i, j] * tracer[i, j+1] * 0.1
                
                if v[i, j] > 0 and i > 0:
                    advection += v[i, j] * tracer[i-1, j] * 0.1
                elif v[i, j] < 0 and i < ny-1:
                    advection += -v[i, j] * tracer[i+1, j] * 0.1
                
                tracer_new[i, j] = tracer[i, j] * 0.9 + advection
        
        tracer = gaussian_filter(tracer_new, sigma=0.8)
        tracer[buildings > 0.5] = 0
    
    try:
        im4 = ax4.contourf(X, Y, tracer, levels=15, cmap='Reds', alpha=0.8)
        plt.colorbar(im4, ax=ax4, label='Koncentracja')
    except:
        im4 = ax4.imshow(tracer, cmap='Reds', alpha=0.8, extent=[x[0], x[-1], y[0], y[-1]])
        plt.colorbar(im4, ax=ax4, label='Koncentracja')
    
    if np.any(buildings > 0.5):
        ax4.contour(X, Y, buildings, levels=[0.5], colors='black', linewidths=2)
    ax4.set_title('Dyfuzja znacznika')
    ax4.set_aspect('equal')
    ax4.set_xlabel('Odległość [m]')
    ax4.set_ylabel('Odległość [m]')
    
    # 5. Pole ciśnienia (przybliżone)
    ax5 = plt.subplot(2, 3, 5)
    # Oblicz dywergencję jako przybliżenie ciśnienia
    pressure = np.zeros_like(u)
    for i in range(1, ny-1):
        for j in range(1, nx-1):
            divergence = (u[i, j+1] - u[i, j-1])/(2*abs(profile['transform'][0])) + \
                        (v[i+1, j] - v[i-1, j])/(2*abs(profile['transform'][4]))
            pressure[i, j] = -divergence
    
    pressure = gaussian_filter(pressure, sigma=1.0)
    pressure_max = np.percentile(np.abs(pressure), 95) if np.any(pressure != 0) else 0.1
    
    try:
        pressure_levels = np.linspace(-pressure_max, pressure_max, 20)
        im5 = ax5.contourf(X, Y, pressure, levels=pressure_levels, cmap='RdYlBu_r', alpha=0.8)
        plt.colorbar(im5, ax=ax5, label='Ciśnienie względne')
    except:
        im5 = ax5.imshow(pressure, cmap='RdYlBu_r', alpha=0.8, extent=[x[0], x[-1], y[0], y[-1]])
        plt.colorbar(im5, ax=ax5, label='Ciśnienie względne')
    
    if np.any(buildings > 0.5):
        ax5.contour(X, Y, buildings, levels=[0.5], colors='black', linewidths=1)
    ax5.set_title('Pole ciśnienia')
    ax5.set_aspect('equal')
    ax5.set_xlabel('Odległość [m]')
    ax5.set_ylabel('Odległość [m]')
    
    # 6. Profil prędkości
    ax6 = plt.subplot(2, 3, 6)
    
    # Średni profil w środku domeny
    mid_col = nx // 2
    height_profile = np.mean(speed[:, max(0, mid_col-2):min(nx, mid_col+3)], axis=1)
    heights = y
    
    ax6.plot(height_profile, heights, 'b-', linewidth=3, label='CFD wynik')
    
    # Teoretyczny profil logarytmiczny
    z0 = 0.1
    u_star = wind_speed * 0.1
    y_theory = np.linspace(max(z0, heights[0]), heights[-1], 50)
    u_theory = (u_star / 0.41) * np.log(y_theory / z0)
    u_theory = np.clip(u_theory, 0, wind_speed * 2)
    
    ax6.plot(u_theory, y_theory, 'r--', linewidth=2, label='Logarytmiczny (teoria)')
    
    ax6.set_xlabel('Prędkość [m/s]')
    ax6.set_ylabel('Wysokość [m]')
    ax6.set_title('Profil prędkości')
    ax6.legend()
    ax6.grid(True, alpha=0.3)
    ax6.set_xlim(0, max(wind_speed * 1.5, np.max(height_profile) * 1.1))
    
    plt.tight_layout()
    
    # Zapisz wizualizację
    viz_path = output_path.replace('.tif', '_cfd_visualization.png')
    try:
        plt.savefig(viz_path, dpi=150, bbox_inches='tight', 
                    facecolor='white', edgecolor='none')
        print(f"  -> Wizualizacja zapisana: {viz_path}")
    except Exception as e:
        print(f"  -> Błąd zapisu wizualizacji: {e}")
        viz_path = None
    
    plt.close()
    return viz_path

def main(config):
    """Główna funkcja symulacji CFD"""
    print("\n--- Uruchamianie Stabilnej Symulacji CFD v8.1 ---")
    paths = config['paths']
    params = config['params']['wind']
    
    # Setup siatki obliczeniowej
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale = src.res[0] / target_res
        w, h = int(src.width * scale), int(src.height * scale)
        transform = src.transform * src.transform.scale(1/scale, 1/scale)
        profile.update({'height': h, 'width': w, 'transform': transform, 'dtype': 'float32'})

    print("-> Przygotowanie danych wejściowych...")
    
    # Wczytaj dane podstawowe
    nmt = align_raster(paths['nmt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    
    # Wczytaj model budynków
    building_mask = np.zeros((h, w), dtype=np.uint8)
    building_heights = np.zeros((h, w), dtype=np.float32)
    
    if 'output_buildings_mask' in paths and os.path.exists(paths['output_buildings_mask']):
        building_mask = align_raster(paths['output_buildings_mask'], profile, 'nearest')
        print(f"  -> Wczytano maskę budynków: {np.sum(building_mask)} pikseli")
    
    if 'output_buildings_raster' in paths and os.path.exists(paths['output_buildings_raster']):
        building_heights = align_raster(paths['output_buildings_raster'], profile, 'bilinear')
        print(f"  -> Średnia wysokość budynków: {np.mean(building_heights[building_mask > 0]):.1f}m")
    else:
        # Fallback: użyj różnicy NMPT-NMT
        if 'nmpt' in paths and os.path.exists(paths['nmpt']):
            nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
            height_diff = nmpt - nmt
            building_mask = (height_diff > 2.5).astype(np.uint8)
            building_heights = np.where(building_mask, height_diff, 0)
            print("  -> Używam różnicy NMPT-NMT jako model budynków")
    
    # Parametry symulacji
    wind_speed = params.get('wind_speed', 5.0)
    wind_direction = params.get('wind_direction', 270.0)
    
    # Konwersja kierunku na komponenty
    wind_rad = np.deg2rad(wind_direction)
    u_inlet = wind_speed * np.sin(wind_rad)
    v_inlet = wind_speed * np.cos(wind_rad)
    
    print(f"-> Symulacja: {wind_speed:.1f} m/s z kierunku {wind_direction:.0f}°")
    print(f"   Komponenty: U={u_inlet:.2f}, V={v_inlet:.2f}")
    
    # Inicjalizacja pól prędkości
    print("-> Inicjalizacja stabilnej symulacji CFD...")
    
    u = np.full((h, w), u_inlet * 0.1, dtype=np.float32)  # Łagodne rozpoczęcie
    v = np.full((h, w), v_inlet * 0.1, dtype=np.float32)
    
    # Parametry symulacji
    dt = 0.01  # Mniejszy krok czasowy dla stabilności
    viscosity = target_res * wind_speed * 0.01  # Lepkość
    
    # Symulacja CFD
    print("-> Uruchamianie stabilnej symulacji...")
    n_steps = 200  # Mniej kroków ale stabilniejszych
    
    for step in range(n_steps):
        u, v = simple_cfd_simulation(u, v, building_mask.astype(bool), 
                                   u_inlet, v_inlet, dt, target_res, target_res, viscosity)
        
        # Sprawdź stabilność
        if np.any(np.isnan(u)) or np.any(np.isnan(v)) or np.any(np.isinf(u)) or np.any(np.isinf(v)):
            print(f"  -> Niestabilność w kroku {step}! Resetowanie...")
            u = np.full((h, w), u_inlet * 0.5, dtype=np.float32)
            v = np.full((h, w), v_inlet * 0.5, dtype=np.float32)
            dt *= 0.5  # Zmniejsz krok czasowy
            continue
        
        if (step + 1) % 50 == 0:
            speed = np.sqrt(u**2 + v**2)
            max_speed = np.max(speed) if not np.isnan(speed).any() else 0
            print(f"  -> Krok {step+1}/{n_steps}, Max prędkość: {max_speed:.2f} m/s")
    
    # Końcowe czyszczenie danych
    u = np.nan_to_num(u, nan=0.0, posinf=wind_speed*2, neginf=-wind_speed*2)
    v = np.nan_to_num(v, nan=0.0, posinf=wind_speed*2, neginf=-wind_speed*2)
    
    # Zastosuj maskę budynków
    u[building_mask.astype(bool)] = 0
    v[building_mask.astype(bool)] = 0
    
    # Oblicz pola pochodne
    print("-> Obliczanie pól pochodnych...")
    speed = np.sqrt(u**2 + v**2)
    vorticity = calculate_vorticity(u, v, target_res, target_res)
    wind_direction_field = (np.rad2deg(np.arctan2(u, v)) + 360) % 360
    wind_direction_field[speed < 0.1] = wind_direction
    
    # Wygładź wyniki
    speed = gaussian_filter(speed, sigma=0.5)
    u = gaussian_filter(u, sigma=0.3)
    v = gaussian_filter(v, sigma=0.3)
    
    print("-> Tworzenie bezpiecznej wizualizacji...")
    viz_path = create_safe_visualization(u, v, vorticity, building_mask.astype(float), 
                                       wind_speed, profile, paths['output_wind_speed_raster'])
    
    # Zapisz wyniki
    print("-> Zapisywanie wyników...")
    
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(speed.astype(np.float32), 1)
        
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(wind_direction_field.astype(np.float32), 1)
    
    # Zapisz komponenty
    u_path = paths['output_wind_speed_raster'].replace('.tif', '_u_component.tif')
    v_path = paths['output_wind_speed_raster'].replace('.tif', '_v_component.tif')
    vort_path = paths['output_wind_speed_raster'].replace('.tif', '_vorticity.tif')
    
    with rasterio.open(u_path, 'w', **profile) as dst:
        dst.write(u.astype(np.float32), 1)
    with rasterio.open(v_path, 'w', **profile) as dst:
        dst.write(v.astype(np.float32), 1)
    with rasterio.open(vort_path, 'w', **profile) as dst:
        dst.write(vorticity.astype(np.float32), 1)
    
    # Metadane
    metadata_path = paths['output_wind_speed_raster'].replace('.tif', '_metadata.json')
    with open(metadata_path, 'w') as f:
        metadata = {
            'simulation_type': 'Stabilna CFD - Simplified Navier-Stokes',
            'wind_speed_input': wind_speed,
            'wind_direction_input': wind_direction,
            'max_speed_result': float(np.max(speed)),
            'avg_speed_result': float(np.mean(speed[speed > 0.1])),
            'buildings_count': int(np.sum(building_mask)),
            'resolution_m': target_res,
            'grid_size': [h, w],
            'simulation_steps': n_steps,
            'time_step': dt,
            'viscosity': viscosity
        }
        json.dump(metadata, f, indent=2)
    
    print(f"--- Symulacja CFD zakończona pomyślnie! ---")
    print(f"   Max prędkość: {np.max(speed):.1f} m/s")
    print(f"   Średnia prędkość: {np.mean(speed[speed > 0.1]):.1f} m/s")
    if viz_path:
        print(f"   Wizualizacja: {os.path.basename(viz_path)}")
    
    return paths['output_wind_speed_raster']
