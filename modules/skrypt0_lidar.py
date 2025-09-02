# -*- coding: utf-8 -*-
import numpy as np
import rasterio
import laspy
import os
import geopandas as gpd
from numba import njit, prange
from concurrent.futures import ProcessPoolExecutor, as_completed

# Ta funkcja pozostaje bez zmian - jest już bardzo szybka
@njit(parallel=True)
def create_chm_from_points(points_x, points_y, points_z, transform, width, height, nodata_value):
    """Szybkie tworzenie rastra CHM z punktów przy użyciu Numba."""
    chm = np.full((height, width), nodata_value, dtype=np.float32)
    inv_transform = ~transform
    for i in prange(len(points_x)):
        px, py = inv_transform * (points_x[i], points_y[i])
        col, row = int(px), int(py)
        if 0 <= row < height and 0 <= col < width:
            # Ta operacja nie jest w pełni bezpieczna w trybie równoległym,
            # ale przy gęstej chmurze i małej rozdzielczości piksela ryzyko nadpisania jest akceptowalne
            # dla uzyskania maksymalnej prędkości.
            if points_z[i] > chm[row, col]:
                chm[row, col] = points_z[i]
    return chm

def process_laz_file(filepath, min_h, max_h, nmt_arr, nmt_transform):
    """Funkcja do przetwarzania pojedynczego pliku LAZ (zaprojektowana do pracy równoległej)."""
    try:
        with laspy.open(filepath) as laz_file:
            points = laz_file.read().points
            
            # Jeśli plik ma klasyfikację, użyj jej do wstępnego, szybkiego odfiltrowania 99% niepotrzebnych punktów
            if hasattr(points, 'classification'):
                mask = (points.classification == 5) # Klasa 5: Wysoka roślinność
                if np.sum(mask) == 0: return None # Zwróć None, jeśli nie ma punktów tej klasy
                points = points[mask]

            # Oblicz wysokość względną dla pozostałych punktów
            coords = np.vstack((points.x, points.y)).transpose()
            rows, cols = rasterio.transform.rowcol(nmt_transform, coords[:, 0], coords[:, 1])
            
            # Usuń punkty, które wypadły poza NMT
            valid_idx = (np.array(rows) >= 0) & (np.array(rows) < nmt_arr.shape[0]) & \
                        (np.array(cols) >= 0) & (np.array(cols) < nmt_arr.shape[1])
            
            if np.sum(valid_idx) == 0: return None

            rows, cols = np.array(rows)[valid_idx], np.array(cols)[valid_idx]
            points = points[valid_idx]

            ground_elev = nmt_arr[rows, cols]
            relative_height = points.z - ground_elev
            
            # Ostateczne filtrowanie po wysokości
            final_mask = (relative_height >= min_h) & (relative_height < max_h)
            
            if np.sum(final_mask) == 0: return None

            # Zwróć tylko potrzebne dane: X, Y i wysokość względną
            return np.vstack((points.x[final_mask], points.y[final_mask], relative_height[final_mask])).T
            
    except Exception as e:
        print(f"BŁĄD w procesie potomnym dla pliku {os.path.basename(filepath)}: {e}")
        return None

def main(config):
    print("\n--- Uruchamianie Skryptu 0: Przetwarzanie Lidar (Wersja Zoptymalizowana) ---")
    paths = config['paths']
    params = config['params']['lidar']
    laz_folder = paths['laz_folder']
    
    # 1. Przygotuj siatkę wynikową i wczytaj NMT do pamięci
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

    # 2. Równoległe przetwarzanie plików LAZ
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
        print("BŁĄD KRYTYCZNY: Nie znaleziono żadnych punktów roślinności w plikach LAZ po przetworzeniu.")
        # Zapisz pusty raster, aby uniknąć błędów w dalszej części pipeline'u
        with rasterio.open(paths['output_crowns_raster'], 'w', **profile) as dst:
            dst.write(np.full((profile['height'], profile['width']), profile['nodata'], dtype=np.float32), 1)
        return paths['output_crowns_raster'], gpd.GeoDataFrame()

    # 3. Połącz wyniki i stwórz finalny raster CHM
    print("  -> Łączenie wyników i tworzenie rastra wysokości koron...")
    combined_points = np.vstack(all_veg_points)
    
    chm_raster = create_chm_from_points(
        combined_points[:, 0], # X
        combined_points[:, 1], # Y
        combined_points[:, 2], # Już jest to wysokość względna
        profile['transform'],
        profile['width'],
        profile['height'],
        profile['nodata']
    )
    
    # 4. Zapisz wynik
    output_path = paths['output_crowns_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(chm_raster, 1)
    print(f"  -> Raster koron drzew zapisany: {output_path}")

    return output_path, gpd.GeoDataFrame()
