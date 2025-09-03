# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
from pysolar.solar import get_altitude
from datetime import datetime
import pytz
import os

def align_raster(source_path, profile, resampling_method):
    if not os.path.exists(source_path):
        return np.zeros((profile['height'], profile['width']), dtype=np.float32)
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

def calculate_svf(nmpt, resolution, svf_path, profile):
    if os.path.exists(svf_path):
        print("  -> Wczytywanie istniejącego rastra Sky View Factor (SVF)...")
        with rasterio.open(svf_path) as src:
            return src.read(1)

    print("  -> Obliczanie Sky View Factor (SVF)...")
    svf = np.zeros_like(nmpt, dtype=np.float32)
    max_scan_radius = int(200 / resolution)
    num_directions = 16
    
    for i in range(num_directions):
        angle = i * (360.0 / num_directions)
        rad = np.deg2rad(angle)
        horizon_angle = np.full_like(nmpt, np.pi / 2, dtype=np.float32)
        for dist in range(1, max_scan_radius):
            dx, dy = int(dist * np.cos(rad)), int(dist * np.sin(rad))
            shifted_nmpt = np.roll(nmpt, (-dy, -dx), axis=(0, 1))
            elevation_angle = np.arctan2(shifted_nmpt - nmpt, dist * resolution)
            horizon_angle = np.minimum(horizon_angle, np.pi / 2 - elevation_angle)
        svf += np.sin(horizon_angle)**2
    svf /= num_directions
    
    with rasterio.open(svf_path, 'w', **profile) as dst:
        dst.write(svf.astype(np.float32), 1)
    
    return svf

def calculate_utci_regression(Ta, RH, v, MRT):
    # Model regresyjny UTCI
    return -0.0006*Ta**3 + 0.0137*Ta**2 + 0.987*Ta - 2.88 + 0.0176*RH - 0.252*v**0.5 + 0.75*(MRT - Ta)

def main(config, weather_data):
    print("\n--- Uruchamianie Skryptu 3: Analiza MWC (Wersja 2.0) ---")
    paths, params, loc = config['paths'], config['params']['uhi'], config['location']
    
    print("-> Etap 1: Wczytywanie danych...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy(); profile.update({'dtype': 'float32'})
    
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    wind_speed_map = align_raster(paths['output_wind_speed_raster'], profile, 'bilinear')
    wind_speed_map = np.maximum(wind_speed_map, 0.5)

    print("-> Etap 2: Obliczanie Insolacji i Cieni...")
    sim_time = pytz.timezone(loc['timezone']).localize(params['simulation_datetime'])
    altitude = get_altitude(loc['latitude'], loc['longitude'], sim_time)
    insolation = params['solar_constant'] * np.sin(np.deg2rad(altitude)) * params['atmospheric_transmissivity'] if altitude > 0 else 0

    svf_path = os.path.join(os.path.dirname(paths['output_utci_raster']), "svf_cached.tif")
    svf = calculate_svf(nmpt, profile['transform'][0], svf_path, profile)
    
    print("-> Etap 3: Symulacja Temperatury Powierzchni (LST)...")
    lst = np.full(nmpt.shape, params['lst_base_temp'][-1], dtype=np.float32)
    for lc, temp in params['lst_base_temp'].items():
        if lc != -1: lst[landcover == lc] = temp
    lst += insolation * params['insolation_heating_factor']

    print("-> Etap 4: Obliczanie Komfortu Cieplnego UTCI...")
    mrt = lst + (insolation * params['mrt_insolation_factor']) + (1 - svf) * 12

    utci = calculate_utci_regression(weather_data['temperature'], weather_data['humidity'], wind_speed_map, mrt)

    print("-> Etap 5: Zapisywanie wyników...")
    with rasterio.open(paths['output_utci_raster'], 'w', **profile) as dst:
        dst.write(utci, 1)

    print(f"--- Skrypt 3 zakończony pomyślnie! ---")
    return paths['output_utci_raster']
