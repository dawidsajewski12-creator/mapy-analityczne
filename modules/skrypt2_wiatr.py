# /modules/skrypt2_wiatr.py
import os; import zipfile; import numpy as np; import rasterio; from rasterio.features import rasterize; from rasterio.warp import reproject, Resampling; import geopandas as gpd; from scipy.ndimage import convolve, rotate, distance_transform_edt, sobel; from skimage.feature import corner_harris, corner_peaks
def find_and_extract_bdot_layers(zip_path, target_filenames, extract_folder):
    if not os.path.exists(extract_folder): os.makedirs(extract_folder)
    extracted_paths = []
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            if not member.is_dir() and any(target in os.path.basename(member.filename) for target in target_filenames):
                source = zip_ref.open(member); target_path = os.path.join(extract_folder, os.path.basename(member.filename))
                with open(target_path, "wb") as target_file: target_file.write(source.read())
                extracted_paths.append(target_path)
    return extracted_paths
def align_raster_to_base(path, base_shape, base_profile, resampling_method='nearest'):
    with rasterio.open(path) as src:
        aligned_arr = np.empty(base_shape, dtype=np.float32)
        reproject(source=rasterio.band(src, 1), destination=aligned_arr, src_transform=src.transform, src_crs=src.crs, dst_transform=base_profile['transform'], dst_crs=base_profile['crs'], resampling=Resampling[resampling_method])
    return aligned_arr
def main(config):
    paths = config['paths']; params = config['params']['wind']
    print("\n--- Uruchamianie Skryptu 2: Analiza Wiatru ---")
    print("   Etap 1: Przygotowanie danych...")
    with rasterio.open(paths['nmt']) as src_nmt:
        base_profile = src_nmt.profile; scale_factor = base_profile['transform'].a / params['target_res']
        ny = int(src_nmt.height * scale_factor); nx = int(src_nmt.width * scale_factor)
        transform = base_profile['transform'] * base_profile['transform'].scale(1/scale_factor, 1/scale_factor)
        base_profile.update(height=ny, width=nx, transform=transform, dtype='float32')
        nmt = np.empty((ny, nx), dtype=np.float32)
        reproject(source=src_nmt.read(1), destination=nmt, src_transform=src_nmt.transform, src_crs=src_nmt.crs, dst_transform=transform, dst_crs=base_profile['crs'], resampling=Resampling.bilinear)
    nmpt = align_raster_to_base(paths['nmpt'], (ny, nx), base_profile, 'bilinear')
    z0_raster = np.full((ny, nx), params['z0_map'][-1], dtype=np.float32)
    if os.path.exists(paths['landcover']):
        lc_resampled = align_raster_to_base(paths['landcover'], (ny, nx), base_profile, 'nearest').astype(np.uint8)
        for i in range(ny):
            for j in range(nx):
                if lc_resampled[i, j] in params['z0_map']: z0_raster[i, j] = params['z0_map'][lc_resampled[i, j]]
    object_heights = np.clip(nmpt - nmt, 0, None)
    bdot_mask = np.zeros((ny, nx), dtype=np.uint8)
    if os.path.exists(paths['bdot_zip']):
        building_paths = find_and_extract_bdot_layers(paths['bdot_zip'], [params['bdot_building_file']], paths['bdot_extract'])
        if building_paths:
            buildings_gdf = gpd.read_file(building_paths[0])
            if buildings_gdf.crs != base_profile['crs']: buildings_gdf = buildings_gdf.to_crs(base_profile['crs'])
            geometries = [(geom, 1) for geom in buildings_gdf.geometry]
            bdot_mask = rasterize(shapes=geometries, out_shape=(ny, nx), transform=transform, fill=0, dtype=np.uint8)
    building_footprints = ((bdot_mask == 1) & (object_heights > params['building_threshold'])).astype(np.float32)
    building_heights = object_heights * building_footprints
    print("   Etap 2: Obliczenia fizyczne...")
    mod_roughness = np.log(params['analysis_height'] / z0_raster) / np.log(10.0 / z0_raster); mod_roughness = np.clip(mod_roughness, 0, 2.0)
    bldg_h_values = building_heights[building_heights > 0]
    typical_max_h = np.percentile(bldg_h_values, 98) if len(bldg_h_values) > 0 else 0
    wake_length_m = min(10 * typical_max_h, 400); wake_length_px = int(wake_length_m / params['target_res'])
    if wake_length_px > 1:
        kernel_size = wake_length_px * 2 + 1; center = kernel_size // 2
        kernel = np.zeros((kernel_size, kernel_size), dtype=np.float32)
        coords = np.arange(-center, center + 1); x, y = np.meshgrid(coords, coords)
        mask_wake = (x >= 0) & (np.abs(y) < x * 0.4 + 10) & (x < wake_length_px)
        kernel[mask_wake] = (wake_length_px - x[mask_wake]) / wake_length_px
        rotated_kernel = rotate(kernel, -(params['wind_direction'] - 90), reshape=False, order=1)
        wake_effect = convolve(building_heights, rotated_kernel, mode='reflect')
        wake_reduction = np.clip(wake_effect / (np.percentile(wake_effect, 99.5) + 1e-6), 0, 1)
        mod_wake = 1.0 - wake_reduction
    else: mod_wake = np.ones_like(nmt, dtype=np.float32)
    inverted_footprints = 1 - building_footprints
    dist_to_building = distance_transform_edt(inverted_footprints) * params['target_res']
    mod_channel = np.interp(dist_to_building, [0, 50 / 2], [1.8, 1.0])
    corner_response = corner_harris(building_footprints)
    corners = corner_peaks(corner_response, min_distance=3, threshold_rel=0.01)
    corner_mod = np.ones_like(building_footprints)
    for r, c in corners:
        min_r, max_r = max(0, r-3), min(corner_mod.shape[0], r+4)
        min_c, max_c = max(0, c-3), min(corner_mod.shape[1], c+4)
        corner_mod[min_r:max_r, min_c:max_c] = 1.5
    mod_accel = np.maximum(mod_channel, corner_mod)
    final_wind_speed = params['wind_speed'] * mod_roughness * mod_wake * mod_accel
    final_wind_speed[building_footprints == 1] = 0
    print("   Etap 3: Zapisywanie wyniku...")
    output_path = paths['output_wind_raster']
    base_profile.update(nodata=-9999.0)
    with rasterio.open(output_path, 'w', **base_profile) as dst:
        dst.write(final_wind_speed.astype(np.float32), 1)
    print(f"--- Skrypt 2 zakończony pomyślnie! Wynik: {output_path} ---")
    return output_path
