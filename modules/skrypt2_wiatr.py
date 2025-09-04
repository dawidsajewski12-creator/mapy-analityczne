# Opcjonalne ulepszenia do dodania w skrypt2_wiatr.py

# 1. LEPSZY PROFIL WLOTU WIATRU
def create_wind_profile(heights, wind_speed, wind_direction, z0=0.1, h_ref=10.0):
    """Tworzy realistyczny profil logarytmiczny wiatru"""
    u_star = wind_speed * 0.41 / np.log(h_ref / z0)
    
    wind_rad = np.deg2rad(wind_direction)
    profile_u = np.zeros_like(heights)
    profile_v = np.zeros_like(heights)
    
    for i, h in enumerate(heights):
        if h > z0:
            speed_at_h = (u_star / 0.41) * np.log(h / z0)
            profile_u[i] = speed_at_h * np.sin(wind_rad)
            profile_v[i] = speed_at_h * np.cos(wind_rad)
    
    return profile_u, profile_v

# 2. TURBULENCJA BUDYNKÓW
@njit
def add_building_turbulence(u, v, buildings, intensity=0.1):
    """Dodaje turbulencję za budynkami"""
    ny, nx = u.shape
    
    for i in range(1, ny-1):
        for j in range(1, nx-1):
            if buildings[i, j] > 0.5:
                # Dodaj wake turbulence za budynkiem
                for wake_j in range(j+1, min(nx, j+5)):
                    if buildings[i, wake_j] < 0.5:
                        # Redukcja prędkości i dodanie fluktuacji
                        u[i, wake_j] *= (0.7 + np.random.random() * intensity)
                        v[i, wake_j] *= (0.7 + np.random.random() * intensity)
    
    return u, v

# 3. ADAPTACYJNA SIATKA
def create_adaptive_grid(building_mask, base_resolution):
    """Tworzy gęstszą siatkę wokół budynków"""
    refinement_zones = binary_dilation(building_mask, iterations=3)
    
    # W rzeczywistej implementacji użyłbyś interpolacji
    # lub biblioteki typu OpenFOAM/FEniCS
    return refinement_zones

# 4. WALIDACJA WYNIKÓW
def validate_cfd_results(u, v, buildings, wind_speed):
    """Sprawdza jakość wyników CFD"""
    issues = []
    
    # Sprawdź masę
    speed = np.sqrt(u**2 + v**2)
    if np.max(speed) > wind_speed * 3:
        issues.append("Zbyt wysokie prędkości - możliwe przyspieszenie numeryczne")
    
    # Sprawdź warunki brzegowe
    if np.any(speed[buildings > 0.5] > 0.1):
        issues.append("Naruszenie warunków no-slip na budynkach")
    
    # Sprawdź ciągłość
    divergence = np.zeros_like(u)
    for i in range(1, u.shape[0]-1):
        for j in range(1, u.shape[1]-1):
            div = (u[i, j+1] - u[i, j-1])/2 + (v[i+1, j] - v[i-1, j])/2
            divergence[i, j] = abs(div)
    
    if np.mean(divergence) > 0.1:
        issues.append("Duża dywergencja - sprawdź ciągłość")
    
    return issues

# 5. EKSPORT DO PARAVIEW
def export_to_vtk(u, v, speed, pressure, buildings, output_path):
    """Eksportuje wyniki do formatu VTK dla ParaView"""
    try:
        import pyvista as pv
        
        ny, nx = u.shape
        grid = pv.StructuredGrid()
        
        # Siatka strukturalna
        x = np.arange(nx)
        y = np.arange(ny)
        z = np.array([0])  # 2D
        X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
        
        grid.points = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
        grid.dimensions = [nx, ny, 1]
        
        # Dodaj dane
        grid.point_data['Velocity_U'] = u.ravel()
        grid.point_data['Velocity_V'] = v.ravel()
        grid.point_data['Speed'] = speed.ravel()
        grid.point_data['Pressure'] = pressure.ravel()
        grid.point_data['Buildings'] = buildings.ravel()
        
        # Zapisz
        grid.save(output_path.replace('.tif', '.vtk'))
        print(f"  -> Eksport VTK: {output_path.replace('.tif', '.vtk')}")
        
    except ImportError:
        print("  -> PyVista niedostępne - pomiń eksport VTK")

# 6. STATYSTYKI CFD
def calculate_cfd_statistics(u, v, buildings):
    """Oblicza szczegółowe statystyki CFD"""
    speed = np.sqrt(u**2 + v**2)
    
    # Obszary otwarte (bez budynków)
    open_areas = buildings < 0.5
    
    stats = {
        'reynolds_number': np.mean(speed) * 100 / 1.5e-5,  # Przybliżony Re
        'velocity_ratio': np.max(speed) / np.mean(speed[open_areas]),
        'building_coverage': np.sum(buildings > 0.5) / buildings.size,
        'flow_separation_zones': np.sum(speed < np.mean(speed) * 0.3),
        'acceleration_zones': np.sum(speed > np.mean(speed) * 1.5),
        'turbulence_intensity': np.std(speed) / np.mean(speed)
    }
    
    return stats

# Użycie w main():
# W funkcji main() dodaj przed zapisem wyników:

# Walidacja
issues = validate_cfd_results(u, v, building_mask.astype(float), wind_speed)
if issues:
    print("  -> Uwagi dotyczące jakości CFD:")
    for issue in issues:
        print(f"     - {issue}")

# Statystyki
cfd_stats = calculate_cfd_statistics(u, v, building_mask.astype(float))
print(f"  -> Reynolds number: {cfd_stats['reynolds_number']:.0f}")
print(f"  -> Pokrycie budynkami: {cfd_stats['building_coverage']*100:.1f}%")
print(f"  -> Intensywność turbulencji: {cfd_stats['turbulence_intensity']:.3f}")

# Eksport VTK (opcjonalnie)
# export_to_vtk(u, v, speed, pressure, building_mask.astype(float), paths['output_wind_speed_raster'])
