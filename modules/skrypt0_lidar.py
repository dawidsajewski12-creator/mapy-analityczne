# /modules/skrypt0_lidar.py
import os, numpy as np, rasterio, geopandas as gpd, laspy
from rasterio.crs import CRS
from scipy.ndimage import gaussian_filter, maximum_filter, label
from skimage.segmentation import watershed
from shapely.geometry import Point
from numba import njit, prange

# --- FUNKCJA RASTERYZUJĄCA (NUMBA) ---
@njit(parallel=True)
def rasterize_class5_numba(points, min_x, max_y, res, nx, ny):
    """Szybka funkcja do rasteryzacji punktów, znajdująca maksymalną wysokość w komórce."""
    # Inicjalizujemy siatkę wartością niższą niż jakakolwiek możliwa wysokość
    grid_max = np.full((ny, nx), -np.inf, dtype=np.float32)

    for i in prange(points.shape[0]):
        # Oblicz indeks komórki dla punktu
        col = int((points[i, 0] - min_x) / res)
        row = int((max_y - points[i, 1]) / res)

        if 0 <= row < ny and 0 <= col < nx:
            # Atomowa operacja szukania maksimum (bezpieczna dla wielu wątków)
            atomic_max(grid_max, (row, col), points[i, 2])

    return grid_max

@njit
def atomic_max(array, idx, value):
    """Prosta implementacja atomowego maksimum dla Numba."""
    if value > array[idx]:
        array[idx] = value

# --- GŁÓWNA LOGIKA SKRYPTU ---
def main():
    print("Rozpoczynanie tworzenia rastra koron drzew...")
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

    laz_files = [os.path.join(LAZ_FOLDER_PATH, f) for f in os.listdir(LAZ_FOLDER_PATH) if f.endswith('.laz')]
    if not laz_files:
        raise FileNotFoundError(f"Nie znaleziono plików .laz w folderze: {LAZ_FOLDER_PATH}")

    # Krok 1: Określ całkowity zasięg i CRS
    print("-> Skanowanie zasięgu i CRS plików...")
    total_bounds = [np.inf, np.inf, -np.inf, -np.inf] # minx, miny, maxx, maxy
    crs = None
    for f in laz_files:
        with laspy.open(f) as laz_file:
            total_bounds[0] = min(total_bounds[0], laz_file.header.x_min)
            total_bounds[1] = min(total_bounds[1], laz_file.header.y_min)
            total_bounds[2] = max(total_bounds[2], laz_file.header.x_max)
            total_bounds[3] = max(total_bounds[3], laz_file.header.y_max)
            if crs is None:
                try:
                    parsed_crs = laz_file.header.parse_crs()
                    if parsed_crs: crs = parsed_crs
                except Exception: pass

    if crs is None:
        crs = CRS.from_string(DEFAULT_CRS)
        print(f"  -> OSTRZEŻENIE: Nie udało się odczytać CRS. Używam domyślnego: {DEFAULT_CRS}")
    else:
        print(f"  -> Odczytano CRS: {crs.to_string()}")

    # Krok 2: Zdefiniuj siatkę wynikową
    minx, miny, maxx, maxy = total_bounds
    nx = int(np.ceil((maxx - minx) / TARGET_RES))
    ny = int(np.ceil((maxy - miny) / TARGET_RES))

    # Inicjalizujemy finalny raster wartością NODATA
    final_raster = np.full((ny, nx), NODATA_VALUE, dtype=np.float32)

    # Krok 3: Przetwarzaj pliki jeden po drugim
    for f in laz_files:
        print(f"-> Przetwarzanie pliku: {os.path.basename(f)}...")
        laz = laspy.read(f)

        # Filtruj tylko klasę 5
        veg_points = np.stack([laz.x, laz.y, laz.z], axis=1)[laz.classification == 5]

        if len(veg_points) > 0:
            # Rasteryzuj punkty z bieżącego pliku
            raster_tile = rasterize_class5_numba(veg_points, minx, maxy, TARGET_RES, nx, ny)

            # Połącz wynik z finalnym rastrem, biorąc wyższą wartość
            # (na wypadek gdyby korony z różnych plików nachodziły na siebie)
            final_raster = np.maximum(final_raster, raster_tile)

        del laz, veg_points # Zwolnij pamięć

    # Krok 4: Zapisz finalny raster GeoTIFF
    print(f"-> Zapisywanie wyniku do pliku: {OUTPUT_RASTER_PATH}")
    transform = rasterio.transform.from_origin(minx, maxy, TARGET_RES, TARGET_RES)
    profile = {
        'driver': 'GTiff', 'height': ny, 'width': nx, 'count': 1,
        'dtype': 'float32', 'crs': crs, 'transform': transform,
        'nodata': NODATA_VALUE, 'compress': 'lzw'
    }

    with rasterio.open(OUTPUT_RASTER_PATH, 'w', **profile) as dst:
        dst.write(final_raster, 1)

    print("\n✅ Proces zakończony pomyślnie!")

