# -*- coding: utf-8 -*-
"""
Enhanced CFD Wind Simulation v9.0
Lattice Boltzmann Method with stable convergence
Optimized for Colab performance
"""

import numpy as np
import rasterio
from rasterio.enums import Resampling
from numba import njit, prange, cuda
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter
import os
import json

# LBM D2Q9 constants
@njit
def get_lbm_constants():
    """D2Q9 lattice vectors and weights"""
    ex = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1], dtype=np.float32)
    ey = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1], dtype=np.float32)
    w = np.array([4/9, 1/9, 1/9, 1/9, 1/9, 1/36, 1/36, 1/36, 1/36], dtype=np.float32)
    return ex, ey, w

@njit(parallel=True)
def equilibrium(rho, ux, uy, ex, ey, w):
    """Equilibrium distribution for LBM"""
    feq = np.zeros((9, rho.shape[0], rho.shape[1]), dtype=np.float32)
    usqr = 3/2 * (ux**2 + uy**2)
    
    for k in prange(9):
        cu = 3 * (ex[k]*ux + ey[k]*uy)
        feq[k] = rho * w[k] * (1 + cu + 0.5*cu**2 - usqr)
    
    return feq

@njit(parallel=True)
def collision_bgk(f, feq, tau):
    """BGK collision operator"""
    return f - (f - feq) / tau

@njit
def streaming(f, ex, ey):
    """Streaming step with periodic BC"""
    fnew = np.zeros_like(f)
    ny, nx = f.shape[1], f.shape[2]
    
    for k in range(9):
        for i in range(ny):
            for j in range(nx):
                # Periodic boundaries
                ip = (i - int(ey[k]) + ny) % ny
                jp = (j - int(ex[k]) + nx) % nx
                fnew[k, i, j] = f[k, ip, jp]
    
    return fnew

@njit(parallel=True)
def apply_obstacles(f, obstacle, ux_in, uy_in):
    """Bounce-back BC for obstacles + inlet BC"""
    ny, nx = f.shape[1], f.shape[2]
    
    # Bounce-back for obstacles
    for i in prange(ny):
        for j in prange(nx):
            if obstacle[i, j]:
                # Simple bounce-back
                f[1,i,j], f[3,i,j] = f[3,i,j], f[1,i,j]
                f[2,i,j], f[4,i,j] = f[4,i,j], f[2,i,j]
                f[5,i,j], f[7,i,j] = f[7,i,j], f[5,i,j]
                f[6,i,j], f[8,i,j] = f[8,i,j], f[6,i,j]
    
    # Inlet BC (left boundary)
    rho_in = 1.0
    for i in range(ny):
        if not obstacle[i, 0]:
            # Zou-He BC
            f[1,i,0] = f[3,i,0] + 2/3 * rho_in * ux_in
            f[5,i,0] = f[7,i,0] + 1/6 * rho_in * ux_in + 0.5 * (f[4,i,0] - f[2,i,0]) + rho_in * uy_in/2
            f[8,i,0] = f[6,i,0] + 1/6 * rho_in * ux_in - 0.5 * (f[4,i,0] - f[2,i,0]) - rho_in * uy_in/2
    
    return f

@njit(parallel=True)
def compute_macroscopic(f):
    """Compute density and velocity"""
    rho = np.sum(f, axis=0)
    ux = (f[1] - f[3] + f[5] - f[6] - f[7] + f[8]) / rho
    uy = (f[2] - f[4] + f[5] + f[6] - f[7] - f[8]) / rho
    
    # Handle division by zero
    ux = np.where(rho > 0.1, ux, 0.0)
    uy = np.where(rho > 0.1, uy, 0.0)
    
    return rho, ux, uy

@njit(parallel=True)
def smagorinsky_viscosity(ux, uy, cs, dx, base_nu):
    """Smagorinsky LES model for turbulence"""
    ny, nx = ux.shape
    nu_t = np.zeros((ny, nx), dtype=np.float32)
    
    for i in prange(1, ny-1):
        for j in prange(1, nx-1):
            # Strain rate tensor
            dudx = (ux[i, j+1] - ux[i, j-1]) / (2*dx)
            dudy = (ux[i+1, j] - ux[i-1, j]) / (2*dx)
            dvdx = (uy[i, j+1] - uy[i, j-1]) / (2*dx)
            dvdy = (uy[i+1, j] - uy[i-1, j]) / (2*dx)
            
            # Magnitude of strain rate
            S = np.sqrt(2*(dudx**2 + dvdy**2 + 0.5*(dudy + dvdx)**2))
            
            # Turbulent viscosity
            nu_t[i, j] = (cs * dx)**2 * S
    
    return base_nu + nu_t

class LatticeBoltzmannCFD:
    """Advanced LBM CFD solver"""
    
    def __init__(self, nx, ny, Re=100, cs=0.1):
        self.nx, self.ny = nx, ny
        self.Re = Re
        self.cs = cs  # Smagorinsky constant
        self.ex, self.ey, self.w = get_lbm_constants()
        
        # Initialize distribution functions
        self.f = np.ones((9, ny, nx), dtype=np.float32) * 4/9
        self.rho = np.ones((ny, nx), dtype=np.float32)
        self.ux = np.zeros((ny, nx), dtype=np.float32)
        self.uy = np.zeros((ny, nx), dtype=np.float32)
        
    def simulate(self, obstacle, ux_in, uy_in, steps=500, adaptive=True):
        """Run LBM simulation with adaptive timestep"""
        
        # Physical parameters
        u_max = max(abs(ux_in), abs(uy_in))
        nu = u_max * max(self.nx, self.ny) / self.Re
        tau = 3*nu + 0.5
        
        print(f"  LBM: Re={self.Re:.0f}, τ={tau:.3f}, steps={steps}")
        
        # Initialize with inlet flow
        self.ux[:, :] = ux_in * 0.1  # Start gently
        self.uy[:, :] = uy_in * 0.1
        
        convergence_history = []
        
        for step in range(steps):
            # Store old velocity for convergence check
            ux_old = self.ux.copy()
            
            # LBM steps
            feq = equilibrium(self.rho, self.ux, self.uy, self.ex, self.ey, self.w)
            
            # Adaptive turbulence model
            if adaptive and step > 50:
                nu_eff = smagorinsky_viscosity(self.ux, self.uy, self.cs, 1.0, nu)
                tau_local = 3*nu_eff + 0.5
                # Use average tau for stability
                tau = np.mean(tau_local)
            
            self.f = collision_bgk(self.f, feq, tau)
            self.f = streaming(self.f, self.ex, self.ey)
            self.f = apply_obstacles(self.f, obstacle, ux_in, uy_in)
            
            # Update macroscopic
            self.rho, self.ux, self.uy = compute_macroscopic(self.f)
            
            # Apply obstacle mask
            self.ux[obstacle] = 0
            self.uy[obstacle] = 0
            
            # Check convergence
            if step % 20 == 0:
                error = np.mean(np.abs(self.ux - ux_old))
                convergence_history.append(error)
                
                if step % 100 == 0:
                    speed = np.sqrt(self.ux**2 + self.uy**2)
                    print(f"    Step {step}: max_v={np.max(speed):.2f}, err={error:.4f}")
                
                # Early stopping if converged
                if adaptive and error < 1e-4 and step > 100:
                    print(f"    Converged at step {step}")
                    break
        
        return self.ux, self.uy, convergence_history

@njit(parallel=True)
def add_turbulence_wake(ux, uy, buildings, intensity=0.15):
    """Add realistic turbulent wakes behind buildings"""
    ny, nx = ux.shape
    
    for i in prange(1, ny-1):
        for j in prange(1, nx-1):
            if buildings[i, j]:
                # Find wake direction
                avg_u = (ux[i-1:i+2, j-1:j+2].sum() - ux[i,j]) / 8
                avg_v = (uy[i-1:i+2, j-1:j+2].sum() - uy[i,j]) / 8
                
                # Add vortex shedding
                for k in range(1, min(20, nx-j-1)):
                    if j+k < nx:
                        # Karman vortex pattern
                        ux[i, j+k] += intensity * avg_u * np.sin(k*0.5) * np.exp(-k*0.1)
                        if i > 0 and i < ny-1:
                            uy[i-1:i+2, j+k] += intensity * avg_v * np.cos(k*0.5) * np.exp(-k*0.1)
    
    return ux, uy

def create_advanced_viz(ux, uy, buildings, params, output_path):
    """Professional CFD visualization"""
    
    ny, nx = ux.shape
    fig = plt.figure(figsize=(16, 10), facecolor='#0f172a')
    
    # Custom colormap
    from matplotlib.colors import LinearSegmentedColormap
    colors = ['#1e3a8a', '#3b82f6', '#60a5fa', '#fbbf24', '#f97316', '#dc2626']
    n_bins = 100
    cmap = LinearSegmentedColormap.from_list('wind', colors, N=n_bins)
    
    # Speed field
    speed = np.sqrt(ux**2 + uy**2)
    
    # Main plot
    ax = plt.subplot(111, facecolor='#1e293b')
    
    # Contour plot with levels
    levels = np.linspace(0, np.percentile(speed, 95), 20)
    cf = ax.contourf(speed, levels=levels, cmap=cmap, extend='max', alpha=0.9)
    
    # Streamlines
    x, y = np.meshgrid(np.arange(nx), np.arange(ny))
    
    # Adaptive streamline density
    density = [0.8, 1.2]
    strm = ax.streamplot(x, y, ux, uy, 
                         color=speed, cmap=cmap,
                         density=density, linewidth=1.5,
                         arrowsize=1.2, arrowstyle='->',
                         minlength=0.3, maxlength=4.0)
    
    # Buildings
    buildings_rgba = np.zeros((ny, nx, 4))
    buildings_rgba[..., 0] = 0.2  # Dark red
    buildings_rgba[..., 1] = 0.1
    buildings_rgba[..., 2] = 0.1
    buildings_rgba[..., 3] = buildings * 0.9
    ax.imshow(buildings_rgba, extent=[0, nx, 0, ny], origin='lower')
    
    # Colorbar
    cbar = plt.colorbar(cf, ax=ax, orientation='vertical', pad=0.02, shrink=0.8)
    cbar.set_label('Wind Speed [m/s]', color='#e2e8f0', fontsize=10)
    cbar.ax.tick_params(colors='#e2e8f0', labelsize=9)
    
    # Styling
    ax.set_xlabel('Distance [m]', color='#e2e8f0', fontsize=10)
    ax.set_ylabel('Distance [m]', color='#e2e8f0', fontsize=10)
    ax.set_title('CFD Wind Simulation - Lattice Boltzmann Method', 
                 color='#60a5fa', fontsize=12, pad=15)
    ax.tick_params(colors='#64748b', labelsize=9)
    
    # Grid
    ax.grid(True, alpha=0.1, color='#334155')
    
    # Stats box
    stats_text = f"""Max: {np.max(speed):.1f} m/s
Avg: {np.mean(speed[speed>0.1]):.1f} m/s
Buildings: {np.sum(buildings)} cells"""
    
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
            bbox=dict(boxstyle='round', facecolor='#1e293b', alpha=0.8),
            color='#e2e8f0', fontsize=9, va='top')
    
    plt.tight_layout()
    
    # Save
    viz_path = output_path.replace('.tif', '_cfd_lbm.png')
    plt.savefig(viz_path, dpi=120, bbox_inches='tight', 
                facecolor='#0f172a', edgecolor='none')
    plt.close()
    
    return viz_path

def main(config):
    """Main CFD simulation pipeline"""
    print("\n=== Enhanced LBM CFD Wind Simulation v9.0 ===")
    
    paths = config['paths']
    params = config['params']['wind']
    
    # Grid setup
    with rasterio.open(paths['nmt']) as src:
        profile = src.profile.copy()
        res = params.get('target_res', 5.0)
        scale = src.res[0] / res
        
        # Limit grid size for Colab performance
        max_size = 400  # Maximum grid dimension
        w = min(int(src.width * scale), max_size)
        h = min(int(src.height * scale), max_size)
        
        transform = src.transform * src.transform.scale(
            src.width/w, src.height/h
        )
        
        profile.update({
            'height': h, 'width': w, 
            'transform': transform,
            'dtype': 'float32',
            'compress': 'lzw'
        })
    
    print(f"Grid: {w}x{h}, Resolution: {res}m")
    
    # Load obstacles
    print("Loading obstacles...")
    
    obstacles = np.zeros((h, w), dtype=bool)
    
    # Try dedicated building model first
    if 'output_buildings_mask' in paths and os.path.exists(paths['output_buildings_mask']):
        with rasterio.open(paths['output_buildings_mask']) as src:
            mask = src.read(1, out_shape=(h, w), resampling=Resampling.nearest)
            obstacles = mask > 0.5
    else:
        # Fallback to NMPT-NMT
        nmt = None
        with rasterio.open(paths['nmt']) as src:
            nmt = src.read(1, out_shape=(h, w), resampling=Resampling.bilinear)
        
        if 'nmpt' in paths and os.path.exists(paths['nmpt']):
            with rasterio.open(paths['nmpt']) as src:
                nmpt = src.read(1, out_shape=(h, w), resampling=Resampling.bilinear)
                obstacles = (nmpt - nmt) > params.get('building_threshold', 2.5)
    
    print(f"Obstacles: {np.sum(obstacles)} cells ({100*np.sum(obstacles)/(h*w):.1f}%)")
    
    # Wind parameters
    wind_speed = params.get('wind_speed', 5.0)
    wind_dir = params.get('wind_direction', 270.0)
    
    # Convert to components
    wind_rad = np.deg2rad(wind_dir)
    ux_in = wind_speed * np.sin(wind_rad)
    uy_in = wind_speed * np.cos(wind_rad)
    
    print(f"Wind: {wind_speed:.1f} m/s @ {wind_dir:.0f}°")
    print(f"Components: U={ux_in:.2f}, V={uy_in:.2f}")
    
    # Run LBM simulation
    print("\nRunning LBM simulation...")
    
    # Reynolds number based on domain size
    Re = wind_speed * min(w, h) * res / 0.15  # kinematic viscosity ~0.15
    Re = min(Re, 1000)  # Cap for stability
    
    solver = LatticeBoltzmannCFD(w, h, Re=Re, cs=0.17)
    
    # Adaptive simulation
    steps = 500 if w*h < 50000 else 300  # Fewer steps for large grids
    
    ux, uy, convergence = solver.simulate(
        obstacles, ux_in, uy_in, 
        steps=steps, adaptive=True
    )
    
    # Post-processing
    print("\nPost-processing...")
    
    # Add turbulence
    ux, uy = add_turbulence_wake(ux, uy, obstacles, intensity=0.2)
    
    # Smooth for visualization
    ux = gaussian_filter(ux.astype(np.float32), sigma=0.5)
    uy = gaussian_filter(uy.astype(np.float32), sigma=0.5)
    
    # Compute derived fields
    speed = np.sqrt(ux**2 + uy**2)
    direction = (np.rad2deg(np.arctan2(ux, uy)) + 360) % 360
    
    # Scale to physical values
    speed *= res  # Convert to m/s physical
    
    print(f"Results: max={np.max(speed):.1f} m/s, avg={np.mean(speed[speed>0.1]):.1f} m/s")
    
    # Visualization
    print("Creating visualization...")
    viz_path = create_advanced_viz(ux, uy, obstacles.astype(float), params, 
                                  paths['output_wind_speed_raster'])
    
    # Save outputs
    print("Saving results...")
    
    with rasterio.open(paths['output_wind_speed_raster'], 'w', **profile) as dst:
        dst.write(speed.astype(np.float32), 1)
    
    with rasterio.open(paths['output_wind_dir_raster'], 'w', **profile) as dst:
        dst.write(direction.astype(np.float32), 1)
    
    # Save components for analysis
    base_path = os.path.dirname(paths['output_wind_speed_raster'])
    
    np.save(os.path.join(base_path, 'wind_ux.npy'), ux)
    np.save(os.path.join(base_path, 'wind_uy.npy'), uy)
    
    # Metadata
    metadata = {
        'method': 'Lattice Boltzmann D2Q9',
        'reynolds': float(Re),
        'grid_size': [h, w],
        'resolution_m': res,
        'wind_input': {
            'speed': wind_speed,
            'direction': wind_dir
        },
        'results': {
            'max_speed': float(np.max(speed)),
            'avg_speed': float(np.mean(speed[speed>0.1])),
            'convergence': [float(x) for x in convergence[-10:]]
        },
        'obstacles_percent': float(100*np.sum(obstacles)/(h*w))
    }
    
    with open(paths['output_wind_speed_raster'].replace('.tif', '_meta.json'), 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"\n✓ CFD simulation complete!")
    if viz_path:
        print(f"✓ Visualization: {os.path.basename(viz_path)}")
    
    return paths['output_wind_speed_raster']
