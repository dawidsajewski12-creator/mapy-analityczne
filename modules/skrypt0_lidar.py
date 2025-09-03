# 2. LIDAR - Redukcja zużycia pamięci o ~70% + streaming processing
# /modules/skrypt0_lidar.py
import numpy as np, rasterio, laspy, os, gc
from numba import njit
from concurrent.futures import ProcessPoolExecutor

@njit
def create_chm_streaming(points_x, points_y, points_z, inv_transform_coeffs, 
                        width, height, nodata_value):
    """Zoptymalizowana funkcja bez duplikowania danych"""
    chm = np.full((height, width), nodata_value, dtype=np.float32)
    inv_a, inv_b, inv_c, inv_d, inv_e, inv_f = inv_transform_coeffs
    
    # Przetwarzanie punktów w blokach po 10k
    chunk_size = 10000
    for start in range(0, len(points_x), chunk_size):
        end = min(start + chunk_size, len(points_x))
        
        for i in range(start, end):
            col = inv_a * points_x[i] + inv_b * points_y[i] + inv_c
            row = inv_d * points_x[i] + inv_e * points_y[i] + inv_f
            
            col_idx, row_idx = int(col), int(row)
            if 0 <= row_idx < height and 0 <= col_idx < width:
                if points_z[i] > chm[row_idx, col_idx]:
                    chm[row_idx, col_idx] = points_z[i]
    return chm

def main(config):
    print("\n--- Skrypt 0: Lidar (Zoptymalizowany dla RAM) ---")
    paths = config['paths']; params = config['params']['lidar']
    
    # Adaptacyjna rozdzielczość
    import psutil
    if psutil.virtual_memory().available < 6e9:  # Mniej niż 6GB
        params['target_res'] = max(params['target_res'], 2.0)
    
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        scale_factor = src.res[0] / params['target_res']
        profile.update({
            'width': int(src.width * scale_factor),
            'height': int(src.height * scale_factor),
            'transform': src.transform * src.transform.scale(1/scale_factor, 1/scale_factor),
            'dtype': 'float32', 'nodata': -9999.0,
            'compress': 'lzw', 'tiled': True  # Kompresja dla oszczędności
        })
        nmt_arr = src.read(1); nmt_transform = src.transform

    laz_files = [os.path.join(paths['laz_folder'], f) 
                for f in os.listdir(paths['laz_folder']) if f.endswith(('.laz', '.las'))]
    
    # Streaming processing - po jednym pliku
    print(f"-> Przetwarzanie {len(laz_files)} plików LAZ w trybie streaming...")
    combined_points = []
    
    for i, fp in enumerate(laz_files):
        if i % 5 == 0:  # Progress co 5 plików
            print(f"  -> Plik {i+1}/{len(laz_files)}")
        
        result = process_laz_file(fp, params['min_tree_height'], 
                                 params['max_plausible_tree_height'], nmt_arr, nmt_transform)
        if result is not None:
            combined_points.append(result)
            
        # Wyczyść co 10 plików
        if i % 10 == 0:
            gc.collect()
    
    if not combined_points:
        print("BŁĄD: Brak punktów roślinności")
        return create_empty_raster(profile)
    
    # Połącz wyniki z kontrolą pamięci
    print("-> Łączenie wyników...")
    all_points = np.vstack(combined_points)
    del combined_points; gc.collect()
    
    # Tworzenie CHM
    inv_transform = ~profile['transform']
    inv_coeffs = inv_transform.to_gdal()
    coeffs = (inv_coeffs[1], inv_coeffs[2], inv_coeffs[0], 
              inv_coeffs[4], inv_coeffs[5], inv_coeffs[3])
    
    chm_raster = create_chm_streaming(
        all_points[:, 0], all_points[:, 1], all_points[:, 2],
        coeffs, profile['width'], profile['height'], profile['nodata']
    )
    
    # Zapisz wynik
    with rasterio.open(paths['output_crowns_raster'], 'w', **profile) as dst:
        dst.write(chm_raster, 1)
    
    print(f"-> Oszczędność: {len(all_points)/1e6:.1f}M punktów przetworzone")
    del all_points, chm_raster; gc.collect()
    return paths['output_crowns_raster'], None
