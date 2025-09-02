# /modules/skrypt1_podtopienia.py
import os; import numpy as np; import rasterio; from rasterio.warp import reproject, Resampling; from numba import njit, prange
@njit(parallel=True)
def simulation_step_numba(water_depth, dem, manning_n, pixel_size, dt):
    rows, cols = water_depth.shape; flux_out, flux_in = np.zeros_like(water_depth), np.zeros_like(water_depth); K = 0.1
    for r in prange(1, rows - 1):
        for c in range(1, cols - 1):
            if water_depth[r, c] > 0:
                h_center = dem[r, c] + water_depth[r, c]
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        if dr == 0 and dc == 0: continue
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < rows and 0 <= nc < cols:
                            h_neighbor = dem[nr, nc] + water_depth[nr, nc]; slope = h_center - h_neighbor
                            if slope > 0:
                                avg_n = (manning_n[r, c] + manning_n[nr, nc]) / 2.0
                                if avg_n > 0:
                                    flow = K * np.sqrt(slope) / avg_n * water_depth[r, c] * dt; flow = min(flow, water_depth[r, c] * pixel_size**2 / 8.0)
                                    if flow > 0: flux_out[r, c] += flow; flux_in[nr, nc] += flow
    new_water_depth = water_depth + (flux_in - flux_out) / (pixel_size**2)
    return np.maximum(0, new_water_depth)
def align_raster(path, base_profile, resampling_method='nearest'):
    with rasterio.open(path) as src:
        aligned_arr = np.empty((base_profile['height'], base_profile['width']), dtype=src.read(1).dtype)
        reproject(source=rasterio.band(src, 1), destination=aligned_arr, src_transform=src.transform, src_crs=src.crs, dst_transform=base_profile['transform'], dst_crs=base_profile['crs'], resampling=Resampling[resampling_method])
    return aligned_arr
def main(config):
    print("\n--- Uruchamianie Skryptu 1: Analiza Podtopień ---")
    paths = config['paths']; params = config['params']['flood']
    print("-> Przygotowywanie danych wejściowych...")
    with rasterio.open(paths['nmt']) as src:
        scale_factor = src.res[0] / params['target_res']; ny = int(src.height * scale_factor); nx = int(src.width * scale_factor)
        new_shape = (ny, nx); nmt = src.read(1, out_shape=new_shape, resampling=Resampling.bilinear); profile = src.profile.copy()
        transform = src.transform * src.transform.scale(1/scale_factor, 1/scale_factor); profile.update({'height': ny, 'width': nx, 'transform': transform, 'dtype': 'float32'})
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear'); landcover = align_raster(paths['landcover'], profile, 'nearest'); crowns = align_raster(paths['output_crowns_raster'], profile, 'nearest'); crown_mask = crowns > 0
    print("-> Modelowanie intercepcji i obliczanie spływu...")
    effective_rainfall_mm = np.full(nmt.shape, params['total_rainfall_mm'], dtype=np.float32)
    effective_rainfall_mm[crown_mask] -= params['interception_mm']; effective_rainfall_mm[effective_rainfall_mm < 0] = 0
    cn_raster = np.full(nmt.shape, params['cn_map']['default'], dtype=np.float32)
    for lc_class, cn_val in params['cn_map'].items():
        if isinstance(lc_class, int): cn_raster[landcover == lc_class] = cn_val
    s = (25400 / cn_raster) - 254; ia = 0.2 * s
    runoff_mm = np.where(effective_rainfall_mm > ia, ((effective_rainfall_mm - ia)**2) / (effective_rainfall_mm - ia + s), 0)
    runoff_per_second = (runoff_mm / 1000.0) / (params['rainfall_duration_h'] * 3600)
    print("-> Rozpoczynanie dynamicznej symulacji hydraulicznej...")
    dem_with_barriers = np.where((nmpt - nmt) > params['obstacle_height_m'], nmt + 50, nmt)
    manning_raster = np.full(nmt.shape, params['manning_map']['default'], dtype=np.float32)
    for lc_class, n_val in params['manning_map'].items():
        if isinstance(lc_class, int): manning_raster[landcover == lc_class] = n_val
    total_steps = int(params['simulation_duration_h'] * 3600 / params['dt_s']); rain_steps = int(params['rainfall_duration_h'] * 3600 / params['dt_s'])
    water_depth = np.zeros_like(nmt, dtype=np.float32); max_water_depth = np.zeros_like(nmt, dtype=np.float32)
    for i in range(total_steps):
        if i < rain_steps: water_depth += runoff_per_second * params['dt_s']
        water_depth = simulation_step_numba(water_depth, dem_with_barriers, manning_raster, params['target_res'], params['dt_s'])
        max_water_depth = np.maximum(max_water_depth, water_depth)
        if (i + 1) % 50 == 0: print(f"  ...krok symulacji {i+1}/{total_steps}")
    print("-> Zapisywanie wyniku...")
    output_path = paths['output_flood_raster']; profile.update(nodata=-9999.0)
    with rasterio.open(output_path, 'w', **profile) as dst: dst.write(max_water_depth.astype(np.float32), 1)
    print(f"--- Skrypt 1 zakończony pomyślnie! Wynik: {paths['output_flood_raster']} ---")
    return paths['output_flood_raster']
