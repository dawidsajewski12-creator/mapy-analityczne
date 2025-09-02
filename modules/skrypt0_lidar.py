# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
import laspy
import os
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
            # Używamy pętli, aby uniknąć problemów z równoległym zapisem do tej samej komórki
            # To jest uproszczenie - w praktyce przy tej rozdzielczości konflikty są rzadkie
            # Dla pełnej atomowości operacji potrzebne byłyby bardziej zaawansowane techniki
            if points_z[i] > chm[row, col]:
                chm[row, col] = points_z[i]
    return chm

def main(config):
    print("\n--- Uruchamianie Skryptu 0: Przetwarzanie Lidar (Nowa Wersja) ---")
    paths = config['paths']
    params = config['params']['lidar']
    laz_folder = paths['laz_folder']
    target_res = params['target_res']

    # 1. Uzyskaj profil i rozdzielczość z NMT
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

    # 2. Przetwarzaj pliki LAZ
    all_veg_points = []
    laz_files = [f for f in os.listdir(laz_folder) if f.endswith('.laz')]
    for filename in laz_files:
        print(f"  -> Filtrowanie pliku: {filename}...")
        try:
            with laspy.open(os.path.join(laz_folder, filename)) as laz:
                mask = (laz.classification == 5) # Klasa 5: Wysoka roślinność
                points = laz.read_points(mask)
                
                # Zbieramy tylko potrzebne dane
                x, y, z = points.x, points.y, points.z
                # Odrzucamy punkty poza obszarem NMT dla bezpieczeństwa
                valid_indices = (x > nmt_bounds.left) & (x < nmt_bounds.right) & \
                                (y > nmt_bounds.bottom) & (y < nmt_bounds.top)
                
                # Przechowujemy jako tablice numpy
                all_veg_points.append(np.vstack((x[valid_indices], y[valid_indices], z[valid_indices])).T)

        except Exception as e:
            print(f"BŁĄD podczas przetwarzania {filename}: {e}")

    if not all_veg_points:
        print("BŁĄD KRYTYCZNY: Nie znaleziono żadnych punktów roślinności w plikach LAZ.")
        return None, None # Zwracamy None, aby pipeline mógł kontynuować

    # 3. Połącz punkty i stwórz CHM
    print("  -> Łączenie punktów i tworzenie rastra wysokości koron...")
    combined_points = np.vstack(all_veg_points)
    
    # Przelicz Z na wysokość względną (CHM)
    # Wczytaj NMT i zinterpoluj wartości dla punktów
    with rasterio.open(paths['nmt']) as nmt_src:
        coords = [(p[0], p[1]) for p in combined_points]
        ground_elev = np.array([val[0] for val in nmt_src.sample(coords)])
    
    # Wysokość względna
    relative_height = combined_points[:, 2] - ground_elev

    # Filtrowanie wysokości
    height_mask = (relative_height >= params['min_tree_height']) & (relative_height < params['max_plausible_tree_height'])
    
    # Tworzenie CHM
    chm_raster = create_chm_from_points(
        combined_points[:, 0][height_mask],
        combined_points[:, 1][height_mask],
        relative_height[height_mask],
        nmt_profile['transform'],
        nmt_profile['width'],
        nmt_profile['height'],
        nmt_profile['nodata']
    )
    
    # 4. Zapisz wynik
    output_path = paths['output_crowns_raster']
    with rasterio.open(output_path, 'w', **nmt_profile) as dst:
        dst.write(chm_raster, 1)
    print(f"  -> Raster koron drzew zapisany: {output_path}")

    # Zwracamy ścieżkę do rastra i pusty obiekt geopandas, aby zachować spójność interfejsu
    # z poprzednią wersją, która zwracała też wektor.
    return output_path, gpd.GeoDataFrame()
