# /modules/skrypt0_landcover.py
import os, zipfile, numpy as np, rasterio
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

def main(config):
    print("\n--- Uruchamianie Skryptu 0: Tworzenie Pokrycia Terenu ---")
    paths = config['paths']; params = config['params']['landcover']
    with rasterio.open(paths['nmt']) as src_nmt:
        base_profile = src_nmt.profile.copy()
        scale_factor = base_profile['transform'].a / params['target_res']
        ny, nx = int(src_nmt.height * scale_factor), int(src_nmt.width * scale_factor)
        transform = base_profile['transform'] * base_profile['transform'].scale(1/scale_factor, 1/scale_factor)
    
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
    
    output_path = paths['output_landcover_raster']
    out_profile = base_profile.copy()
    out_profile.update(height=ny, width=nx, transform=transform, dtype='uint8', nodata=0, compress='lzw')
    with rasterio.open(output_path, 'w', **out_profile) as dst:
        dst.write(landcover_raster, 1)
    
    print(f"-> Zapisano raster pokrycia terenu: {output_path}")
    print("--- Skrypt 0 (Land Cover) zakończony pomyślnie ---")
    return output_path
