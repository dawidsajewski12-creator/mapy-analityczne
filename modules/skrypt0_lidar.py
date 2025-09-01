# /modules/skrypt0_lidar.py
import os
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio import features  # <<< CORRECT IMPORT
from scipy.ndimage import gaussian_filter, maximum_filter, label
from skimage.segmentation import watershed
import geopandas as gpd
from shapely.geometry import Point
import laspy
from numba import njit, prange

@njit(parallel=True)
def rasterize_points_numba(points, min_x, max_y, res, nx, ny):
    grid_max = np.full((ny, nx), -np.inf, dtype=np.float32)
    for i in prange(points.shape[0]):
        col = int((points[i, 0] - min_x) / res)
        row = int((max_y - points[i, 1]) / res)
        if 0 <= row < ny and 0 <= col < nx:
            atomic_max(grid_max, (row, col), points[i, 2])
    for i in prange(ny):
        for j in range(nx):
            if np.isinf(grid_max[i, j]):
                grid_max[i, j] = np.nan
    return grid_max

@njit
def atomic_max(array, idx, value):
    if value > array[idx]:
        array[idx] = value

def main(config):
    print("\n--- Running Script 0: Lidar Processing ---")
    paths = config['paths']
    params = config['params']['lidar']
    with rasterio.open(paths['nmt']) as src_nmt:
        profile = src_nmt.profile
        nmt = src_nmt.read(1)
        ny, nx = nmt.shape
        minx, maxy = profile['transform'] * (0, 0)
        pixel_width = profile['transform'].a
    laz_files = [os.path.join(paths['laz_folder'], f) for f in os.listdir(paths['laz_folder']) if f.endswith('.laz')]
    if not laz_files:
        raise FileNotFoundError(f"No .laz files found in folder: {paths['laz_folder']}")
    vnmpt = np.full((ny, nx), np.nan, dtype=np.float32)
    for f in laz_files:
        print(f"  -> Processing file: {os.path.basename(f)}...")
        laz = laspy.read(f)
        veg_points = np.stack([laz.x, laz.y, laz.z], axis=1)[laz.classification == 5]
        if len(veg_points) > 0:
            vnmpt_tile = rasterize_points_numba(veg_points, minx, maxy, pixel_width, nx, ny)
            vnmpt = np.fmax(vnmpt, vnmpt_tile)
            
    # <<< CORRECTED FUNCTION CALL
    vnmpt_filled = features.fillnodata(vnmpt, mask=~np.isnan(vnmpt))
    
    chm = vnmpt_filled - nmt
    chm[chm < 0] = 0
    chm[chm > params['max_plausible_tree_height']] = 0
    chm_smoothed = gaussian_filter(chm, sigma=0.5)
    maxima = maximum_filter(chm_smoothed, size=params['treetop_filter_size'])
    treetops_mask = (chm_smoothed == maxima) & (chm > params['min_tree_height'])
    markers, num_features = label(treetops_mask)
    segmentation_mask = chm > (params['min_tree_height'] / 2)
    labels = watershed(-chm_smoothed, markers, mask=segmentation_mask)
    unique_labels, counts = np.unique(labels, return_counts=True)
    small_labels = unique_labels[counts * (pixel_width**2) < params['min_crown_area_m2']]
    for small_label in small_labels:
        labels[labels == small_label] = 0
    profile.update(dtype=rasterio.int32, nodata=0, compress='lzw')
    with rasterio.open(paths['output_crowns_raster'], 'w', **profile) as dst:
        dst.write(labels.astype(rasterio.int32), 1)
    print(f"  -> Tree crown raster saved.")
    tree_data = []
    unique_final_labels = np.unique(labels)[1:]
    for tree_id in unique_final_labels:
        current_crown_mask = (labels == tree_id)
        chm_crown = np.where(current_crown_mask, chm, -np.inf)
        flat_index = np.argmax(chm_crown)
        treetop_row, treetop_col = np.unravel_index(flat_index, chm_crown.shape)
        height_relative = chm[treetop_row, treetop_col]
        height_absolute = nmt[treetop_row, treetop_col] + height_relative
        crown_base = height_absolute - (height_relative * (1 - params['crown_base_factor']))
        treetop_x, treetop_y = profile['transform'] * (treetop_col + 0.5, treetop_row + 0.5)
        crown_area = np.sum(current_crown_mask) * (pixel_width**2)
        tree_data.append({'geometry': Point(treetop_x, treetop_y), 'tree_id': int(tree_id), 'wysokosc_wzgledna_m': float(height_relative), 'wysokosc_npm_m': float(height_absolute), 'podstawa_korony_m': float(crown_base), 'pow_korony_m2': float(crown_area)})
    if tree_data:
        gdf = gpd.GeoDataFrame(tree_data, crs=profile['crs'])
        gdf.to_file(paths['output_trees_vector'], driver='GPKG')
        print(f"  -> Inventory complete. Saved {len(gdf)} trees.")
    print("--- Script 0 Finished ---")
    return True
