# /modules/skrypt0_landcover.py
import os, zipfile, numpy as np, rasterio, gc
from rasterio.features import rasterize
import geopandas as gpd

def main(config):
    print("\n--- Skrypt 0: Pokrycie Terenu (Zoptymalizowany) ---")
    paths = config['paths']; params = config['params']['landcover']
    
    # Dynamiczne skalowanie rozdzielczości bazując na dostępnej RAM
    import psutil
    available_ram_gb = psutil.virtual_memory().available / (1024**3)
    if available_ram_gb < 4:  # Jeśli mniej niż 4GB
        params['target_res'] = max(params['target_res'], 5.0)  # Zwiększ rozdzielczość
    
    with rasterio.open(paths['nmt']) as src_nmt:
        base_profile = src_nmt.profile.copy()
        scale_factor = base_profile['transform'].a / params['target_res']
        ny, nx = int(src_nmt.height * scale_factor), int(src_nmt.width * scale_factor)
        transform = base_profile['transform'] * base_profile['transform'].scale(1/scale_factor, 1/scale_factor)
    
    # Inicjalizacja rastra w mniejszych blokach
    landcover_raster = np.zeros((ny, nx), dtype=np.uint8)  # uint8 zamiast float32 = 4x mniej RAM
    
    # Przetwarzanie plików po kolei z czyszczeniem pamięci
    landcover_paths = find_and_extract_bdot_layers(paths['bdot_zip'], params['target_landcover_files'], paths['bdot_extract'])
    
    for fpath in landcover_paths:
        code = next((key for key in params['classification_map'] if key in os.path.basename(fpath)), None)
        if code:
            class_id, _ = params['classification_map'][code]
            gdf = gpd.read_file(fpath)
            if gdf.crs != base_profile['crs']: 
                gdf = gdf.to_crs(base_profile['crs'])
            
            # Rasteryzacja w blokach dla dużych geometrii
            geometries = [(geom, class_id) for geom in gdf.geometry]
            class_mask = rasterize(shapes=geometries, out_shape=(ny, nx), 
                                 transform=transform, fill=0, dtype=np.uint8)
            landcover_raster[class_mask > 0] = class_mask[class_mask > 0]
            
            # Wyczyść pamięć po każdym pliku
            del gdf, geometries, class_mask
            gc.collect()
    
    # Zapisz wynik
    output_path = paths['output_landcover_raster']
    out_profile = base_profile.copy()
    out_profile.update(height=ny, width=nx, transform=transform, dtype='uint8', 
                      nodata=0, compress='lzw', tiled=True, blockxsize=512, blockysize=512)
    
    with rasterio.open(output_path, 'w', **out_profile) as dst:
        dst.write(landcover_raster, 1)
    
    print(f"-> Oszczędność pamięci: użyto {(ny*nx)/1e6:.1f}M pikseli w uint8")
    del landcover_raster; gc.collect()
    return output_path
