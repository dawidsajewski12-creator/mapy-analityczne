# /modules/skrypt2_wiatr_jax.py
# Wersja 8.0: Symulacja CFD na GPU z użyciem JAX-CFD i generowanie mapy przepływu
import os
import numpy as np
import rasterio
from rasterio.enums import Resampling
import jax
import jax.numpy as jnp
import jax_cfd.base as cfd
from jax_cfd.ml import towers
from matplotlib import colors
from PIL import Image

def align_raster(source_path, profile, resampling_method):
    """Dopasowuje raster do docelowego profilu."""
    with rasterio.open(source_path) as src:
        array = src.read(
            1,
            out_shape=(profile['height'], profile['width']),
            resampling=getattr(Resampling, resampling_method)
        )
    return array

def generate_flow_map(u, v, output_path, building_mask):
    """
    Generuje obraz PNG (flow map) z pola wektorowego (u, v).
    Kąt -> Hue, Prędkość -> Value.
    """
    print("-> Generowanie mapy przepływu (flow_map.png)...")
    magnitude = np.sqrt(u**2 + v**2)
    angle = np.arctan2(v, u)

    # Normalizacja
    # Kąt na zakres 0-1 dla Hue
    h = (angle + np.pi) / (2 * np.pi)
    # Nasycenie stałe
    s = np.ones_like(h) * 0.9
    # Prędkość na zakres 0-1 dla Value, z ograniczeniem do 99 percentyla
    v_max = np.percentile(magnitude, 99.5)
    v = np.clip(magnitude / v_max, 0, 1) if v_max > 0 else np.zeros_like(magnitude)
    
    # Stworzenie obrazu HSV
    hsv = np.stack([h, s, v], axis=-1)
    
    # Konwersja do RGB
    rgb = colors.hsv_to_rgb(hsv)
    
    # Dodanie kanału Alpha (przezroczystość na budynkach)
    alpha = np.where(building_mask, 0, 255).astype(np.uint8)
    rgba = np.dstack(( (rgb*255).astype(np.uint8), alpha ))

    # Zapis do pliku PNG
    img = Image.fromarray(rgba, 'RGBA')
    img.save(output_path, 'PNG')
    print(f"  -> Mapa przepływu zapisana w: {output_path}")


def main(config):
    """Główna funkcja do symulacji wiatru z użyciem JAX-CFD na GPU."""
    print("\n--- Uruchamianie Symulacji Wiatru JAX-CFD (GPU) ---")
    paths = config['paths']
    params = config['params']['wind']

    # 1. Konfiguracja siatki obliczeniowej
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        target_res = params['target_res']
        scale = src.res[0] / target_res
        w, h = int(src.width * scale), int(src.height * scale)
        transform = src.transform * src.transform.scale(1 / scale, 1 / scale)
        profile.update({'height': h, 'width': w, 'transform': transform, 'dtype': 'float32'})

    grid = cfd.grids.Grid(shape=(h, w), domain=((0, h * target_res), (0, w * target_res)))

    # 2. Przygotowanie danych wejściowych
    print("-> Przygotowywanie danych wejściowych...")
    building_heights = align_raster(paths['output_buildings_raster'], profile, 'bilinear')
    # Maska przeszkód - tam gdzie wysokość budynku jest większa od wysokości analizy
    obstacle_mask = building_heights > params.get('analysis_height', 1.5)
    
    # 3. Konfiguracja warunków brzegowych i fizyki
    wind_speed = params.get('wind_speed', 5.0)
    wind_direction_deg = params.get('wind_direction', 270)
    wind_direction_rad = jnp.deg2rad(wind_direction_deg)

    # Ustawienie prędkości wlotowej
    velocity_x = wind_speed * jnp.cos(wind_direction_rad)
    velocity_y = wind_speed * jnp.sin(wind_direction_rad)
    inflow_velocity = (velocity_y, velocity_x) # (v, u) dla JAX-CFD (oś Y, oś X)

    # Warunki brzegowe Dirichleta na wlocie, zerowy gradient na wylocie
    bc = cfd.boundaries.dirichlet_boundary_conditions(grid, v=inflow_velocity)
    # Warunek braku poślizgu na przeszkodach
    obstacle_bc = cfd.boundaries.no_slip_boundary_conditions(grid)
    obstacle = cfd.geometry.obstacle(cfd.geometry.BooleanMask(obstacle_mask, grid=grid))
    
    # 4. Konfiguracja i uruchomienie symulacji
    # Zmniejszamy lepkość dla bardziej turbulentnego przepływu
    nu = params.get('kinematic_viscosity', 1.5e-2)
    dt = cfd.step_fns.stable_time_step(wind_speed, 0.5, nu, grid)
    
    # Równania Naviera-Stokesa z uwzględnieniem przeszkód
    step_fn = cfd.step_fns.semi_implicit_navier_stokes(
        density=1.225, viscosity=nu, dt=dt, grid=grid,
        convection_fn=cfd.advection.upwind,
        pressure_fn=cfd.pressure.fast_diagonalization,
        forcing=obstacle.forcing(obstacle_bc)
    )

    # Inicjalizacja pola prędkości
    v0 = tuple(jnp.full(grid.shape, c, grid.dtype) for c in inflow_velocity)
    
    # Kompilacja JIT i uruchomienie symulacji
    trajectory_fn = jax.jit(towers.trajectory(step_fn, num_steps=1500))
    
    print(f"-> Symulacja CFD: {wind_speed:.1f} m/s, {wind_direction_deg:.0f}°, kroki: 1500...")
    # Uruchomienie na urządzeniu (GPU, jeśli dostępne)
    _, trajectory = trajectory_fn(v0)
    
    vy, vx = trajectory # v, u
    
    # Konwersja wyników z JAX do NumPy
    final_u = np.array(vx)
    final_v = np.array(vy)
    
    # 5. Obliczenie i zapisanie wyników
    print("-> Zapisywanie wyników...")
    wind_speed_field = np.sqrt(final_u**2 + final_v**2)
    
    # Zapis rastra prędkości
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(wind_speed_field.astype(np.float32), 1)

    # Generowanie mapy przepływu do animacji
    generate_flow_map(final_u, final_v, paths['output_flow_map'], obstacle_mask)

    print(f"--- Symulacja CFD (JAX) zakończona! Max prędkość: {np.max(wind_speed_field):.1f} m/s ---")
    return paths['output_wind_speed_raster']
