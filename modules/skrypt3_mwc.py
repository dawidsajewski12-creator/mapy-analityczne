# 5. KOMFORT CIEPLNY - Ulepszona formuła UTCI + caching
# /modules/skrypt3_mwc.py
import numpy as np, rasterio, os, gc
from datetime import datetime

def calculate_utci_improved(Ta, RH, v10, Tmrt):
    """Ulepszona formuła UTCI - bardziej precyzyjna"""
    # Saturated vapour pressure
    es = 611.2 * np.exp(17.62 * Ta / (243.12 + Ta))
    e = es * RH / 100.0
    Pa = e / 100.0  # hPa to kPa
    
    # Podstawowa formuła UTCI (uproszczona wielomianowa aproksymacja)
    utci = Ta + 0.607562052 + (-0.0227712343 * Ta) + (8.06470249e-4 * Ta * Ta) + \
           (-0.154271372 * Pa) + (-3.24651735e-6 * Ta * Ta * Pa) + \
           (7.32602852e-8 * Ta * Ta * Pa * Pa) + (1.35959073e-2 * Pa * Pa) + \
           (-2.25836520 * v10) + (0.0880326035 * Ta * v10) + \
           (0.00216844454 * Ta * Ta * v10) + (-1.73117890e-4 * Ta * Ta * Ta * v10) + \
           (-0.00609315952 * v10 * v10) + (-0.000283432478 * Ta * v10 * v10) + \
           (8.55711863e-5 * Ta * Ta * v10 * v10) + (9.06474138e-7 * Ta * Ta * Ta * v10 * v10) + \
           (0.15458372 * Tmrt) + (0.00369670042 * Ta * Tmrt) + \
           (4.25014108e-6 * Ta * Ta * Tmrt) + (-2.98301478e-8 * Ta * Ta * Ta * Tmrt) + \
           (0.000184586073 * Pa * Tmrt) + (-3.81498312e-8 * Ta * Pa * Tmrt) + \
           (5.45686828e-12 * Ta * Ta * Pa * Tmrt)
    
    return utci

def calculate_mean_radiant_temp(lst, svf, solar_radiation, building_density):
    """Realistyczne MRT z uwzględnieniem radiacji"""
    # Podstawa - temperatura powierzchni
    mrt_base = lst.copy()
    
    # Długofalowa radiacja od powierzchni (Stefan-Boltzmann)
    stefan_boltzmann = 5.67e-8
    surface_radiation = stefan_boltzmann * (lst + 273.15)**4
    
    # Wpływ nieba (zimnego)
    sky_temp = lst - 15  # Niebo ~15°C zimniejsze
    sky_radiation = stefan_boltzmann * (sky_temp + 273.15)**4
    
    # Średnia ważona radiacji
    total_radiation = (1 - svf) * surface_radiation + svf * sky_radiation
    mrt = (total_radiation / stefan_boltzmann)**(1/4) - 273.15
    
    # Dodaj krótkofalową radiację słoneczną
    mrt += solar_radiation * 0.03  # Konwersja W/m² na °C
    
    # Efekt urban canyon (gęsta zabudowa)
    mrt += building_density * 5  # Do +5°C w gęstej zabudowie
    
    return mrt

def main(config, weather_data):
    print("\n--- Skrypt 3: Komfort Cieplny (Ulepszona UTCI) ---")
    paths, params = config['paths'], config['params']['uhi']
    
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        profile.update({'dtype': 'float32', 'compress': 'lzw'})
    
    # Wczytaj dane
    landcover = align_raster(paths['landcover'], profile, 'nearest')
    wind_speed = align_raster(paths['output_wind_speed_raster'], profile, 'bilinear')
    wind_speed = np.maximum(wind_speed, 0.5)  # Min 0.5 m/s
    
    # SVF z cache
    svf_path = os.path.join(os.path.dirname(paths['output_utci_raster']), "svf_cache.tif")
    svf = calculate_svf_optimized(landcover, profile, svf_path)
    
    # Radiacja słoneczna (realistyczna)
    from pysolar.solar import get_altitude
    import pytz
    
    loc = config['location']
    sim_time = pytz.timezone(loc['timezone']).localize(params['simulation_datetime'])
    solar_elevation = get_altitude(loc['latitude'], loc['longitude'], sim_time)
    
    if solar_elevation > 0:
        solar_radiation = 1000 * np.sin(np.deg2rad(solar_elevation)) * 0.75  # W/m²
    else:
        solar_radiation = 0
    
    print(f"-> Radiacja słoneczna: {solar_radiation:.0f} W/m²")
    
    # LST - realistyczne temperatury powierzchni
    lst_base = weather_data['temperature']  # Bazowa temperatura powietrza
    lst = np.full(landcover.shape, lst_base, dtype=np.float32)
    
    # Różnice temperatury powierzchni względem powietrza (realistyczne)
    temp_adjustments = {
        1: +15,  # Asfalt - bardzo gorący w słońcu
        2: +8,   # Budynki - ciepłe 
        3: -3,   # Las - chłodniejszy
        5: +2,   # Trawa - lekko cieplejsza
        6: +10,  # Gleba - gorąca
        7: -5    # Woda - chłodna
    }
    
    for lc, adj in temp_adjustments.items():
        mask = landcover == lc
        lst[mask] += adj
        # Dodatkowy efekt słońca dla twardych powierzchni
        if lc in [1, 6] and solar_radiation > 0:
            lst[mask] += solar_radiation * 0.02
    
    # Gęstość zabudowy (wpływ na MRT)
    building_mask = (landcover == 2).astype(np.float32)
    from scipy import ndimage
    building_density = ndimage.uniform_filter(building_mask, size=5)  # Lokalna gęstość
    
    # Oblicz MRT
    mrt = calculate_mean_radiant_temp(lst, svf, solar_radiation, building_density)
    
    # UTCI z ulepszoną formułą
    utci = calculate_utci_improved(
        weather_data['temperature'],  # Ta
        weather_data['humidity'],     # RH  
        wind_speed,                   # v10
        mrt                          # Tmrt
    )
    
    # Zapisz wynik
    with rasterio.open(paths['output_utci_raster'], 'w', **profile) as dst:
        dst.write(utci.astype(np.float32), 1)
    
    print(f"-> UTCI: {np.min(utci):.1f}°C - {np.max(utci):.1f}°C")
    del lst, mrt, utci, building_density; gc.collect()
    return paths['output_utci_raster']

def calculate_svf_optimized(landcover, profile, cache_path):
    """Zoptymalizowany SVF z cache i uproszczonym algorytmem"""
    if os.path.exists(cache_path):
        with rasterio.open(cache_path) as src:
            return src.read(1)
    
    print("-> Obliczanie SVF (uproszczony algorytm)...")
    # Uproszczony SVF bazujący na gęstości budynków
    building_mask = (landcover == 2).astype(np.float32)
    
    # Konwolucja z kernelem do wygładzenia
    from scipy import ndimage
    kernel_size = max(3, int(50 / profile['transform'][0]))  # 50m radius scaled
    building_density = ndimage.uniform_filter(building_mask, size=kernel_size)
    
    # SVF = 1 - gęstość budynków (uproszczenie)
    svf = 1.0 - building_density * 0.8  # Max redukcja 80%
    svf = np.clip(svf, 0.1, 1.0)  # Ograniczenia fizyczne
    
    # Cache wynik
    with rasterio.open(cache_path, 'w', **profile) as dst:
        dst.write(svf.astype(np.float32), 1)
    
    return svf
