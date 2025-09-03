# 3. PODTOPIENIA - Redukcja iteracji + lepsze parametry fizyczne
# /modules/skrypt1_podtopienia.py
import numpy as np, rasterio, gc
from numba import njit, prange

@njit(parallel=True)
def hydraulic_simulation_optimized(manning, water_depth, rainfall_rate,
                                  total_steps, dt, dx, Ks_effective, 
                                  slope_magnitude, nmt):
    """Zoptymalizowana symulacja z realistycznymi parametrami"""
    max_depth = np.copy(water_depth)
    gravity = 9.81
    
    # Efektywna infiltracja - uproszczony model Hortona
    infiltration_capacity = Ks_effective.copy()
    
    for step in prange(total_steps):
        # Opady tylko przez pierwsze 2h
        if step * dt < 7200:  # 2 godziny
            water_depth += rainfall_rate * dt
        
        # Infiltracja - model Hortona z degradacją
        for i in range(water_depth.shape[0]):
            for j in range(water_depth.shape[1]):
                if water_depth[i, j] > 0.001:
                    # Degradacja infiltracji w czasie
                    current_rate = infiltration_capacity[i, j] * np.exp(-step * dt / 3600)
                    infiltrated = min(current_rate * dt, water_depth[i, j])
                    water_depth[i, j] -= infiltrated
        
        # Przepływ powierzchniowy - uproszczony kinematic wave
        new_water = np.copy(water_depth)
        
        for i in prange(1, water_depth.shape[0]-1):
            for j in prange(1, water_depth.shape[1]-1):
                if water_depth[i, j] > 0.01:  # Próg przepływu
                    # Prędkość Manning-Strickler
                    depth = water_depth[i, j]
                    slope = max(slope_magnitude[i, j], 0.001)  # Min slope
                    velocity = (depth**(2/3) * np.sqrt(slope)) / manning[i, j]
                    
                    # Ograniczenie CFL
                    max_velocity = 0.5 * dx / dt
                    velocity = min(velocity, max_velocity)
                    
                    # Kierunek spływu (prosty gradient)
                    if slope_magnitude[i, j] > 0.001:
                        # Uproszczony przepływ w kierunku największego spadku
                        flow_rate = velocity * depth / dx
                        outflow = min(flow_rate * dt, depth * 0.3)  # Max 30% wody
                        new_water[i, j] -= outflow
        
        water_depth = new_water
        max_depth = np.maximum(max_depth, water_depth)
    
    return max_depth

def main(config):
    print("\n--- Skrypt 1: Podtopienia (Zoptymalizowany) ---")
    paths, params = config['paths'], config['params']['flood']
    
    # Automatyczne dostosowanie rozdzielczości
    import psutil
    if psutil.virtual_memory().available < 5e9:
        params['target_res'] = max(params['target_res'], 10.0)
        print(f"-> Zwiększono rozdzielczość do {params['target_res']}m (oszczędność RAM)")
    
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        scale = src.res[0] / params['target_res']
        w, h = int(src.width * scale), int(src.height * scale)
        profile.update({
            'height': h, 'width': w,
            'transform': src.transform * src.transform.scale(1/scale, 1/scale),
            'dtype': 'float32', 'compress': 'lzw'
        })
        nmt = src.read(1, out_shape=(h, w), resampling=rasterio.enums.Resampling.bilinear)
    
    # Landcover z oszczędnością pamięci
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    
    # Realistyczne parametry Manning
    manning = np.full(nmt.shape, 0.05, dtype=np.float32)
    manning_values = {1: 0.013, 2: 0.1, 3: 0.08, 5: 0.03, 6: 0.025, 7: 0.025}
    for lc, val in manning_values.items():
        manning[landcover == lc] = val
    
    # Efektywna infiltracja (m/s) - realistyczne wartości
    Ks = np.full(nmt.shape, 5e-6, dtype=np.float32)  # Domyślna gleba
    Ks[landcover == 3] = 3e-5   # Las - wysoka
    Ks[landcover == 5] = 1e-5   # Trawa - średnia  
    Ks[landcover == 6] = 8e-6   # Gleba - niska
    Ks[(landcover == 1) | (landcover == 2)] = 1e-8  # Nieprzepuszczalne
    Ks[landcover == 7] = 0      # Woda
    
    # Oblicz gradient terenu (magnitude)
    gy, gx = np.gradient(nmt, params['target_res'])
    slope_mag = np.sqrt(gx**2 + gy**2)
    del gx, gy; gc.collect()
    
    # Parametry symulacji - zredukowane dla wydajności
    rainfall_rate = (params['total_rainfall_mm'] / 1000) / (params['rainfall_duration_h'] * 3600)
    total_steps = min(int(params['simulation_duration_h'] * 3600 / params['dt_s']), 720)  # Max 720 kroków
    
    print(f"-> Symulacja {total_steps} kroków, {params['total_rainfall_mm']}mm deszczu")
    
    water_init = np.zeros_like(nmt, dtype=np.float32)
    max_depth = hydraulic_simulation_optimized(
        manning, water_init, float(rainfall_rate), total_steps,
        float(params['dt_s']), float(params['target_res']), 
        Ks, slope_mag, nmt
    )
    
    # Usuń szum numeryczny
    max_depth[max_depth < 0.02] = 0
    
    # Zapisz z kompresją
    profile.update(nodata=0.0)
    with rasterio.open(paths['output_flood_raster'], 'w', **profile) as dst:
        dst.write(max_depth.astype(np.float32), 1)
    
    print(f"-> Max głębokość: {np.max(max_depth):.2f}m, Powierzchnia: {np.sum(max_depth>0.02)*params['target_res']**2/1e4:.1f}ha")
    del max_depth, manning, Ks; gc.collect()
    return paths['output_flood_raster']
