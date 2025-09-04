# -*- coding: utf-8 -*-
# modules/skrypt2_wiatr.py - Zaawansowana symulacja CFD v8.0
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
def lattice_boltzmann_d2q9(f, rho, u, v, tau, obstacle_mask, u_inlet, v_inlet):
    """Implementacja Lattice Boltzmann Method D2Q9 - szybka i stabilna"""
    # Wektory prędkości D2Q9
    ex = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1], dtype=np.float32)
    ey = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1], dtype=np.float32)
    w = np.array([4/9, 1/9, 1/9, 1/9, 1/9, 1/36, 1/36, 1/36, 1/36], dtype=np.float32)
    
    ny, nx, nq = f.shape
    f_new = np.zeros_like(f)
    
    # Streaming step
    for i in prange(ny):
        for j in prange(nx):
            for q in range(9):
                ni = i - int(ey[q])
                nj = j - int(ex[q])
                if 0 <= ni < ny and 0 <= nj < nx:
                    f_new[i, j, q] = f[ni, nj, q]
    
    # Collision step
    for i in prange(ny):
        for j in prange(nx):
            if obstacle_mask[i, j]:
                # Bounce-back dla przeszkód
                f_new[i, j, 1] = f[i, j, 3]  # East -> West
                f_new[i, j, 3] = f[i, j, 1]  # West -> East
                f_new[i, j, 2] = f[i, j, 4]  # North -> South
                f_new[i, j, 4] = f[i, j, 2]  # South -> North
                f_new[i, j, 5] = f[i, j, 7]  # NE -> SW
                f_new[i, j, 7] = f[i, j, 5]  # SW -> NE
                f_new[i, j, 6] = f[i, j, 8]  # NW -> SE
                f_new[i, j, 8] = f[i, j, 6]  # SE -> NW
            else:
                # Oblicz gęstość i prędkość
                rho_local = 0.0
                ux_local = 0.0
                uy_local = 0.0
                
                for q in range(9):
                    rho_local += f_new[i, j, q]
                    ux_local += ex[q] * f_new[i, j, q]
                    uy_local += ey[q] * f_new[i, j, q]
                
                if rho_local > 0:
                    ux_local /= rho_local
                    uy_local /= rho_local
                
                rho[i, j] = rho_local
                u[i, j] = ux_local
                v[i, j] = uy_local
                
                # Warunki brzegowe dla wlotu
                if j == 0:  # Lewa granica
                    ux_local = u_inlet
                    uy_local = v_inlet
                    rho_local = 1.0
                
                # BGK collision
                u_sq = ux_local**2 + uy_local**2
                for q in range(9):
                    cu = ex[q] * ux_local + ey[q] * uy_local
                    feq = w[q] * rho_local * (1 + 3*cu + 4.5*cu**2 - 1.5*u_sq)
                    f_new[i, j, q] = f_new[i, j, q] - (f_new[i, j, q] - feq) / tau
    
    return f_new

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

@njit(parallel=True)
def generate_streamlines(u, v, x_start, y_start, dx, dy, max_length=100):
    """Generuje linie prądu dla wizualizacji"""
    n_seeds = len(x_start)
    ny, nx = u.shape
    
    streamlines = []
    for seed in prange(n_seeds):
        x, y = x_start[seed], y_start[seed]
        line_x, line_y = [x], [y]
        
        for step in range(max_length):
            i = int(y / dy)
            j = int(x / dx)
            
            if i <= 0 or i >= ny-1 or j <= 0 or j >= nx-1:
                break
            
            # Interpolacja prędkości
            u_interp = u[i, j]
            v_interp = v[i, j]
            
            if abs(u_interp) < 1e-6 and abs(v_interp) < 1e-6:
                break
            
            # Krok Runge-Kutta
            dt = 0.5 * min(dx, dy) / max(abs(u_interp), abs(v_interp), 1e-6)
            x += u_interp * dt
            y += v_interp * dt
            
            line_x.append(x)
            line_y.append(y)
        
        streamlines.append((np.array(line_x), np.array(line_y)))
    
    return streamlines

def create_advanced_visualization(u, v, vorticity, buildings, wind_speed, profile, output_path):
    """Tworzy zaawansowaną wizualizację CFD w stylu profesjonalnych symulacji"""
    ny, nx = u.shape
    
    # Setup siatki
    x = np.linspace(0, nx * profile['transform'][0], nx)
    y = np.linspace(0, ny * abs(profile['transform'][4]), ny)
    X, Y = np.meshgrid(x, y)
    
    # Utwórz figurę z subplotami
    fig = plt.figure(figsize=(20, 12))
    
    # 1. Pole prędkości z wektorami
    ax1 = plt.subplot(2, 3, 1)
    speed = np.sqrt(u**2 + v**2)
    im1 = ax1.contourf(X, Y, speed, levels=50, cmap='viridis', alpha=0.8)
    
    # Strzałki przepływu (przerzedzone)
    skip = max(1, nx // 30)
    ax1.quiver(X[::skip, ::skip], Y[::skip, ::skip], 
               u[::skip, ::skip], v[::skip, ::skip], 
               scale=50, alpha=0.7, color='white', width=0.003)
    
    # Budynki
    ax1.contour(X, Y, buildings, levels=[0.5], colors='red', linewidths=2)
    plt.colorbar(im1, ax=ax1, label='Prędkość [m/s]')
    ax1.set_title('Pole prędkości z wektorami przepływu')
    ax1.set_aspect('equal')
    
    # 2. Wirowanie (vorticity)
    ax2 = plt.subplot(2, 3, 2)
    vort_levels = np.linspace(-np.percentile(np.abs(vorticity), 95), 
                             np.percentile(np.abs(vorticity), 95), 50)
    im2 = ax2.contourf(X, Y, vorticity, levels=vort_levels, cmap='RdBu_r', alpha=0.8)
    ax2.contour(X, Y, buildings, levels=[0.5], colors='black', linewidths=1)
    plt.colorbar(im2, ax=ax2, label='Wirowanie [1/s]')
    ax2.set_title('Pole wirowania (turbulencje)')
    ax2.set_aspect('equal')
    
    # 3. Linie prądu
    ax3 = plt.subplot(2, 3, 3)
    ax3.contourf(X, Y, speed, levels=30, cmap='plasma', alpha=0.6)
    
    # Generuj punkty startowe dla linii prądu
    y_seeds = np.linspace(y[0], y[-1], 15)
    x_seeds = np.full_like(y_seeds, x[5])  # Start z lewej strony
    
    # Oblicz linie prądu
    streamlines = generate_streamlines(u, v, x_seeds, y_seeds, 
                                     profile['transform'][0], abs(profile['transform'][4]))
    
    for line_x, line_y in streamlines:
        if len(line_x) > 2:
            ax3.plot(line_x, line_y, 'white', alpha=0.8, linewidth=1.5)
    
    ax3.contour(X, Y, buildings, levels=[0.5], colors='red', linewidths=2)
    ax3.set_title('Linie prądu')
    ax3.set_aspect('equal')
    
    # 4. Dyfuzja "dymu" - symulacja cząstek
    ax4 = plt.subplot(2, 3, 4)
    
    # Symulacja rozprzestrzeniania się znacznika
    tracer = np.zeros_like(u)
    tracer[:, :5] = 1.0  # Źródło na lewej granicy
    
    # Dyfuzja z konwekcją
    for _ in range(20):
        # Adwekcja
        tracer_new = np.zeros_like(tracer)
        for i in range(1, ny-1):
            for j in range(1, nx-1):
                if not buildings[i, j]:
                    # Upwind scheme
                    if u[i, j] > 0:
                        tracer_new[i, j] += u[i, j] * tracer[i, j-1] * 0.1
                    else:
                        tracer_new[i, j] += -u[i, j] * tracer[i, j+1] * 0.1
                    
                    if v[i, j] > 0:
                        tracer_new[i, j] += v[i, j] * tracer[i-1, j] * 0.1
                    else:
                        tracer_new[i, j] += -v[i, j] * tracer[i+1, j] * 0.1
                    
                    tracer_new[i, j] += tracer[i, j] * 0.6
        
        # Dyfuzja
        tracer = gaussian_filter(tracer_new, sigma=0.5)
        tracer[buildings > 0.5] = 0  # Usuń znacznik z budynków
    
    im4 = ax4.contourf(X, Y, tracer, levels=30, cmap='Reds', alpha=0.8)
    ax4.contour(X, Y, buildings, levels=[0.5], colors='black', linewidths=2)
    plt.colorbar(im4, ax=ax4, label='Koncentracja znacznika')
    ax4.set_title('Dyfuzja znacznika (symulacja dymu)')
    ax4.set_aspect('equal')
    
    # 5. Ciśnienie (rekonstruowane z dywergencji)
    ax5 = plt.subplot(2, 3, 5)
    
    # Oblicz dywergencję
    div = np.zeros_like(u)
    for i in range(1, ny-1):
        for j in range(1, nx-1):
            div[i, j] = (u[i, j+1] - u[i, j-1])/(2*profile['transform'][0]) + \
                       (v[i+1, j] - v[i-1, j])/(2*abs(profile['transform'][4]))
    
    # Przybliżone ciśnienie (zakładając -∇p ~ div(u))
    pressure = gaussian_filter(-div, sigma=1.0)
    
    im5 = ax5.contourf(X, Y, pressure, levels=50, cmap='RdYlBu_r', alpha=0.8)
    ax5.contour(X, Y, buildings, levels=[0.5], colors='black', linewidths=1)
    plt.colorbar(im5, ax=ax5, label='Ciśnienie względne')
    ax5.set_title('Pole ciśnienia')
    ax5.set_aspect('equal')
    
    # 6. Profil prędkości
    ax6 = plt.subplot(2, 3, 6)
    
    # Średni profil prędkości w funkcji wysokości
    mid_col = nx // 2
    height_profile = np.mean(speed[:, mid_col-5:mid_col+5], axis=1)
    heights = y
    
    ax6.plot(height_profile, heights, 'b-', linewidth=2, label='CFD')
    
    # Teoretyczny profil logarytmiczny
    z0 = 0.1  # Chropowatość
    u_star = wind_speed * 0.1  # Prędkość tarcia
    y_theory = np.linspace(z0, heights[-1], 100)
    u_theory = (u_star / 0.41) * np.log(y_theory / z0)
    ax6.plot(u_theory, y_theory, 'r--', linewidth=2, label='Logarytmiczny')
    
    ax6.set_xlabel('Prędkość [m/s]')
    ax6.set_ylabel('Wysokość [m]')
    ax6.set_title('Profil prędkości')
    ax6.legend()
    ax6.grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    # Zapisz wizualizację
    viz_path = output_path.replace('.tif', '_cfd_visualization.png')
    plt.savefig(viz_path, dpi=150, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    plt.close()
    
    return viz_path

def main(config):
    """Główna funkcja symulacji CFD"""
    print("\n--- Uruchamianie Zaawansowanej Symulacji CFD v8.0 ---")
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
    wind_direction = params.get('wind_direction', 270.0)  # Zachód
    
    # Konwersja kierunku na komponenty (meteorologiczna konwencja)
    wind_rad = np.deg2rad(wind_direction)
    u_inlet = wind_speed * np.sin(wind_rad)  # Składowa E-W
    v_inlet = wind_speed * np.cos(wind_rad)  # Składowa N-S
    
    print(f"-> Symulacja: {wind_speed:.1f} m/s z kierunku {wind_direction:.0f}°")
    print(f"   Komponenty: U={u_inlet:.2f}, V={v_inlet:.2f}")
    
    # Inicjalizacja Lattice Boltzmann
    print("-> Inicjalizacja Lattice Boltzmann D2Q9...")
    
    nq = 9  # Liczba kierunków w D2Q9
    f = np.ones((h, w, nq), dtype=np.float32) * (1/9)  # Rozkład równowagowy
    rho = np.ones((h, w), dtype=np.float32)
    u = np.full((h, w), u_inlet, dtype=np.float32)
    v = np.full((h, w), v_inlet, dtype=np.float32)
    
    # Maska przeszkód (budynki + rozszerzenie dla stabilności)
    obstacle_mask = building_mask.astype(bool)
    
    # Parametry LBM
    tau = 0.8  # Czas relaksacji (wpływa na lepkość)
    
    # Symulacja LBM
    print("-> Uruchamianie symulacji Lattice Boltzmann...")
    n_steps = 500
    save_interval = 50
    
    for step in range(n_steps):
        f = lattice_boltzmann_d2q9(f, rho, u, v, tau, obstacle_mask, u_inlet, v_inlet)
        
        if (step + 1) % save_interval == 0:
            speed = np.sqrt(u**2 + v**2)
            print(f"  -> Krok {step+1}/{n_steps}, Max prędkość: {np.max(speed):.2f} m/s")
    
    # Oblicz dodatkowe pola
    print("-> Obliczanie pól pochodnych...")
    
    speed = np.sqrt(u**2 + v**2)
    vorticity = calculate_vorticity(u, v, target_res, target_res)
    
    # Wygładź wyniki
    speed = gaussian_filter(speed, sigma=0.5)
    u = gaussian_filter(u, sigma=0.5)
    v = gaussian_filter(v, sigma=0.5)
    
    # Zastosuj maskę budynków (zero w budynkach)
    speed[obstacle_mask] = 0
    u[obstacle_mask] = 0
    v[obstacle_mask] = 0
    
    # Kierunek wiatru w stopniach
    wind_direction_field = (np.rad2deg(np.arctan2(u, v)) + 360) % 360
    wind_direction_field[speed < 0.1] = wind_direction  # Domyślny kierunek dla małych prędkości
    
    print("-> Tworzenie zaawansowanej wizualizacji...")
    
    # Utwórz wizualizację CFD
    viz_path = create_advanced_visualization(u, v, vorticity, building_mask.astype(float), 
                                           wind_speed, profile, paths['output_wind_speed_raster'])
    print(f"  -> Zapisano wizualizację: {viz_path}")
    
    # Zapisz wyniki jako rastry
    print("-> Zapisywanie wyników...")
    
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(speed.astype(np.float32), 1)
        
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(wind_direction_field.astype(np.float32), 1)
    
    # Zapisz dodatkowe dane
    extra_outputs = {
        'vorticity': vorticity,
        'u_component': u,
        'v_component': v,
        'buildings_used': building_mask,
        'simulation_params': {
            'wind_speed': wind_speed,
            'wind_direction': wind_direction,
            'tau': tau,
            'steps': n_steps,
            'resolution': target_res
        }
    }
    
    # Zapisz pola prędkości jako komponenty (do dalszego wykorzystania)
    u_path = paths['output_wind_speed_raster'].replace('.tif', '_u_component.tif')
    v_path = paths['output_wind_speed_raster'].replace('.tif', '_v_component.tif')
    vort_path = paths['output_wind_speed_raster'].replace('.tif', '_vorticity.tif')
    
    with rasterio.open(u_path, 'w', **profile) as dst:
        dst.write(u.astype(np.float32), 1)
    with rasterio.open(v_path, 'w', **profile) as dst:
        dst.write(v.astype(np.float32), 1)
    with rasterio.open(vort_path, 'w', **profile) as dst:
        dst.write(vorticity.astype(np.float32), 1)
    
    # Zapisz metadane symulacji
    metadata_path = paths['output_wind_speed_raster'].replace('.tif', '_metadata.json')
    with open(metadata_path, 'w') as f:
        metadata = {
            'simulation_type': 'Lattice Boltzmann D2Q9',
            'wind_speed_input': wind_speed,
            'wind_direction_input': wind_direction,
            'max_speed_result': float(np.max(speed)),
            'avg_speed_result': float(np.mean(speed[speed > 0.1])),
            'buildings_count': int(np.sum(building_mask)),
            'resolution_m': target_res,
            'grid_size': [h, w],
            'files_generated': {
                'speed': os.path.basename(paths['output_wind_speed_raster']),
                'direction': os.path.basename(paths['output_wind_dir_raster']),
                'u_component': os.path.basename(u_path),
                'v_component': os.path.basename(v_path),
                'vorticity': os.path.basename(vort_path),
                'visualization': os.path.basename(viz_path)
            }
        }
        json.dump(metadata, f, indent=2)
    
    print(f"--- Symulacja CFD zakończona pomyślnie! ---")
    print(f"   Max prędkość: {np.max(speed):.1f} m/s")
    print(f"   Średnia prędkość: {np.mean(speed[speed > 0.1]):.1f} m/s")
    print(f"   Pliki wygenerowane:")
    print(f"     - Prędkość: {paths['output_wind_speed_raster']}")
    print(f"     - Kierunek: {paths['output_wind_dir_raster']}")
    print(f"     - Wizualizacja: {viz_path}")
    print(f"     - Metadane: {metadata_path}")
    
    return paths['output_wind_speed_raster']
