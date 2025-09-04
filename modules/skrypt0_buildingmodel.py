# /modules/skrypt0_buildingmodel.py
import os
import geopandas as gpd
import pandas as pd
import rasterio
from rasterio.mask import mask
from rasterio.features import rasterize
from rasterio.enums import Resampling
import numpy as np
import zipfile
from numba import njit, prange

def find_and_extract_bdot_layers(zip_path, target_filenames, extract_folder):
    """Wyszukuje i wypakowuje określone warstwy z archiwum ZIP BDOT."""
    if not os.path.exists(extract_folder):
        os.makedirs(extract_folder)
    extracted_paths = {}
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            if member.is_dir():
                continue
            filename = os.path.basename(member.filename)
            for target_key, target_name in target_filenames.items():
                if target_name in filename:
                    source = zip_ref.open(member)
                    target_path = os.path.join(extract_folder, filename)
                    with open(target_path, "wb") as f:
                        f.write(source.read())
                    if target_key not in extracted_paths:
                        extracted_paths[target_key] = []
                    extracted_paths[target_key].append(target_path)
    return extracted_paths

@njit(parallel=True)
def create_building_voxels(height_raster, building_mask, max_height):
    """Tworzy 3D model budynków dla symulacji CFD"""
    ny, nx = height_raster.shape
    nz = int(max_height) + 1
    voxels = np.zeros((nz, ny, nx), dtype=np.uint8)
    
    for i in prange(ny):
        for j in prange(nx):
            if building_mask[i, j]:
                h = min(int(height_raster[i, j]), nz - 1)
                for k in range(h):
                    voxels[k, i, j] = 1
    return voxels

def main(config):
    """
    Tworzy kompletny model budynków z wysokościami i bryłami 3D
    """
    print("\n--- Uruchamianie Skryptu 0: Zaawansowany Model Budynków ---")
    paths = config['paths']
    params = config['params'].get('buildings', {
        'target_res': 2.0,
        'min_building_height': 3.0,
        'max_building_height': 100.0,
        'height_estimation_method': 'percentile95'
    })
    
    # 1. Setup siatki bazowej z NMT
    with rasterio.open(paths['nmt']) as src_nmt:
        profile = src_nmt.profile.copy()
        scale_factor = src_nmt.res[0] / params['target_res']
        new_height = int(src_nmt.height * scale_factor)
        new_width = int(src_nmt.width * scale_factor)
        transform = src_nmt.transform * src_nmt.transform.scale(1/scale_factor, 1/scale_factor)
        
        # Przeskaluj NMT
        nmt_data = src_nmt.read(1, out_shape=(new_height, new_width), resampling=Resampling.bilinear)
        
        profile.update({
            'height': new_height,
            'width': new_width, 
            'transform': transform,
            'dtype': 'float32',
            'nodata': 0,
            'compress': 'lzw'
        })

    # 2. Wczytaj NMPT i oblicz różnicę wysokości
    nmpt_data = None
    if os.path.exists(paths['nmpt']):
        with rasterio.open(paths['nmpt']) as src_nmpt:
            nmpt_data = src_nmpt.read(1, out_shape=(new_height, new_width), resampling=Resampling.bilinear)

    # 3. Inicjalizacja rastrów wynikowych
    building_height_raster = np.zeros((new_height, new_width), dtype=np.float32)
    building_mask = np.zeros((new_height, new_width), dtype=np.uint8)

    # 4. Przetwarzanie danych BDOT - warstwa BUBD_A (budynki)
    print("-> Przetwarzanie warstwy budynków BUBD_A...")
    bdot_files = find_and_extract_bdot_layers(
        paths['bdot_zip'],
        {'buildings': 'BUBD_A'},
        paths['bdot_extract']
    )

    if bdot_files.get('buildings'):
        print(f"  -> Znaleziono {len(bdot_files['buildings'])} plików budynków")
        
        # Wczytaj wszystkie pliki budynków
        gdf_list = []
        for fpath in bdot_files['buildings']:
            try:
                gdf = gpd.read_file(fpath)
                if not gdf.empty:
                    gdf_list.append(gdf)
            except Exception as e:
                print(f"    -> Błąd wczytywania {os.path.basename(fpath)}: {e}")
        
        if gdf_list:
            buildings_gdf = gpd.GeoDataFrame(pd.concat(gdf_list, ignore_index=True))
            
            # Reprojekcja CRS
            if buildings_gdf.crs != profile['crs']:
                print(f"  -> Reprojekcja z {buildings_gdf.crs} do {profile['crs']}")
                buildings_gdf = buildings_gdf.to_crs(profile['crs'])
            
            # 5. Oszacowanie wysokości budynków
            print(f"-> Oszacowanie wysokości dla {len(buildings_gdf)} budynków...")
            
            geometries_with_heights = []
            for idx, building in buildings_gdf.iterrows():
                geom = building.geometry
                
                # Metoda 1: Użyj NMPT jeśli dostępne
                if nmpt_data is not None:
                    try:
                        # Maska budynku na siatce
                        mask_result, mask_transform = mask([src_nmt], [geom], crop=True, all_touched=True)
                        if mask_result.size > 0:
                            # Pobierz wysokości z NMPT dla tego obszaru
                            rows, cols = rasterio.transform.rowcol(mask_transform, 
                                                                 [geom.bounds[0], geom.bounds[2]], 
                                                                 [geom.bounds[1], geom.bounds[3]])
                            
                            # Oszacuj wysokość budynku
                            if len(rows) > 1 and len(cols) > 1:
                                r_min, r_max = max(0, min(rows)), min(nmpt_data.shape[0], max(rows))
                                c_min, c_max = max(0, min(cols)), min(nmpt_data.shape[1], max(cols))
                                
                                if r_max > r_min and c_max > c_min:
                                    nmpt_patch = nmpt_data[r_min:r_max, c_min:c_max]
                                    nmt_patch = nmt_data[r_min:r_max, c_min:c_max]
                                    height_diff = nmpt_patch - nmt_patch
                                    
                                    valid_heights = height_diff[height_diff > params['min_building_height']]
                                    if len(valid_heights) > 0:
                                        if params['height_estimation_method'] == 'percentile95':
                                            estimated_height = np.percentile(valid_heights, 95)
                                        elif params['height_estimation_method'] == 'max':
                                            estimated_height = np.max(valid_heights)
                                        else:
                                            estimated_height = np.mean(valid_heights)
                                        
                                        # Ograniczenia wysokości
                                        estimated_height = np.clip(estimated_height, 
                                                                 params['min_building_height'], 
                                                                 params['max_building_height'])
                                        
                                        geometries_with_heights.append((geom, estimated_height))
                                        continue
                    except Exception:
                        pass
                
                # Metoda 2: Wysokość domyślna na podstawie powierzchni
                area = geom.area
                if area > 500:  # Duże budynki
                    default_height = 15.0
                elif area > 100:  # Średnie budynki
                    default_height = 8.0
                else:  # Małe budynki
                    default_height = 5.0
                
                geometries_with_heights.append((geom, default_height))
            
            # 6. Rasteryzacja budynków z wysokościami
            if geometries_with_heights:
                print(f"  -> Rasteryzacja {len(geometries_with_heights)} budynków...")
                
                # Rasteryzacja wysokości
                rasterized_heights = rasterize(
                    shapes=geometries_with_heights,
                    out_shape=(new_height, new_width),
                    transform=transform,
                    fill=0,
                    dtype=np.float32
                )
                
                # Rasteryzacja maski (budynek/nie-budynek)
                building_shapes = [(geom, 1) for geom, _ in geometries_with_heights]
                rasterized_mask = rasterize(
                    shapes=building_shapes,
                    out_shape=(new_height, new_width),
                    transform=transform,
                    fill=0,
                    dtype=np.uint8
                )
                
                building_height_raster = rasterized_heights
                building_mask = rasterized_mask
                
                print(f"  -> Średnia wysokość budynków: {np.mean(building_height_raster[building_mask > 0]):.1f}m")
                print(f"  -> Max wysokość: {np.max(building_height_raster):.1f}m")

    # 7. Tworzenie modelu 3D dla CFD
    max_height = max(params['max_building_height'], np.max(building_height_raster))
    print(f"-> Tworzenie modelu 3D voxeli (max wysokość: {max_height:.0f}m)...")
    
    building_voxels = create_building_voxels(building_height_raster, building_mask, max_height)
    
    # 8. Zapisz wyniki
    print("-> Zapisywanie wyników...")
    
    # Raster wysokości budynków
    output_height_path = paths.get('output_buildings_raster', 
                                 os.path.join(os.path.dirname(paths['output_landcover_raster']), 'budynki_wysokosci.tif'))
    with rasterio.open(output_height_path, 'w', **profile) as dst:
        dst.write(building_height_raster, 1)
    
    # Raster maski budynków
    mask_profile = profile.copy()
    mask_profile.update(dtype='uint8')
    output_mask_path = paths.get('output_buildings_mask', 
                               os.path.join(os.path.dirname(paths['output_landcover_raster']), 'budynki_maska.tif'))
    with rasterio.open(output_mask_path, 'w', **mask_profile) as dst:
        dst.write(building_mask, 1)
    
    # Model 3D (jako numpy array - do użycia w CFD)
    voxel_path = paths.get('output_buildings_voxels',
                          os.path.join(os.path.dirname(paths['output_landcover_raster']), 'budynki_3d.npy'))
    np.save(voxel_path, building_voxels)
    
    # Zaktualizuj ścieżki w config
    paths['output_buildings_raster'] = output_height_path
    paths['output_buildings_mask'] = output_mask_path
    paths['output_buildings_voxels'] = voxel_path
    
    print(f"-> Zapisano model budynków:")
    print(f"   - Wysokości: {output_height_path}")
    print(f"   - Maska: {output_mask_path}")
    print(f"   - Model 3D: {voxel_path}")
    print("--- Skrypt 0 (Model Budynków) zakończony pomyślnie ---")
    
    return {
        'height_raster': output_height_path,
        'mask_raster': output_mask_path,
        'voxel_model': voxel_path,
        'stats': {
            'num_buildings': np.sum(building_mask > 0),
            'avg_height': float(np.mean(building_height_raster[building_mask > 0])) if np.any(building_mask) else 0,
            'max_height': float(np.max(building_height_raster))
        }
    }
