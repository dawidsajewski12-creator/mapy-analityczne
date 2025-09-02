# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
import laspy
import os
import geopandas as gpd
from numba import njit, prange

@njit(parallel=True)
def create_chm_from_points(points_x, points_y, points_z, transform, width, height, nodata_value):
    """Szybkie tworzenie rastra CHM z punktów przy użyciu Numba."""
    chm = np.full((height, width), nodata_value, dtype=np.float32)
    inv_transform = ~transform

    for i in prange(len(points_x)):
        px, py = inv_transform * (points_x[i], points_y[i])
        col, row = int(px), int(py)

        if 0 <= row < height and 0 <= col < width:
            # Prosta operacja max - w praktyce wystarczająco dobra przy dużej liczbie punktów
            if points_z[i] > chm[row, col]:
                chm[row, col] = points_z[i]
    return chm

def main(config):
    print("\n--- Uruchamianie Skryptu 0: Przetwarzanie Lidar (Nowa Wersja) ---")
    paths = config['paths']
    params = config['params']['lidar']
    laz_folder = paths['laz_folder']
    target_res = params['target_res']

    # 1. Uzyskaj profil i rozdzielczość z NMT do stworzenia siatki wynikowej
    with rasterio.open(paths['nmt']) as src:
        nmt_profile = src.profile.copy()
        nmt_bounds = src.bounds
        scale_factor = src.res[0] / target_res
        new_width = int(src.width * scale_factor)
        new_height = int(src.height * scale_factor)
        transform = src.transform * src.transform.scale(1/scale_factor, 1/scale_factor)
        nmt_profile.update({
            'width': new_width,
            'height': new_height,
            'transform': transform,
            'dtype': 'float32',
            'nodata': -9999.0
        })

    # 2. Wczytaj NMT do pamięci raz, aby unikać wielokrotnego otwierania pliku
    with rasterio.open(paths['nmt']) as nmt_src:
        nmt_for_sampling = nmt_src.read(1)
        nmt_transform_for_sampling = nmt_src.transform

    # 3. Przetwarzaj pliki LAZ
    all_veg_points = []
    laz_files = [f for f in os.listdir(laz_folder) if f.endswith(('.laz', '.las'))]
    for filename in laz_files:
        print(f"  -> Przetwarzanie pliku: {filename}...")
        try:
            full_path = os.path.join(laz_folder, filename)
            with laspy.open(full_path) as laz_file:
                
                # Wczytaj wszystkie punkty
                points = laz_file.read().points
                
                # === NOWA LOGIKA: SPRAWDŹ CZY KLASYFIKACJA ISTNIEJE ===
                if hasattr(points, 'classification'):
                    # METODA 1: Filtrowanie po klasie (preferowane)
                    mask = (points.classification == 5) # Klasa 5: Wysoka roślinność
                    veg_indices = np.where(mask)[0]
                else:
                    # METODA 2 (FALLBACK): Filtrowanie po wysokości względnej
                    print(f"    -> OSTRZEŻENIE: Brak pola 'classification' w {filename}. Używam filtrowania po wysokości.")
                    # Oblicz wysokość względną dla wszystkich punktów
                    coords = np.vstack((points.x, points.y)).transpose()
                    row, col = rasterio.transform.rowcol(nmt_transform_for_sampling, coords[:, 0], coords[:, 1])
                    # Ogranicz indeksy do tych wewnątrz rastra NMT
                    valid_idx = (np.array(row) >= 0) & (np.array(row) < nmt_for_sampling.shape[0]) & \
                                (np.array(col) >= 0) & (np.array(col) < nmt_for_sampling.shape[1])
                    
                    ground_elev = nmt_for_sampling[np.array(row)[valid_idx], np.array(col)[valid_idx]]
                    relative_height = points.z[valid_idx] - ground_elev
                    
                    # Zastosuj maskę wysokościową
                    height_mask = (relative_height >= params['min_tree_height']) & (relative_height < params['max_plausible_tree_height'])
                    
                    # Potrzebujemy oryginalnych indeksów, więc musimy je zmapować z powrotem
                    original_indices = np.where(valid_idx)[0]
                    veg_indices = original_indices[height_mask]
                
                if len(veg_indices) > 0:
                    filtered_points = points[veg_indices]
                    all_veg_points.append(np.vstack((filtered_points.x, filtered_points.y, filtered_points.z)).T)

        except Exception as e:
            print(f"BŁĄD podczas przetwarzania {filename}: {e}")

    if not all_veg_points:
        print("BŁĄD KRYTYCZNY: Nie znaleziono żadnych punktów roślinności w plikach LAZ.")
        return None, None 

    # 4. Połącz punkty i oblicz ostateczną wysokość względną
    print("  -> Łączenie punktów i tworzenie rastra wysokości koron...")
    combined_points = np.vstack(all_veg_points)
    
    # Próbkuj NMT dla połączonych punktów
    coords_for_sampling = [(p[0], p[1]) for p in combined_points]
    ground_elevations = np.array([val[0] for val in rasterio.open(paths['nmt']).sample(coords_for_sampling)])
    
    # Ostateczna wysokość względna (CHM)
    relative_heights = combined_points[:, 2] - ground_elevations

    # Ostateczne filtrowanie na podstawie finalnej wysokości
    final_mask = (relative_heights >= params['min_tree_height']) & (relative_heights < params['max_plausible_tree_height'])

    # 5. Stwórz raster CHM
    chm_raster = create_chm_from_points(
        combined_points[:, 0][final_mask],
        combined_points[:, 1][final_mask],
        relative_heights[final_mask],
        nmt_profile['transform'],
        nmt_profile['width'],
        nmt_profile['height'],
        nmt_profile['nodata']
    )
    
    # 6. Zapisz wynik
    output_path = paths['output_crowns_raster']
    with rasterio.open(output_path, 'w', **nmt_profile) as dst:
        dst.write(chm_raster, 1)
    print(f"  -> Raster koron drzew zapisany: {output_path}")

    return output_path, gpd.GeoDataFrame()
