# -*- coding: utf-8 -*-
import numpy as np
import rasterio
from rasterio.enums import Resampling
from pysolar.solar import get_altitude
from datetime import datetime
import pytz
import os

def align_raster(source_path, profile, resampling_method):
    """Dopasowuje raster do zadanego profilu."""
    if not os.path.exists(source_path):
        print(f"BŁĄD: Plik do wyrównania nie istnieje: {source_path}")
        # Zwracamy pustą tablicę o odpowiednich wymiarach, wypełnioną zerami
        return np.zeros((profile['height'], profile['width']), dtype=np.float32)
        
    with rasterio.open(source_path) as src:
        array = src.read(1, out_shape=(profile['height'], profile['width']), resampling=getattr(Resampling, resampling_method))
    return array

def calculate_svf(nmpt, resolution, num_directions=16):
    """Oblicza uproszczony Sky View Factor (SVF)."""
    print("  -> Obliczanie Sky View Factor (SVF)...")
    svf = np.zeros_like(nmpt, dtype=np.float32)
    max_scan_radius = int(200 / resolution) # Skanuj do 200m

    for i in range(num_directions):
        angle = i * (360.0 / num_directions)
        rad = np.deg2rad(angle)
        horizon_angle = np.full_like(nmpt, np.pi / 2, dtype=np.float32)
        
        for dist in range(1, max_scan_radius):
            dx, dy = int(dist * np.cos(rad)), int(dist * np.sin(rad))
            
            # Przesunięta elewacja
            shifted_nmpt = np.roll(nmpt, (-dy, -dx), axis=(0, 1))
            
            # Kąt do horyzontu
            elevation_angle = np.arctan2(shifted_nmpt - nmpt, dist * resolution)
            
            # Aktualizuj kąt horyzontu tylko jeśli nowy obiekt jest wyższy
            horizon_angle = np.minimum(horizon_angle, np.pi / 2 - elevation_angle)

        svf += np.sin(horizon_angle)**2
        
    return svf / num_directions


def calculate_utci(temp_c, rh_percent, wind_speed_ms, mrt):
    """Uproszczona formuła UTCI."""
    # To jest bardzo duże uproszczenie, pełna formuła jest bardzo złożona.
    # Wartości są przybliżone i służą do celów poglądowych.
    utci = temp_c + 0.5 * (mrt - temp_c) - 2.0 * np.sqrt(wind_speed_ms) + 0.05 * (rh_percent - 50)
    return utci


def main(config, weather_data):
    print("\n--- Uruchamianie Skryptu 3: Analiza MWC (Wersja Zaawansowana) ---")
    paths = config['paths']
    params = config['params']['uhi']
    loc = config['location']
    
    print("-> Etap 1: Wczytywanie danych...")
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        profile.update({'dtype': 'float32'})
        nmt = src.read(1)
        
    nmpt = align_raster(paths['nmpt'], profile, 'bilinear')
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    
    # === KLUCZOWA ZMIANA: Wczytaj mapę wiatru ===
    wind_speed_map = align_raster(paths['output_wind_raster'], profile, 'bilinear')
    wind_speed_map = np.maximum(wind_speed_map, 0.5) # Minimalna prędkość wiatru 0.5 m/s

    print("-> Etap 2: Obliczanie Insolacji i Cieni...")
    tz = pytz.timezone(loc['timezone'])
    sim_time = tz.localize(params['simulation_datetime'])
    
    altitude = get_altitude(loc['latitude'], loc['longitude'], sim_time)
    
    if altitude <= 0:
        insolation = np.zeros_like(nmt, dtype=np.float32)
        shadows = np.ones_like(nmt, dtype=np.float32)
    else:
        # Użyjemy prostej inslacji, cienie pominiemy dla szybkości
        insolation = params['solar_constant'] * np.sin(np.deg2rad(altitude)) * params['atmospheric_transmissivity']
        
    # Oblicz Sky View Factor
    svf = calculate_svf(nmpt, profile['transform'][0])

    print("-> Etap 3: Symulacja Temperatury Powierzchni (LST)...")
    lst = np.full(nmt.shape, params['lst_base_temp'][-1], dtype=np.float32)
    for lc_class, temp in params['lst_base_temp'].items():
        if lc_class != -1:
            lst[landcover == lc_class] = temp
            
    # Modyfikuj LST na podstawie inslacji i cienia
    lst += insolation * params['insolation_heating_factor']

    print("-> Etap 4: Obliczanie Komfortu Cieplnego UTCI...")
    # Oblicz średnią temperaturę radiacji (MRT)
    # Uproszczony model: MRT zależy od LST, inslacji i "uwięzienia" ciepła przez budynki (1-SVF)
    mrt = lst + (insolation * params['mrt_insolation_factor']) + (1 - svf) * 10
    
    utci = calculate_utci(
        weather_data['temperature'],
        weather_data['humidity'],
        wind_speed_map, # Używamy mapy wiatru!
        mrt
    )

    print("-> Etap 5: Zapisywanie wyników...")
    output_path = paths['output_utci_raster']
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(utci, 1)

    print(f"--- Skrypt 3 zakończony pomyślnie! Wynik: {output_path} ---")
    return output_path
