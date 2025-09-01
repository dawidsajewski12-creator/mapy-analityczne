# /modules/skrypt0_landcover.py
import os
import zipfile
import json
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import reproject, Resampling
import geopandas as gpd

def find_and_extract_bdot_layers(zip_path, target_filenames, extract_folder):
    """Przeszukuje archiwum ZIP i ekstraktuje warstwy."""
    if not os.path.exists(extract_folder): os.makedirs(extract_folder)
    extracted_paths = []
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            if not member.is_dir() and any(target in os.path.basename(member.filename) for target in target_filenames):
                source = zip_ref.open(member)
                target_path = os.path.join(extract_folder, os.path.basename(member.filename))
                with open(target_path, "wb") as f: f.write(source.read())
                extracted_paths.append(target_path)
    return extracted_paths

def calculate_and_save_stats(raster_path, stats_path, legend_map):
    """Oblicza statystyki pokrycia terenu i zapisuje je do pliku JSON."""
    print(f"-> Obliczanie statystyk z: {os.path.basename(raster_path)}")
    with rasterio.open(raster_path) as src:
        data = src.read(1)
    
    class_ids, counts = np.unique(data, return_counts=True)
    total_pixels = np.sum(counts[class_ids != 0])
    if total_pixels == 0: total_pixels = 1 # Uniknięcie dzielenia przez zero

    stats_data = {}
    for class_id, count in zip(class_ids, counts):
        if class_id == 0 or class_id not in legend_map:
            continue
        
        percentage = (count / total_pixels) * 100
        class_name = legend_map[class_id]['name']
        stats_data[class_name] = round(percentage, 2)
        
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(stats_data, f, ensure_ascii=False, indent=4)
    print(f"-> Zapisano statystyki do: {stats_path}")

def run_landcover_analysis(config):
    """Główna funkcja modułu, tworzy raster landcover.tif i statystyki."""
    print("\n--- Uruchamianie Skryptu 0: Tworzenie Pokrycia Terenu ---")
    paths = config['paths']
    params = config['params']

    # Krok 1: Przygotowanie siatki na podstawie NMT
    with rasterio.open(paths['nmt']) as src_nmt:
        base_profile = src_nmt.profile.copy() # Używamy kopii, aby nie modyfikować oryginału
        scale_factor = base_profile['transform'].a / params['target_res']
        ny = int(src_nmt.height * scale_factor)
        nx = int(src_nmt.width * scale_factor)
        transform = base_profile['transform'] * base_profile['transform'].scale(1/scale_factor, 1/scale_factor)

    # Krok 2: Stworzenie rastra landcover.tif
    print("-> Przetwarzanie danych BDOT...")
    landcover_raster = np.zeros((ny, nx), dtype=np.uint8)
    landcover_paths = find_and_extract_bdot_layers(paths['bdot_zip'], params['target_landcover_files'], paths['bdot_extract'])
    
    if landcover_paths:
        for fpath in landcover_paths:
            code = next((key for key in params['classification_map'] if key in os.path.basename(fpath)), None)
            if code:
                class_id, _ = params['classification_map'][code]
                gdf = gpd.read_file(fpath)
                if gdf.crs != base_profile['crs']: gdf = gdf.to_crs(base_profile['crs'])
                geometries = [(geom, class_id) for geom in gdf.geometry]
                class_mask = rasterize(shapes=geometries, out_shape=(ny, nx), transform=transform, fill=0, dtype=np.uint8)
                landcover_raster[class_mask > 0] = class_mask[class_mask > 0]
    
    # Krok 3: Zapis rastra z poprawnym profilem
    output_path = paths['output_landcover_raster']
    # Przygotuj profil specjalnie dla tego rastra
    out_profile = base_profile.copy()
    out_profile.update(height=ny, width=nx, transform=transform, dtype='uint8', nodata=0, compress='lzw')
    
    with rasterio.open(output_path, 'w', **out_profile) as dst:
        dst.write(landcover_raster, 1)
    print(f"-> Zapisano raster pokrycia terenu: {output_path}")

    # Krok 4: Automatyczne obliczanie i zapis statystyk
    calculate_and_save_stats(output_path, paths['output_landcover_stats'], params['legend_map'])

    print("--- Skrypt 0 zakończony pomyślnie ---")
    return output_path
