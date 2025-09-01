# -*- coding: utf-8 -*-
"""
FINALNY PIPELINE (v3.5): Ostateczny, Poprawiony Skrypt Sterujący

Opis:
- Naprawiono błąd `KeyError` poprzez zapewnienie, że pełna i poprawna
  konfiguracja jest tworzona i przekazywana do każdego modułu analitycznego.
- Zintegrowano wszystkie najlepsze praktyki, w tym generowanie
  kolorowych kafelków RGBA dla poprawnej wizualizacji.
"""
import os
import sys
import subprocess
from datetime import datetime
import rasterio
import numpy as np
import geopandas as gpd
from google.colab import userdata, drive, output

# --- 1. KONFIGURACJA GŁÓWNA ---
#@title Konfiguracja Projektu i Analizy
#@markdown ### 1. Ustawienia GitHub
GITHUB_USERNAME = "dawidsajewski12-creator" #@param {type:"string"}
GITHUB_REPONAME = "mapy-analityczne" #@param {type:"string"}

#@markdown ---
#@markdown ### 2. Wybierz Analizę do Uruchomienia
ANALIZA_DO_URUCHOMIENIA = "landcover"  #@param ["landcover"]

# Definicja legendy (centralne miejsce)
LEGEND_MAP = {
    1: {'name': 'Nawierzchnie utwardzone', 'color': (169, 169, 169)},
    2: {'name': 'Budynki', 'color': (220, 20, 60)},
    3: {'name': 'Drzewa / Lasy', 'color': (34, 139, 34)},
    5: {'name': 'Trawa / Tereny zielone', 'color': (124, 252, 0)},
    6: {'name': 'Gleba / Nieużytki', 'color': (210, 180, 140)},
    7: {'name': 'Woda', 'color': (30, 144, 255)}
}

# --- FUNKCJE POMOCNICZE ---
def create_rgba_raster(input_raster_path, output_raster_path, legend_map):
    print(f"-> Tworzenie kolorowego rastra RGBA: {os.path.basename(output_raster_path)}")
    with rasterio.open(input_raster_path) as src:
        data = src.read(1); profile = src.profile.copy()
    rgba_raster = np.zeros((4, data.shape[0], data.shape[1]), dtype=np.uint8)
    for class_id, info in legend_map.items():
        mask = data == class_id
        rgba_raster[0][mask] = info['color'][0]; rgba_raster[1][mask] = info['color'][1]
        rgba_raster[2][mask] = info['color'][2]; rgba_raster[3][mask] = 255
    profile.update(count=4, dtype='uint8', nodata=None)
    with rasterio.open(output_raster_path, 'w', **profile) as dst:
        dst.write(rgba_raster)
    return output_raster_path

def generate_tiles(input_raster, output_folder, zoom_levels='11-16'):
    if not os.path.exists(input_raster): print(f"BŁĄD: Plik wejściowy nie istnieje: {input_raster}"); return
    print(f"-> Generowanie kafelków dla: {os.path.basename(input_raster)}")
    if os.path.exists(output_folder): import shutil; shutil.rmtree(output_folder)
    os.makedirs(output_folder)
    try:
        with rasterio.open(input_raster) as src: crs_string = src.crs.to_string()
        command = ['gdal2tiles.py', '--profile', 'mercator', '-z', zoom_levels, '-w', 'none', '--s_srs', crs_string, input_raster, output_folder]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except Exception as e:
        print(f"BŁĄD podczas generowania kafelków: {e}")
        if hasattr(e, 'stderr'): print(e.stderr)

def run_master_pipeline():
    try: GITHUB_TOKEN = userdata.get('GITHUB_TOKEN')
    except: raise Exception("Nie znaleziono sekretu 'GITHUB_TOKEN'.")
    

    print("Rozpoczynanie procesu automatycznej aktualizacji...")
    local_repo_path = f"/content/{GITHUB_REPONAME}"
    if os.path.exists(local_repo_path):
        import shutil; shutil.rmtree(local_repo_path)
    
    print(f"-> Klonowanie repozytorium '{GITHUB_REPONAME}'...")
    repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPONAME}.git"
    subprocess.run(['git', 'clone', repo_url, local_repo_path], check=True)
    os.chdir(local_repo_path)
    subprocess.run(['git', 'config', '--global', 'user.name', GITHUB_USERNAME])
    subprocess.run(['git', 'config', '--global', 'user.email', f'{GITHUB_USERNAME}@users.noreply.github.com'])
    output.clear(); print("-> Repozytorium sklonowane i skonfigurowane pomyślnie.")
    
    sys.path.insert(0, local_repo_path)
    from modules import skrypt0_landcover

    # ### POPRAWKA: Tworzymy jeden, kompletny słownik CONFIG ###
    CONFIG = {
        "paths": {
            "nmt": "/content/drive/MyDrive/ProjektGIS/dane/nmt.tif",
            "bdot_zip": "/content/drive/MyDrive/ProjektGIS/dane/bdot10k.zip",
            "bdot_extract": os.path.join(local_repo_path, "wyniki/bdot_extracted"),
            "output_landcover_raster": os.path.join(local_repo_path, "wyniki/rastry/landcover.tif"),
            "output_landcover_rgba_raster": os.path.join(local_repo_path, "wyniki/rastry/landcover_rgba.tif"),
            "output_landcover_tiles": os.path.join(local_repo_path, "wyniki/kafelki/landcover"),
            "output_landcover_stats": os.path.join(local_repo_path, "wyniki/landcover_stats.json")
        },
        "params": {
            "target_res": 2.0,
            "target_landcover_files": ["PTTR_A", "PTRK_A", "PTPL_A", "PTUT_A", "PTKM_A", "PTZB_A", "PTLZ_A", "PTGN_A", "PTNZ_A", "PTWP_A", "PTWZ_A"],
            "classification_map": { "OT_PTRK_A": (1, "Paved"), "OT_PTPL_A": (1, "Paved"), "OT_PTUT_A": (1, "Paved"), "OT_PTKM_A": (1, "Paved"), "OT_PTZB_A": (2, "Buildings"), "OT_PTLZ_A": (3, "Trees"), "OT_PTTR_A": (5, "Grass"), "OT_PTGN_A": (5, "Grass"), "OT_PTNZ_A": (6, "Bare soil"), "OT_PTWP_A": (7, "Water"), "OT_PTWZ_A": (7, "Water") },
            "legend_map": LEGEND_MAP
        }
    }
    
    # Upewniamy się, że foldery wyjściowe istnieją
    for path in CONFIG['paths'].values():
        if isinstance(path, str) and 'wyniki' in path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # === URUCHOMIENIE ANALIZY TESTOWEJ ===
    if ANALIZA_DO_URUCHOMIENIA == "landcover":
        class_raster_path = skrypt0_landcover.run_landcover_analysis(CONFIG)
        color_raster_path = create_rgba_raster(class_raster_path, CONFIG['paths']['output_landcover_rgba_raster'], LEGEND_MAP)
        generate_tiles(color_raster_path, CONFIG['paths']['output_landcover_tiles'])
    # W przyszłości dodamy tu `elif ANALIZA_DO_URUCHOMIENIA == "wiatr": ...` itd.

    # === AUTOMATYCZNY COMMIT I PUSH ===
    print("\n-> Wysyłanie zaktualizowanych wyników na GitHub...")
    subprocess.run(['git', 'add', 'wyniki/'])
    commit_message = f"Automatyczna aktualizacja: {ANALIZA_DO_URUCHOMIENIA} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    subprocess.run(['git', 'commit', '-m', commit_message])
    subprocess.run(['git', 'push'])
    output.clear()
    print("✅ Zakończono proces! Zmiany zostały wysłane na GitHub.")

# Uruchomienie
run_master_pipeline()

