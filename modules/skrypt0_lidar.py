# -*- coding: utf-8 -*-
import numpy as np
import rasterio
import laspy
import os
import geopandas as gpd
from numba import njit, prange
from concurrent.futures import ProcessPoolExecutor, as_completed
from rasterio.transform import Affine

@njit(parallel=True)
def create_chm_from_points(points_x, points_y, points_z, 
                           inv_a, inv_b, inv_c, inv_d, inv_e, inv_f, 
                           width, height, nodata_value):
    """Szybkie tworzenie rastra CHM z punktów przy użyciu Numba (z manualną transformacją)."""
    chm = np.full((height, width), nodata_value, dtype=np.float32)
    for i in prange(len(points_x)):
        # Manualna inwersja transformacji afinicznej
        col = inv_a * points_x[i] + inv_b * points_y[i] + inv_c
        row = inv_d * points_x[i] + inv_e * points_y[i] + inv_f
        
        col_idx, row_idx = int(col), int(row)

        if 0 <= row_idx < height and 0 <= col_idx < width:
            if points_z[i] > chm[row_idx, col_idx]:
                chm[row_idx, col_idx] = points_z[i]
    return chm

def process_laz_file(filepath, min_h, max_h, nmt_arr, nmt_transform):
    """Funkcja do przetwarzania pojedynczego pliku LAZ (zaprojektowana do pracy równoległej)."""
    try:
        with laspy.open(filepath) as laz_file:
            points = laz_file.read().points
            
            if hasattr(points, 'classification'):
                mask = (points.classification == 5)
                if np.sum(mask) == 0: return None
                points = points[mask]

            coords = np.vstack((points.x, points.y)).transpose()
            rows, cols = rasterio.transform.rowcol(nmt_transform, coords[:, 0], coords[:, 1])
            
            valid_idx = (np.array(rows) >= 0) & (np.array(rows) < nmt_arr.shape[0]) & \
                        (np.array(cols) >= 0) & (np.array(cols) < nmt_arr.shape[1])
            
            if np.sum(valid_idx) == 0: return None

            rows, cols = np.array(rows)[valid_idx], np.array(cols)[valid_idx]
            points = points[valid_idx]

            ground_elev = nmt_arr[rows, cols]
            relative_height = points.z - ground_elev
            
            final_mask = (relative_height >= min_h) & (relative_height < max_h)
            
            if np.sum(final_mask) == 0: return None

            return np.vstack((points.x[final_mask], points.y[final_mask], relative_height[final_mask])).T
            
    except Exception as e:
        print(f"BŁĄD w procesie potomnym dla pliku {os.path.basename(filepath)}: {e}")
        return None

def main(config):
    print("\n--- Uruchamianie Skryptu 0: Przetwarzanie Lidar (Wersja Zoptymalizowana) ---")
    paths = config['paths']
    params = config['params']['lidar']
    laz_folder = paths['laz_folder']
    
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        scale_factor = src.res[0] / params['target_res']
        profile.update({
            'width': int(src.width * scale_factor),
            'height': int(src.height * scale_factor),
            'transform': src.transform * src.transform.scale(1/scale_factor, 1/scale_factor),
            'dtype': 'float32', 'nodata': -9999.0
        })
        nmt_arr = src.read(1)
        nmt_transform = src.transform

    laz_files = [os.path.join(laz_folder, f) for f in os.listdir(laz_folder) if f.endswith(('.laz', '.las'))]
    all_veg_points = []
    
    print(f"-> Uruchamianie równoległego przetwarzania dla {len(laz_files)} plików...")
    with ProcessPoolExecutor() as executor:
        futures = [executor.submit(process_laz_file, fp, params['min_tree_height'], params['max_plausible_tree_height'], nmt_arr, nmt_transform) for fp in laz_files]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                all_veg_points.append(result)

    if not all_veg_points:
        print("BŁĄD KRYTYCZNY: Nie znaleziono żadnych punktów roślinności w plikach LAZ.")
        with rasterio.open(paths['output_crowns_raster'], 'w', **profile) as dst:
            dst.write(np.full((profile['height'], profile['width']), profile['nodata'], dtype=np.float32), 1)
        return paths['output_crowns_raster'], gpd.GeoDataFrame()

    print("  -> Łączenie wyników i tworzenie rastra wysokości koron...")
    combined_points = np.vstack(all_veg_points)
    
    # === KLUCZOWA ZMIANA: Oblicz odwróconą transformację TUTAJ ===
    inv_transform = ~profile['transform']
    inv_coeffs = inv_transform.to_gdal() # (c, a, b, f, d, e)
    
    chm_raster = create_chm_from_points(
        combined_points[:, 0], # X
        combined_points[:, 1], # Y
        combined_points[:, 2], # Wysokość względna
        inv_coeffs[1], inv_coeffs[2], inv_coeffs[0], # a, b, c
        inv_coeffs[4], inv_coeffs[5], inv_coeffs[3], # d, e, f
        profile['width'],
        profile['height'],
        profile['nodata']
    )
    
    output_path = paths['output_crowns_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(chm_raster, 1)
    print(f"  -> Raster koron drzew zapisany: {output_path}")

    return output_path, gpd.GeoDataFrame()
