# -*- coding: utf-8 -*-
# modules/skrypt2_wiatr.py - Wersja 7.0: Wydajna symulacja CFD z FFT solver
import numpy as np
import rasterio
from rasterio.enums import Resampling
from numba import njit, prange
from scipy.fft import dst, idst
from scipy.ndimage import binary_dilation
import os

def align_raster(source_path, profile, resampling_method):
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), 
                        resampling=getattr(Resampling, resampling_method))
    return array

def poisson_solver_fft(b, dx, dy):
    """FFT-based Poisson solver - szybki i stabilny"""
    m, n = b.shape
    b_int = b[1:-1, 1:-1] * dx * dy
    
    # DST dla warunków brzegowych Dirichleta
    B = dst(dst(b_int, type=1, axis=0), type=1, axis=1)
    
    i = np.arange(1, m-1)[:, None]
    j = np.arange(1, n-1)[None, :]
    
    # Eigenvalues dla operatora Laplace'a
    denom = ((2*np.cos(np.pi*i/(m-1)) - 2)/dx**2 + 
             (2*np.cos(np.pi*j/(n-1)) - 2)/dy**2)
    
    # Rozwiąż w przestrzeni fourierowskiej
    P_hat = np.divide(B, denom, out=np.zeros_like(B), where=(denom!=0))
    
    # Inverse DST
    p_int = idst(idst(P_hat, type=1, axis=0), type=1, axis=1)
    p_int /= (2*(m-1)*(n-1))
    
    # Odtwórz pełne pole
    p = np.zeros_like(b)
    p[1:-1, 1:-1] = p_int
    
    # Warunki brzegowe Neumanna
    p[:, 0] = p[:, 1]; p[:, -1] = p[:, -2]
    p[0, :] = p[1, :]; p[-1, :] = p[-2, :]
    
    return p

@njit(parallel=True)
def compute_rhs(u, v, b, dx, dy, dt, rho):
    """Oblicz prawą stronę równania Poissona"""
    ny, nx = u.shape
    idx = 1.0/dx; idy = 1.0/dy
    
    for i in prange(1, ny-1):
        for j in prange(1, nx-1):
            # Dywergencja prędkości
            dudx = (u[i, j+1] - u[i, j-1]) * 0.5 * idx
            dvdy = (v[i+1, j] - v[i-1, j]) * 0.5 * idy
            
            # Nieliniowe człony adwekcyjne
            adv = dudx**2 + 2*dudx*dvdy + dvdy**2
            
            b[i, j] = rho*(dudx + dvdy)/dt - rho*adv

@njit(parallel=True)
def velocity_update(u, v, p, mask, dx, dy, dt, rho, nu, 
                   u_in, wind_direction, z0, h_ref):
    """Aktualizacja pola prędkości metodą Navier-Stokes"""
    ny, nx = u.shape
    idx = 1.0/dx; idy = 1.0/dy
    idx2 = 1.0/(dx*dx); idy2 = 1.0/(dy*dy)
    
    # Konwersja kierunku wiatru na komponenty
    ang = np.deg2rad(wind_direction)
    u_comp = u_in * np.sin(ang)
    v_comp = u_in * np.cos(ang)
    
    un = u.copy(); vn = v.copy()
    
    # Aktualizacja wnętrza domeny
    for i in prange(1, ny-1):
        for j in prange(1, nx-1):
            if mask[i, j]:
                u[i, j] = 0.0  # No-slip na budynkach
                v[i, j] = 0.0
                continue
                
            # Schemat upwind dla adwekcji
            u_x = (un[i,j] - un[i,j-1])*idx if un[i,j] > 0 else (un[i,j+1] - un[i,j])*idx
            u_y = (un[i,j] - un[i-1,j])*idy if vn[i,j] > 0 else (un[i+1,j] - un[i,j])*idy
            v_x = (vn[i,j] - vn[i,j-1])*idx if un[i,j] > 0 else (vn[i,j+1] - vn[i,j])*idx
            v_y = (vn[i,j] - vn[i-1,j])*idy if vn[i,j] > 0 else (vn[i+1,j] - vn[i,j])*idy
            
            # Dyfuzja (Laplacjan)
            lap_u = (un[i,j+1] - 2*un[i,j] + un[i,j-1])*idx2 + \
                    (un[i+1,j] - 2*un[i,j] + un[i-1,j])*idy2
            lap_v = (vn[i,j+1] - 2*vn[i,j] + vn[i,j-1])*idx2 + \
                    (vn[i+1,j] - 2*vn[i,j] + vn[i-1,j])*idy2
            
            # Gradient ciśnienia
            grad_px = (p[i,j+1] - p[i,j-1]) * 0.5 * idx
            grad_py = (p[i+1,j] - p[i-1,j]) * 0.5 * idy
            
            # Navier-Stokes update
            u[i,j] = un[i,j] - dt*(un[i,j]*u_x + vn[i,j]*u_y) - dt*grad_px/rho + nu*dt*lap_u
            v[i,j] = vn[i,j] - dt*(un[i,j]*v_x + vn[i,j]*v_y) - dt*grad_py/rho + nu*dt*lap_v
    
    # Warunki brzegowe - profil logarytmiczny
    log_factor = np.log(h_ref/z0)
    
    # Wlot z odpowiedniej strony na podstawie kierunku wiatru
    for i in range(ny):
        for j in range(nx):
            # Wysokość nad gruntem
            height = max(z0, (i+1)*dx)
            velocity_factor = np.log(height/z0) / log_factor
            
            # Brzegi domeny
            if i == 0 or i == ny-1 or j == 0 or j == nx-1:
                u[i,j] = u_comp * velocity_factor
                v[i,j] = v_comp * velocity_factor
    
    return u, v

def main(config):
    print("\n--- Uruchamianie Zaawansowanej Symulacji CFD ---")
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

    print("-> Przygotowanie danych...")
    nmt = align_raster(paths['nmt'], profile, 'bilinear')
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    
    # Maska budynków (z dylacją dla lepszej stabilności)
    building_mask = (nmpt - nmt) > params.get('building_threshold', 2.5)
    building_mask = binary_dilation(building_mask, structure=np.ones((3,3)))
    
    # Parametry fizyczne
    rho = 1.225      # gęstość powietrza [kg/m³]
    nu = 1.5e-5      # lepkość kinematyczna [m²/s]
    dt = 0.001       # krok czasowy [s]
    wind_speed = params['wind_speed']
    wind_direction = params['wind_direction']
    z0 = 0.1         # chropowatość powierzchni
    h_ref = 10.0     # wysokość referencyjna
    
    # Inicjalizacja pól prędkości
    u = np.zeros((h, w), dtype=np.float32)
    v = np.zeros((h, w), dtype=np.float32)
    p = np.zeros((h, w), dtype=np.float32)
    b = np.zeros((h, w), dtype=np.float32)
    
    print(f"-> Symulacja CFD: {wind_speed:.1f} m/s, {wind_direction:.0f}°")
    
    # Iteracje CFD (mniej iteracji dla szybkości)
    n_iterations = 100
    
    for iteration in range(n_iterations):
        # 1. Oblicz RHS równania Poissona
        compute_rhs(u, v, b, target_res, target_res, dt, rho)
        
        # 2. Rozwiąż równanie Poissona dla ciśnienia (FFT)
        p = poisson_solver_fft(b, target_res, target_res)
        
        # 3. Aktualizuj pole prędkości
        u, v = velocity_update(u, v, p, building_mask, target_res, target_res, 
                              dt, rho, nu, wind_speed, wind_direction, z0, h_ref)
        
        # Wyświetl postęp co 20 iteracji
        if (iteration + 1) % 20 == 0:
            max_u = np.max(np.abs(u))
            max_v = np.max(np.abs(v))
            print(f"  -> Iteracja {iteration+1}/{n_iterations}, Max U: {max_u:.2f}, Max V: {max_v:.2f}")
    
    # Oblicz wynikowe pola
    wind_speed_field = np.sqrt(u**2 + v**2)
    wind_direction_field = np.rad2deg(np.arctan2(u, v)) % 360
    
    # Zapisz wyniki
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed_field.astype(np.float32), 1)
        
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(wind_direction_field.astype(np.float32), 1)
    
    print(f"--- CFD zakończona! Max prędkość: {np.max(wind_speed_field):.1f} m/s ---")
    return paths['output_wind_speed_raster']
