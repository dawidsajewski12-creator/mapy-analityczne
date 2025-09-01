# -*- coding: utf-8 -*-
"""
SKRYPT NR 0 (v6.2): RASTER KORON + INWENTARYZACJA PUNKTOWA

Opis:
Wersja, która łączy w sobie dwie funkcjonalności:
1. Tworzy raster wysokości bezwzględnej koron drzew na podstawie klasy 5 Lidar.
2. Dodaje nowy, zintegrowany moduł, który na podstawie tego rastra oraz NMT
   przeprowadza segmentację i tworzy wektorową warstwę punktową z atrybutami drzew.
"""
import os
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import reproject, Resampling
import laspy
from numba import njit, prange
import geopandas as gpd
from shapely.geometry import Point
from scipy.ndimage import gaussian_filter, maximum_filter, label
from skimage.segmentation import watershed

# --- 1. KONFIGURACJA ---
print("Montowanie Dysku Google...")
drive.mount('/content/drive', force_remount=True)

# ŚCIEŻKI WEJŚCIOWE
LAZ_FOLDER_PATH = "/content/drive/MyDrive/ProjektGIS/dane/laz/"
NMT_PATH = "/content/drive/MyDrive/ProjektGIS/dane/nmt.tif" # NMT jest teraz wymagany
DEFAULT_CRS = "EPSG:2180"

# PARAMETRY
TARGET_RES = 1.0
NODATA_VALUE = -9999.0
MIN_TREE_HEIGHT = 4.0       # Minimalna wysokość względna, by uznać obiekt za drzewo
TREETOP_FILTER_SIZE = 7     # Rozmiar okna do szukania wierzchołków
MIN_CROWN_AREA_M2 = 10      # Minimalna powierzchnia korony w m^2
CROWN_BASE_FACTOR = 0.25    # Współczynnik do obliczenia podstawy korony (25%)

# ŚCIEŻKI WYJŚCIOWE
OUTPUT_FOLDER = "/content/lidar_class5_raster_and_points_v2"
# Twój raster z wysokościami bezwzględnymi
OUTPUT_CROWNS_SURFACE_RASTER = os.path.join(OUTPUT_FOLDER, "raster_powierzchni_koron_bezwzgledny.tif")
# Nowe, dodatkowe wyniki
OUTPUT_CROWNS_SEGMENTED_RASTER = os.path.join(OUTPUT_FOLDER, "korony_drzew_segmentacja.tif")
OUTPUT_CHM_RASTER = os.path.join(OUTPUT_FOLDER, "chm_wzgledny.tif")
OUTPUT_TREES_VECTOR = os.path.join(OUTPUT_FOLDER, "inwentaryzacja_drzew_punktowa.gpkg")

# --- Funkcje Numba (bez zmian) ---
@njit(parallel=True)
def rasterize_class5_numba(points, min_x, max_y, res, nx, ny):
    grid_max = np.full((ny, nx), -np.inf, dtype=np.float32)
    for i in prange(points.shape[0]):
        col = int((points[i, 0] - min_x) / res)
        row = int((max_y - points[i, 1]) / res)
        if 0 <= row < ny and 0 <= col < nx:
            atomic_max(grid_max, (row, col), points[i, 2])
    return grid_max

@njit
def atomic_max(array, idx, value):
    if value > array[idx]:
        array[idx] = value

# --- GŁÓWNA LOGIKA SKRYPTU ---
def main():
    print("Rozpoczynanie tworzenia rastra koron i inwentaryzacji...")
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

    laz_files = [os.path.join(LAZ_FOLDER_PATH, f) for f in os.listdir(LAZ_FOLDER_PATH) if f.endswith('.laz')]
    if not laz_files: raise FileNotFoundError(f"Nie znaleziono plików .laz w folderze: {LAZ_FOLDER_PATH}")

    # Krok 1: Określ całkowity zasięg i CRS
    print("-> Skanowanie zasięgu i CRS plików...")
    total_bounds = [np.inf, np.inf, -np.inf, -np.inf]; crs = None
    for f in laz_files:
        with laspy.open(f) as laz_file:
            total_bounds[0] = min(total_bounds[0], laz_file.header.x_min); total_bounds[1] = min(total_bounds[1], laz_file.header.y_min)
            total_bounds[2] = max(total_bounds[2], laz_file.header.x_max); total_bounds[3] = max(total_bounds[3], laz_file.header.y_max)
            if crs is None:
                try:
                    parsed_crs = laz_file.header.parse_crs()
                    if parsed_crs: crs = parsed_crs
                except Exception: pass
    if crs is None:
        crs = CRS.from_string(DEFAULT_CRS)
        print(f"  -> OSTRZEŻENIE: Używam domyślnego CRS: {DEFAULT_CRS}")
    else:
        print(f"  -> Odczytano CRS: {crs.to_string()}")

    # Krok 2: Zdefiniuj siatkę wynikową
    minx, miny, maxx, maxy = total_bounds
    nx = int(np.ceil((maxx - minx) / TARGET_RES)); ny = int(np.ceil((maxy - miny) / TARGET_RES))
    
    # Krok 3: Stwórz raster powierzchni koron (vNMPT)
    vnmpt = np.full((ny, nx), NODATA_VALUE, dtype=np.float32)
    for f in laz_files:
        print(f"-> Przetwarzanie pliku: {os.path.basename(f)}...")
        laz = laspy.read(f)
        veg_points = np.stack([laz.x, laz.y, laz.z], axis=1)[laz.classification == 5]
        if len(veg_points) > 0:
            raster_tile = rasterize_class5_numba(veg_points, minx, maxy, TARGET_RES, nx, ny)
            raster_tile[np.isinf(raster_tile)] = NODATA_VALUE
            vnmpt = np.maximum(vnmpt, raster_tile)
        del laz, veg_points
        
    transform = rasterio.transform.from_origin(minx, maxy, TARGET_RES, TARGET_RES)
    profile = {'driver': 'GTiff', 'height': ny, 'width': nx, 'count': 1, 'dtype': 'float32', 'crs': crs, 'transform': transform, 'nodata': NODATA_VALUE, 'compress': 'lzw'}
    with rasterio.open(OUTPUT_CROWNS_SURFACE_RASTER, 'w', **profile) as dst:
        dst.write(vnmpt, 1)
    print(f"-> Zapisano raster powierzchni koron do: {OUTPUT_CROWNS_SURFACE_RASTER}")

    # === NOWY MODUŁ: TWORZENIE WARSTWY PUNKTOWEJ ===
    print("\n-> Rozpoczynanie segmentacji i tworzenia inwentaryzacji punktowej...")
    # Krok 4: Wczytaj NMT i oblicz CHM
    with rasterio.open(NMT_PATH) as src_nmt:
        nmt = np.empty((ny, nx), dtype=np.float32)
        reproject(source=rasterio.band(src_nmt, 1), destination=nmt, src_transform=src_nmt.transform, src_crs=src_nmt.crs, dst_transform=transform, dst_crs=crs, resampling=Resampling.bilinear)
    
    vnmpt[vnmpt == NODATA_VALUE] = np.nan
    vnmpt_filled = rasterio.fill.fillnodata(vnmpt, mask=~np.isnan(vnmpt))
    chm = vnmpt_filled - nmt
    chm[chm < 0] = 0
    with rasterio.open(OUTPUT_CHM_RASTER, 'w', **profile) as dst:
        dst.write(chm, 1)

    # Krok 5: Segmentacja
    chm_smoothed = gaussian_filter(chm, sigma=0.5)
    maxima = maximum_filter(chm_smoothed, size=TREETOP_FILTER_SIZE)
    treetops_mask = (chm_smoothed == maxima) & (chm > MIN_TREE_HEIGHT)
    markers, num_features = label(treetops_mask)
    print(f"  -> Wykryto {num_features} potencjalnych drzew.")
    segmentation_mask = chm > (MIN_TREE_HEIGHT / 2)
    labels = watershed(-chm_smoothed, markers, mask=segmentation_mask)
    
    unique_labels, counts = np.unique(labels, return_counts=True)
    small_labels = unique_labels[counts * (TARGET_RES**2) < MIN_CROWN_AREA_M2]
    for small_label in small_labels: labels[labels == small_label] = 0
    
    profile.update(dtype=rasterio.int32, nodata=0)
    with rasterio.open(OUTPUT_CROWNS_SEGMENTED_RASTER, 'w', **profile) as dst:
        dst.write(labels.astype(rasterio.int32), 1)
    print(f"  -> Zapisano raster segmentacji koron do: {OUTPUT_CROWNS_SEGMENTED_RASTER}")

    # Krok 6: Inwentaryzacja punktowa z atrybutami
    tree_data = []; unique_final_labels = np.unique(labels)[1:]
    for tree_id in unique_final_labels:
        current_crown_mask = (labels == tree_id)
        chm_crown = np.where(current_crown_mask, chm, -np.inf)
        flat_index = np.argmax(chm_crown)
        treetop_row, treetop_col = np.unravel_index(flat_index, chm_crown.shape)
        
        height_relative = chm[treetop_row, treetop_col]
        height_absolute = nmt[treetop_row, treetop_col] + height_relative
        crown_base = height_absolute - (height_relative * (1 - CROWN_BASE_FACTOR))
        treetop_x, treetop_y = transform * (treetop_col + 0.5, treetop_row + 0.5)
        crown_area = np.sum(current_crown_mask) * (TARGET_RES**2)
        
        tree_data.append({'geometry': Point(treetop_x, treetop_y), 'tree_id': int(tree_id),
                          'wysokosc_wzgledna_m': float(height_relative), 'wysokosc_npm_m': float(height_absolute),
                          'podstawa_korony_m': float(crown_base), 'pow_korony_m2': float(crown_area)})
    if tree_data:
        gdf = gpd.GeoDataFrame(tree_data, crs=crs)
        gdf.to_file(OUTPUT_TREES_VECTOR, driver='GPKG')
        print(f"  -> Inwentaryzacja zakończona. Zapisano {len(gdf)} drzew do: {OUTPUT_TREES_VECTOR}")

    print("\n✅ Proces zakończony pomyślnie!")

if __name__ == '__main__':
    main()
