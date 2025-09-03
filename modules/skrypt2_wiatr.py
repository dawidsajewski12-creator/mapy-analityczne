# 4. WIATR - Zmniejszenie iteracji CFD + lepszy model fizyczny
# /modules/skrypt2_wiatr.py
import numpy as np, rasterio, gc
from numba import njit, prange

@njit(parallel=True)
def wind_field_optimized(base_u, base_v, height_field, landcover, 
                        dx, iterations, base_speed):
    """Zoptymalizowany model wiatru z realistic boundary layer"""
    ny, nx = base_u.shape
    u, v = base_u.copy(), base_v.copy()
    
    # Parametry boundary layer
    karman_const = 0.41
    
    for iter in prange(iterations):
        u_new, v_new = u.copy(), v.copy()
        
        for i in prange(2, ny-2):
            for j in prange(2, nx-2):
                # Wysokość nad powierzchnią (10m standard)
                height_agl = height_field[i, j] + 10.0
                
                # Chropowatość terenu
                z0 = 0.03  # domyślna
                if landcover[i, j] == 1: z0 = 0.01    # nawierzchnie
                elif landcover[i, j] == 2: z0 = 2.0   # budynki  
                elif landcover[i, j] == 3: z0 = 1.0   # las
                elif landcover[i, j] == 5: z0 = 0.05  # trawa
                elif landcover[i, j] == 7: z0 = 0.001 # woda
                
                # Profil logarytmiczny wiatru
                log_factor = np.log(height_agl / z0) / np.log(10.0 / z0)
                target_speed = base_speed * log_factor
                
                # Efekty topograficzne - speed-up/slow-down
                height_diff = height_field[i, j] - np.mean(height_field[i-1:i+2, j-1:j+2])
                if height_diff > 2.0:  # Wzgórze - przyspieszenie
                    target_speed *= 1.3
                elif height_diff < -2.0:  # Dolina - spowolnienie
                    target_speed *= 0.7
                
                # Kierunek bazowy z defleksją
                wind_dir = np.arctan2(base_v[i, j], base_u[i, j])
                
                # Defleksja wokół przeszkód
                if landcover[i, j] == 2:  # Budynek
                    # Obszar turbulencji za budynkiem
                    target_speed *= 0.4
                    
                target_u = target_speed * np.cos(wind_dir)
                target_v = target_speed * np.sin(wind_dir)
                
                # Relaksacja do celu (stabilność numeryczna)
                relax_factor = 0.1
                u_new[i, j] = u[i, j] + relax_factor * (target_u - u[i, j])
                v_new[i, j] = v[i, j] + relax_factor * (target_v - v[i, j])
                
                # Ograniczenie prędkości (realistyczne)
                speed = np.sqrt(u_new[i, j]**2 + v_new[i, j]**2)
                if speed > base_speed * 2.0:
                    factor = (base_speed * 2.0) / speed
                    u_new[i, j] *= factor
                    v_new[i, j] *= factor
        
        u, v = u_new, v_new
    
    return u, v

def main(config):
    print("\n--- Skrypt 2: Wiatr (Zoptymalizowany CFD) ---")
    paths, params = config['paths'], config['params']['wind']
    
    # Dostosowanie rozdzielczości
    import psutil
    if psutil.virtual_memory().available < 4e9:
        params['target_res'] = max(params['target_res'], 10.0)
    
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        scale = src.res[0] / params['target_res']
        w, h = int(src.width * scale), int(src.height * scale)
        profile.update({
            'height': h, 'width': w,
            'transform': src.transform * src.transform.scale(1/scale, 1/scale),
            'dtype': 'float32', 'compress': 'lzw'
        })
    
    # Wczytaj dane z optymalizacją pamięci
    nmt = align_raster(paths['nmt'], profile, 'bilinear')
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    
    height_field = np.maximum(nmt, nmpt)
    del nmpt; gc.collect()
    
    # Pole wiatru bazowego
    wind_rad = np.deg2rad(params['wind_direction'])
    base_speed = params['wind_speed']
    
    u_base = np.full((h, w), base_speed * np.cos(wind_rad), dtype=np.float32)
    v_base = np.full((h, w), base_speed * np.sin(wind_rad), dtype=np.float32)
    
    print(f"-> CFD z {params['target_res']}m siatką, wiatr: {base_speed:.1f}m/s z {params['wind_direction']}°")
    
    # Zredukowana liczba iteracji dla wydajności
    iterations = 20 if psutil.virtual_memory().available > 6e9 else 10
    
    u_final, v_final = wind_field_optimized(
        u_base, v_base, height_field, landcover,
        params['target_res'], iterations, base_speed
    )
    
    wind_speed = np.sqrt(u_final**2 + v_final**2)
    wind_direction = np.rad2deg(np.arctan2(v_final, u_final)) % 360
    
    # Zapisz wyniki
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed.astype(np.float32), 1)
        
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(wind_direction.astype(np.float32), 1)
    
    print(f"-> Zakres prędkości: {np.min(wind_speed):.1f} - {np.max(wind_speed):.1f} m/s")
    del u_final, v_final, wind_speed, wind_direction; gc.collect()
    return paths['output_wind_speed_raster']
