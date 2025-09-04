# /modules/skrypt0_buildingmodel.py
import os
import geopandas as gpd
import pandas as pd
import rasterio
from rasterio.mask import mask
from rasterio.features import rasterize
import numpy as np
import zipfile

def find_and_extract_bdot_layers(zip_path, target_filenames, extract_folder):
    """Wyszukuje i wypakowuje określone warstwy z archiwum ZIP BDOT."""
    if not os.path.exists(extract_folder):
        os.makedirs(extract_folder)
    extracted_paths = {}
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            for target_key, target_name in target_filenames.items():
                if target_name in os.path.basename(member.filename) and not member.is_dir():
                    source = zip_ref.open(member)
                    target_path = os.path.join(extract_folder, os.path.basename(member.filename))
                    with open(target_path, "wb") as f:
                        f.write(source.read())
                    if target_key not in extracted_paths:
                        extracted_paths[target_key] = []
                    extracted_paths[target_key].append(target_path)
    return extracted_paths

def main(config):
    """
    Tworzy raster wysokości budynków na podstawie danych BDOT (warstwa BUBD) i NMPT.
    """
    print("\n--- Uruchamianie Skryptu 0: Modelowanie Budynków (warstwa BUBD) ---")
    paths = config['paths']
    
    # 1. Otwórz raster NMPT, aby uzyskać jego profil i dane
    try:
        with rasterio.open(paths['nmpt']) as src_nmpt:
            profile = src_nmpt.profile.copy()
            nmpt_data = src_nmpt.read(1)
            nodata_val = src_nmpt.nodata if src_nmpt.nodata is not None else -9999
            nmpt_data[nmpt_data == nodata_val] = np.nan
            profile.update(dtype='float32', nodata=0, compress='lzw')
    except rasterio.errors.RasterioIOError:
        print(f"BŁĄD: Nie można otworzyć pliku NMPT: {paths['nmpt']}")
        return None

    building_height_raster = np.zeros(nmpt_data.shape, dtype=np.float32)

    # 2. Wypakuj i wczytaj geometrię budynków z BDOT (warstwa BUBD_A)
    print("-> Wyszukiwanie i przetwarzanie warstwy budynków (BUBD_A)...")
    bdot_files = find_and_extract_bdot_layers(
        paths['bdot_zip'],
        {'buildings': 'BUBD_A'}, # KLUCZOWA ZMIANA: Użycie warstwy BUBD
        paths['bdot_extract']
    )

    if not bdot_files.get('buildings'):
        print("UWAGA: Nie znaleziono plików BUBD_A w archiwum BDOT. Pomijanie tworzenia modelu budynków.")
        with rasterio.open(paths['output_buildings_raster'], 'w', **profile) as dst:
            dst.write(building_height_raster, 1)
        return paths['output_buildings_raster']

    gdf_list = [gpd.read_file(fpath) for fpath in bdot_files['buildings']]
    buildings_gdf = gpd.GeoDataFrame(pd.concat(gdf_list, ignore_index=True))
    
    if buildings_gdf.crs != src_nmpt.crs:
        print(f"-> Reprojekcja CRS budynków z {buildings_gdf.crs} do {src_nmpt.crs}")
        buildings_gdf = buildings_gdf.to_crs(src_nmpt.crs)

    # 3. Iteruj po budynkach i obliczaj ich wysokość
    print(f"-> Obliczanie wysokości dla {len(buildings_gdf)} budynków...")
    geometries_with_heights = []
    for index, feature in buildings_gdf.iterrows():
        geom = feature.geometry
        try:
            masked_nmpt, _ = mask(src_nmpt, [geom], crop=True, all_touched=True)
            valid_pixels = masked_nmpt[masked_nmpt != nodata_val]
            if valid_pixels.size > 0:
                height = np.percentile(valid_pixels, 95)
                geometries_with_heights.append((geom, height))
        except (ValueError, IndexError):
            pass

    # 4. Zrasteryzuj wysokości budynków do siatki
    if geometries_with_heights:
        print("-> Rasteryzacja wysokości budynków...")
        rasterized_heights = rasterize(
            shapes=geometries_with_heights,
            out_shape=nmpt_data.shape,
            transform=profile['transform'],
            fill=0,
            dtype=np.float32
        )
        building_height_raster = np.maximum(building_height_raster, rasterized_heights)

    # 5. Zapisz wynikowy raster
    output_path = paths['output_buildings_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(building_height_raster, 1)

    print(f"-> Zapisano raster wysokości budynków: {output_path}")
    print("--- Skrypt 0 (Modelowanie Budynków) zakończony pomyślnie ---")
    return output_path
