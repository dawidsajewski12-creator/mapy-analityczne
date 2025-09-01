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
    # Usuń tło (klasa 0) z obliczeń procentowych
    total_pixels = np.sum(counts[class_ids != 0])
    
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
    print("\n--- Uruchamianie Skryptu 0: Tworzenie Pokrycia Terenu ---")
    paths = config['paths']
    params = config['params']
    
    with rasterio.open(paths['nmt']) as src_nmt:
        profile = src_nmt.profile
        scale_factor = profile['transform'].a / params['target_res']
        ny, nx = int(src_nmt.height * scale_factor), int(src_nmt.width * scale_factor)
        transform = profile['transform'] * profile['transform'].scale(1/scale_factor, 1/scale_factor)
        profile.update(height=ny, width=nx, transform=transform, dtype='uint8', nodata=0, compress='lzw')

    print("-> Przetwarzanie danych BDOT...")
    landcover_raster = np.zeros((ny, nx), dtype=np.uint8)
    landcover_paths = find_and_extract_bdot_layers(paths['bdot_zip'], params['target_landcover_files'], paths['bdot_extract'])
    if landcover_paths:
        for fpath in landcover_paths:
            code = next((key for key in params['classification_map'] if key in os.path.basename(fpath)), None)
            if code:
                class_id, _ = params['classification_map'][code]
                gdf = gpd.read_file(fpath)
                if gdf.crs != profile['crs']: gdf = gdf.to_crs(profile['crs'])
                geometries = [(geom, class_id) for geom in gdf.geometry]
                class_mask = rasterize(shapes=geometries, out_shape=(ny, nx), transform=transform, fill=0, dtype=np.uint8)
                landcover_raster[class_mask > 0] = class_mask[class_mask > 0]
    
    output_path = paths['output_landcover_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(landcover_raster, 1)
    print(f"-> Zapisano raster pokrycia terenu: {output_path}")

    # NOWOŚĆ: Automatyczne obliczanie i zapis statystyk
    calculate_and_save_stats(output_path, paths['output_landcover_stats'], params['legend_map'])

    print("--- Skrypt 0 zakończony pomyślnie ---")
    return output_path
