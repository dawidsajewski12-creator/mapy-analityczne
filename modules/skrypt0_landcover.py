# -*- coding: utf-8 -*-
"""
SKRYPT TESTOWY: Tworzenie i Wizualizacja Land Cover (v1.1)

Opis:
- Naprawiono błąd `RasterioIOError` poprzez dodanie mechanizmu, który
  automatycznie tworzy foldery wyjściowe, jeśli nie istnieją.
"""
import os
import zipfile
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import reproject, Resampling
import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch
from google.colab import drive

# --- 1. KONFIGURACJA ---
print("Montowanie Dysku Google...")
drive.mount('/content/drive', force_remount=True)

# Ścieżki wejściowe
BASE_PATH = '/content/drive/MyDrive/ProjektGIS'
NMT_PATH = os.path.join(BASE_PATH, "dane/nmt.tif")
BDOT_ZIP_PATH = os.path.join(BASE_PATH, "dane/bdot10k.zip")

# Ścieżki wyjściowe
OUTPUT_FOLDER = os.path.join(BASE_PATH, "wyniki_krok1_landcover")
BDOT_EXTRACT_PATH = os.path.join(OUTPUT_FOLDER, "bdot_extracted")
LANDCOVER_RASTER_PATH = os.path.join(OUTPUT_FOLDER, "landcover.tif")
FINAL_IMAGE_PATH = os.path.join(OUTPUT_FOLDER, "mapa_landcover.png")

# Parametry
TARGET_RES = 2.0
TARGET_LANDCOVER_FILES = ["PTTR_A", "PTRK_A", "PTPL_A", "PTUT_A", "PTKM_A", "PTZB_A", "PTLZ_A", "PTGN_A", "PTNZ_A", "PTWP_A", "PTWZ_A"]
CLASSIFICATION_MAP = {
    "OT_PTRK_A": (1, "Nawierzchnie utwardzone"), "OT_PTPL_A": (1, "Nawierzchnie utwardzone"),
    "OT_PTUT_A": (1, "Nawierzchnie utwardzone"), "OT_PTKM_A": (1, "Nawierzchnie utwardzone"),
    "OT_PTZB_A": (2, "Budynki"), "OT_PTLZ_A": (3, "Drzewa / Lasy"),
    "OT_PTTR_A": (5, "Trawa / Tereny zielone"), "OT_PTGN_A": (5, "Trawa / Tereny zielone"),
    "OT_PTNZ_A": (6, "Gleba / Nieużytki"), "OT_PTWP_A": (7, "Woda"), "OT_PTWZ_A": (7, "Woda"),
}
LEGEND_MAP = {
    1: {'name': 'Nawierzchnie utwardzone', 'color': '#A9A9A9'}, 2: {'name': 'Budynki', 'color': '#DC143C'},
    3: {'name': 'Drzewa / Lasy', 'color': '#228B22'}, 5: {'name': 'Trawa / Tereny zielone', 'color': '#7CFC00'},
    6: {'name': 'Gleba / Nieużytki', 'color': '#D2B48C'}, 7: {'name': 'Woda', 'color': '#1E90FF'}
}

# --- Funkcje pomocnicze ---
def find_and_extract_bdot_layers(zip_path, target_filenames, extract_folder):
    if not os.path.exists(extract_folder): os.makedirs(extract_folder)
    extracted_paths = []
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            if not member.is_dir() and any(target in os.path.basename(member.filename) for target in target_filenames):
                source = zip_ref.open(member); target_path = os.path.join(extract_folder, os.path.basename(member.filename))
                with open(target_path, "wb") as f: f.write(source.read())
                extracted_paths.append(target_path)
    return extracted_paths

# --- GŁÓWNA LOGIKA ---
def main():
    print("Rozpoczynanie analizy: Tworzenie mapy pokrycia terenu...")
    
    # ### POPRAWKA: Upewniamy się, że folder wyjściowy istnieje ###
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)
        print(f"Utworzono folder wyjściowy: {OUTPUT_FOLDER}")

    # Krok 1: Przygotowanie siatki
    with rasterio.open(NMT_PATH) as src_nmt:
        profile = src_nmt.profile; scale_factor = profile['transform'].a / TARGET_RES
        ny = int(src_nmt.height * scale_factor); nx = int(src_nmt.width * scale_factor)
        transform = profile['transform'] * profile['transform'].scale(1/scale_factor, 1/scale_factor)
        profile.update(height=ny, width=nx, transform=transform, dtype='uint8', nodata=0, compress='lzw')

    # Krok 2: Stworzenie rastra landcover.tif
    print("-> Przetwarzanie danych BDOT...")
    landcover_raster = np.zeros((ny, nx), dtype=np.uint8)
    landcover_paths = find_and_extract_bdot_layers(BDOT_ZIP_PATH, TARGET_LANDCOVER_FILES, BDOT_EXTRACT_PATH)
    if landcover_paths:
        for fpath in landcover_paths:
            code = next((key for key in CLASSIFICATION_MAP if key in os.path.basename(fpath)), None)
            if code:
                class_id, _ = CLASSIFICATION_MAP[code]; gdf = gpd.read_file(fpath)
                if gdf.crs != profile['crs']: gdf = gdf.to_crs(profile['crs'])
                geometries = [(geom, class_id) for geom in gdf.geometry]
                class_mask = rasterize(shapes=geometries, out_shape=(ny, nx), transform=transform, fill=0, dtype=np.uint8)
                landcover_raster[class_mask > 0] = class_mask[class_mask > 0]
    
    with rasterio.open(LANDCOVER_RASTER_PATH, 'w', **profile) as dst:
        dst.write(landcover_raster, 1)
    print(f"-> Zapisano raster: {LANDCOVER_RASTER_PATH}")
    
    # Krok 3: Wygenerowanie obrazu .png
    print("-> Generowanie obrazu mapy do wyświetlenia na stronie...")
    landcover_masked = np.ma.masked_where(landcover_raster == 0, landcover_raster)
    class_ids = sorted(LEGEND_MAP.keys()); colors = [LEGEND_MAP[cid]['color'] for cid in class_ids]
    cmap = mcolors.ListedColormap(colors)
    bounds = [cid - 0.5 for cid in class_ids] + [class_ids[-1] + 0.5]
    norm = mcolors.BoundaryNorm(bounds, cmap.N)
    fig, ax = plt.subplots(1, 1, figsize=(12, 12))
    ax.imshow(landcover_masked, cmap=cmap, norm=norm)
    ax.set_title("Pokrycie terenu (Land Cover)", fontsize=16); ax.set_axis_off()
    legend_elements = [Patch(facecolor=info['color'], edgecolor='black', label=f"{info['name']}") for _, info in LEGEND_MAP.items()]
    ax.legend(handles=legend_elements, bbox_to_anchor=(1.05, 1), loc='upper left', borderaxespad=0.)
    plt.savefig(FINAL_IMAGE_PATH, dpi=150, bbox_inches='tight', pad_inches=0.1, transparent=True)
    plt.close(fig)
    print(f"-> Zapisano obraz mapy: {FINAL_IMAGE_PATH}")
    print("\n✅ Analiza testowa zakończona pomyślnie!")

if __name__ == '__main__':
    main()
