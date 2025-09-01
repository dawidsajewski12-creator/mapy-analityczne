# /modules/skrypt3_mwc.py (Zawiera logikę skryptów 3,4,5)
import os; import numpy as np; import rasterio; from rasterio.warp import transform as warp_transform; from matplotlib.colors import LightSource; from datetime import datetime; import pytz; from pysolar.solar import get_altitude, get_azimuth; from numba import njit, prange
def align_raster(path, base_profile, resampling_method='nearest'):
    with rasterio.open(path) as src:
        aligned_arr = np.empty((base_profile['height'], base_profile['width']), dtype=np.float32)
        rasterio.warp.reproject(source=rasterio.band(src, 1), destination=aligned_arr, src_transform=src.transform, src_crs=src.crs, dst_transform=base_profile['transform'], dst_crs=base_profile['crs'], resampling=rasterio.warp.Resampling[resampling_method])
    return aligned_arr
@njit(parallel=True)
def calculate_utci_numba(utci_raster, temp_air, wind_speed, mrt, rh):
    for i in prange(temp_air.shape[0]):
        for j in range(temp_air.shape[1]):
            ta = temp_air[i, j]; va = wind_speed[i, j]; tr = mrt[i, j]
            utci = (ta + 0.46 * (tr - ta) - 2.8 * (va**0.5) + 0.08 * (rh - 50) - 0.003 * (tr - ta) * (rh - 50) + 0.012 * (ta - 25) * (va**0.5))
            utci_raster[i, j] = utci
    return utci_raster

def main(config, weather_data):
    print("\n--- Uruchamianie Skryptów 3-5: Analiza MWC i Komfortu Cieplnego ---")
    paths = config['paths']; params = config['params']['uhi']
    
    print("-> Etap 1: Wczytywanie danych...")
    with rasterio.open(paths['nmt']) as src_nmt:
        base_profile = src_nmt.profile; nmt = src_nmt.read(1); bounds = src_nmt.bounds; src_crs = src_nmt.crs
    nmpt = align_raster(paths['nmpt'], base_profile, 'bilinear'); lulc = align_raster(paths['landcover'], base_profile, 'nearest').astype(np.int16); wind = align_raster(paths['output_wind_raster'], base_profile, 'bilinear')

    print("-> Etap 2: Obliczanie Insolacji (Skrypt 4)...")
    center_x, center_y = (bounds.left + bounds.right) / 2, (bounds.top + bounds.bottom) / 2
    center_lon, center_lat = warp_transform(src_crs, {'init': 'EPSG:4326'}, [center_x], [center_y])
    date_utc = params['simulation_datetime'].replace(tzinfo=pytz.timezone(config['location']['timezone'])).astimezone(pytz.utc)
    sun_alt = get_altitude(center_lat[0], center_lon[0], date_utc); sun_azi = get_azimuth(center_lat[0], center_lon[0], date_utc)
    if sun_alt > 0:
        direct_radiation = params['solar_constant'] * params['atmospheric_transmissivity']**(1/np.sin(np.deg2rad(sun_alt))); ls = LightSource(azdeg=sun_azi, altdeg=sun_alt)
        hillshade = ls.shade(nmpt, cmap='gray', vert_exag=1.5, blend_mode='soft'); shadow_factor = hillshade[:,:,0] / 255.0; insolation = direct_radiation * shadow_factor
    else: insolation = np.zeros_like(nmt)

    print("-> Etap 3: Symulacja LST (Skrypt 3)...")
    lst = np.full(nmt.shape, params['lst_base_temp'][-1], dtype=np.float32)
    for lc_class, temp in params['lst_base_temp'].items(): lst[lulc == lc_class] = temp
    lst += insolation * params['insolation_heating_factor']
    
    print("-> Etap 4: Obliczanie Komfortu Cieplnego UTCI (Skrypt 5)...")
    temp_air = lst - 2.0; wind_clipped = np.maximum(0.5, wind)
    mrt = temp_air + (insolation * params['mrt_insolation_factor'])
    utci_raster = np.zeros_like(temp_air, dtype=np.float32)
    utci_raster = calculate_utci_numba(utci_raster, temp_air, wind_clipped, mrt, weather_data['humidity'])
    
    print("-> Etap 5: Zapisywanie wyników...")
    output_path = paths['output_utci_raster']; base_profile.update(nodata=-9999.0, dtype='float32')
    with rasterio.open(output_path, 'w', **base_profile) as dst: dst.write(utci_raster.astype(np.float32), 1)
    
    print(f"--- Skrypty 3-5 zakończone pomyślnie! Wynik: {output_path} ---")
    return output_path
