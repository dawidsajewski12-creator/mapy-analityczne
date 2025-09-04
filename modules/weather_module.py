# -*- coding: utf-8 -*-
"""
Modular Weather & Climate System v2.0
Real-time data integration with forecast capabilities
"""

import requests
import numpy as np
from datetime import datetime, timedelta
import pytz
import json
from typing import Dict, List, Optional, Tuple
import pandas as pd

class WeatherAPI:
    """Multi-source weather data aggregator"""
    
    def __init__(self, lat: float, lon: float, timezone: str = "Europe/Warsaw"):
        self.lat = lat
        self.lon = lon
        self.timezone = timezone
        self.cache = {}
        
    def get_current_weather(self) -> Dict:
        """Get current weather from multiple sources"""
        
        weather = {}
        
        # Open-Meteo (primary)
        try:
            url = (f"https://api.open-meteo.com/v1/forecast?"
                   f"latitude={self.lat}&longitude={self.lon}"
                   f"&current=temperature_2m,relative_humidity_2m,pressure_msl,"
                   f"wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                   f"cloud_cover,precipitation,weather_code"
                   f"&timezone={self.timezone}")
            
            response = requests.get(url, timeout=5)
            if response.ok:
                data = response.json()['current']
                weather.update({
                    'temperature': data.get('temperature_2m', 20),
                    'humidity': data.get('relative_humidity_2m', 50),
                    'pressure': data.get('pressure_msl', 1013),
                    'wind_speed': data.get('wind_speed_10m', 0) / 3.6,  # m/s
                    'wind_direction': data.get('wind_direction_10m', 0),
                    'wind_gusts': data.get('wind_gusts_10m', 0) / 3.6,
                    'cloud_cover': data.get('cloud_cover', 0),
                    'precipitation': data.get('precipitation', 0),
                    'weather_code': data.get('weather_code', 0),
                    'source': 'open-meteo',
                    'timestamp': datetime.now(pytz.timezone(self.timezone))
                })
        except Exception as e:
            print(f"Weather API error: {e}")
            
        # Fallback defaults
        if not weather:
            weather = self.get_seasonal_defaults()
            
        return weather
    
    def get_forecast(self, hours: int = 48) -> pd.DataFrame:
        """Get hourly forecast"""
        
        try:
            url = (f"https://api.open-meteo.com/v1/forecast?"
                   f"latitude={self.lat}&longitude={self.lon}"
                   f"&hourly=temperature_2m,precipitation_probability,"
                   f"precipitation,wind_speed_10m,wind_direction_10m"
                   f"&forecast_days={max(1, hours//24 + 1)}"
                   f"&timezone={self.timezone}")
            
            response = requests.get(url, timeout=5)
            if response.ok:
                data = response.json()['hourly']
                
                df = pd.DataFrame({
                    'time': pd.to_datetime(data['time']),
                    'temperature': data['temperature_2m'],
                    'precip_prob': data['precipitation_probability'],
                    'precipitation': data['precipitation'],
                    'wind_speed': np.array(data['wind_speed_10m']) / 3.6,
                    'wind_direction': data['wind_direction_10m']
                })
                
                return df.iloc[:hours]
                
        except Exception as e:
            print(f"Forecast error: {e}")
            
        return pd.DataFrame()
    
    def get_seasonal_defaults(self) -> Dict:
        """Season-based default weather"""
        
        month = datetime.now().month
        
        # Seasonal profiles for Poland
        if month in [12, 1, 2]:  # Winter
            defaults = {
                'temperature': 0, 'humidity': 80, 'wind_speed': 6,
                'wind_direction': 270, 'cloud_cover': 70
            }
        elif month in [3, 4, 5]:  # Spring
            defaults = {
                'temperature': 12, 'humidity': 65, 'wind_speed': 5,
                'wind_direction': 225, 'cloud_cover': 50
            }
        elif month in [6, 7, 8]:  # Summer
            defaults = {
                'temperature': 22, 'humidity': 60, 'wind_speed': 4,
                'wind_direction': 180, 'cloud_cover': 30
            }
        else:  # Autumn
            defaults = {
                'temperature': 10, 'humidity': 75, 'wind_speed': 5.5,
                'wind_direction': 315, 'cloud_cover': 60
            }
        
        defaults.update({
            'pressure': 1013,
            'wind_gusts': defaults['wind_speed'] * 1.5,
            'precipitation': 0,
            'weather_code': 0,
            'source': 'seasonal_default',
            'timestamp': datetime.now(pytz.timezone(self.timezone))
        })
        
        return defaults
    
    def get_extreme_scenarios(self) -> Dict[str, Dict]:
        """Get extreme weather scenarios for testing"""
        
        return {
            'heatwave': {
                'temperature': 38, 'humidity': 30, 'wind_speed': 2,
                'wind_direction': 180, 'cloud_cover': 0
            },
            'storm': {
                'temperature': 18, 'humidity': 95, 'wind_speed': 20,
                'wind_direction': 45, 'cloud_cover': 100,
                'precipitation': 50  # mm/hr
            },
            'cold_snap': {
                'temperature': -15, 'humidity': 60, 'wind_speed': 8,
                'wind_direction': 0, 'cloud_cover': 40
            },
            'fog': {
                'temperature': 8, 'humidity': 98, 'wind_speed': 1,
                'wind_direction': 90, 'cloud_cover': 100
            }
        }

class ClimateAnalyzer:
    """Climate analysis and projections"""
    
    def __init__(self, lat: float, lon: float):
        self.lat = lat
        self.lon = lon
        
    def get_climate_normals(self) -> Dict:
        """30-year climate normals"""
        
        # Simplified climate data for Poland
        return {
            'annual': {
                'temp_mean': 8.5,
                'temp_min': -2.0,
                'temp_max': 19.0,
                'precipitation': 600,  # mm/year
                'wind_speed': 4.5
            },
            'monthly': self._get_monthly_normals()
        }
    
    def _get_monthly_normals(self) -> List[Dict]:
        """Monthly climate normals"""
        
        # Temperature and precipitation curves for temperate climate
        months = []
        for m in range(1, 13):
            temp = 8 + 12 * np.sin((m - 1) * np.pi / 6 - np.pi/2)
            precip = 50 + 20 * np.sin((m - 6) * np.pi / 6)
            
            months.append({
                'month': m,
                'temp_mean': round(temp, 1),
                'precipitation': round(precip, 1),
                'wind_speed': 4 + 2 * np.cos((m - 1) * np.pi / 6)
            })
        
        return months
    
    def calculate_heat_index(self, temp: float, humidity: float) -> float:
        """Calculate heat index"""
        
        if temp < 27:  # No heat index below 27°C
            return temp
            
        # Rothfusz equation
        T, RH = temp, humidity
        HI = (-42.379 + 2.04901523*T + 10.14333127*RH 
              - 0.22475541*T*RH - 0.00683783*T**2 
              - 0.05481717*RH**2 + 0.00122874*T**2*RH 
              + 0.00085282*T*RH**2 - 0.00000199*T**2*RH**2)
        
        return round(HI, 1)
    
    def calculate_wind_chill(self, temp: float, wind_speed: float) -> float:
        """Calculate wind chill"""
        
        if temp > 10 or wind_speed < 1.3:
            return temp
        
        # Wind chill formula
        v_kmh = wind_speed * 3.6
        WC = 13.12 + 0.6215*temp - 11.37*(v_kmh**0.16) + 0.3965*temp*(v_kmh**0.16)
        
        return round(WC, 1)
    
    def calculate_comfort_index(self, weather: Dict) -> Dict:
        """Calculate various comfort indices"""
        
        T = weather.get('temperature', 20)
        RH = weather.get('humidity', 50)
        v = weather.get('wind_speed', 3)
        
        # Apparent temperature
        if T > 27:
            apparent_temp = self.calculate_heat_index(T, RH)
        elif T < 10:
            apparent_temp = self.calculate_wind_chill(T, v)
        else:
            apparent_temp = T
        
        # Discomfort index
        DI = T - 0.55 * (1 - 0.01*RH) * (T - 14.5)
        
        # Thermal sensation
        if apparent_temp < -40:
            sensation = "extreme_cold"
        elif apparent_temp < -20:
            sensation = "very_cold"
        elif apparent_temp < 0:
            sensation = "cold"
        elif apparent_temp < 10:
            sensation = "cool"
        elif apparent_temp < 20:
            sensation = "comfortable"
        elif apparent_temp < 27:
            sensation = "warm"
        elif apparent_temp < 32:
            sensation = "hot"
        elif apparent_temp < 41:
            sensation = "very_hot"
        else:
            sensation = "extreme_heat"
        
        return {
            'apparent_temperature': apparent_temp,
            'discomfort_index': round(DI, 1),
            'thermal_sensation': sensation,
            'heat_index': self.calculate_heat_index(T, RH) if T > 27 else None,
            'wind_chill': self.calculate_wind_chill(T, v) if T < 10 else None
        }

class WeatherEffects:
    """Weather effects on urban systems"""
    
    @staticmethod
    def wind_profile(height: float, wind_10m: float, 
                     terrain: str = "urban") -> float:
        """Wind speed at height using power law"""
        
        # Roughness parameters
        alpha = {
            'urban': 0.4,
            'suburban': 0.28,
            'rural': 0.16,
            'water': 0.11
        }.get(terrain, 0.28)
        
        return wind_10m * (height / 10) ** alpha
    
    @staticmethod
    def rain_intensity_to_runoff(intensity: float, duration: float,
                                surface: str = "concrete") -> float:
        """Convert rain to runoff coefficient"""
        
        # Runoff coefficients
        C = {
            'concrete': 0.95,
            'asphalt': 0.90,
            'gravel': 0.50,
            'grass': 0.30,
            'forest': 0.20
        }.get(surface, 0.50)
        
        # Adjust for intensity
        if intensity > 50:  # Heavy rain
            C = min(1.0, C * 1.2)
        
        return C * intensity
    
    @staticmethod
    def solar_radiation(lat: float, day_of_year: int, 
                       hour: float, cloud_cover: float) -> float:
        """Calculate solar radiation W/m²"""
        
        # Solar constant
        S0 = 1367
        
        # Declination angle
        delta = 23.45 * np.sin(np.radians(360 * (284 + day_of_year) / 365))
        
        # Hour angle
        h_angle = 15 * (hour - 12)
        
        # Solar elevation
        elevation = np.arcsin(
            np.sin(np.radians(lat)) * np.sin(np.radians(delta)) +
            np.cos(np.radians(lat)) * np.cos(np.radians(delta)) * 
            np.cos(np.radians(h_angle))
        )
        
        if elevation <= 0:
            return 0
        
        # Atmospheric transmission
        m = 1 / np.sin(elevation)  # Air mass
        tau = 0.75 ** m  # Transmission coefficient
        
        # Cloud reduction
        cloud_factor = 1 - 0.75 * (cloud_cover / 100)
        
        # Direct radiation
        radiation = S0 * np.sin(elevation) * tau * cloud_factor
        
        return max(0, radiation)
    
    @staticmethod
    def urban_heat_island(base_temp: float, time: datetime,
                         building_density: float) -> float:
        """Calculate UHI effect"""
        
        hour = time.hour
        
        # Diurnal UHI pattern
        if 22 <= hour or hour < 6:  # Night - maximum UHI
            uhi_factor = 1.0
        elif 6 <= hour < 10:  # Morning - decreasing
            uhi_factor = 0.8 - 0.2 * (hour - 6) / 4
        elif 10 <= hour < 16:  # Day - minimum
            uhi_factor = 0.3
        elif 16 <= hour < 22:  # Evening - increasing
            uhi_factor = 0.3 + 0.7 * (hour - 16) / 6
        else:
            uhi_factor = 0.5
        
        # Maximum UHI based on density
        max_uhi = 4 * building_density  # up to 4°C for dense urban
        
        return base_temp + max_uhi * uhi_factor

class IntegratedWeatherSystem:
    """Complete weather integration for GIS"""
    
    def __init__(self, config: Dict):
        self.config = config
        loc = config['location']
        
        self.weather_api = WeatherAPI(loc['latitude'], loc['longitude'], 
                                     loc['timezone'])
        self.climate = ClimateAnalyzer(loc['latitude'], loc['longitude'])
        self.effects = WeatherEffects()
        
        self.current_weather = None
        self.forecast = None
        
    def update(self) -> Dict:
        """Update all weather data"""
        
        # Get current conditions
        self.current_weather = self.weather_api.get_current_weather()
        
        # Add comfort indices
        comfort = self.climate.calculate_comfort_index(self.current_weather)
        self.current_weather.update(comfort)
        
        # Get forecast
        self.forecast = self.weather_api.get_forecast(48)
        
        return self.current_weather
    
    def get_simulation_params(self, scenario: str = "current") -> Dict:
        """Get parameters for simulations"""
        
        if scenario == "current":
            weather = self.current_weather or self.weather_api.get_current_weather()
        else:
            # Use extreme scenario
            scenarios = self.weather_api.get_extreme_scenarios()
            weather = scenarios.get(scenario, scenarios['storm'])
        
        # Calculate derived parameters
        params = {
            'wind': {
                'speed': weather['wind_speed'],
                'direction': weather['wind_direction'],
                'gusts': weather.get('wind_gusts', weather['wind_speed'] * 1.5),
                'profile': [self.effects.wind_profile(h, weather['wind_speed']) 
                          for h in [2, 10, 50, 100]]
            },
            'thermal': {
                'air_temperature': weather['temperature'],
                'humidity': weather['humidity'],
                'apparent_temp': weather.get('apparent_temperature', weather['temperature']),
                'solar_radiation': self.effects.solar_radiation(
                    self.config['location']['latitude'],
                    datetime.now().timetuple().tm_yday,
                    datetime.now().hour,
                    weather.get('cloud_cover', 50)
                )
            },
            'precipitation': {
                'intensity': weather.get('precipitation', 0),
                'runoff_urban': self.effects.rain_intensity_to_runoff(
                    weather.get('precipitation', 0), 1, 'concrete'
                ),
                'runoff_green': self.effects.rain_intensity_to_runoff(
                    weather.get('precipitation', 0), 1, 'grass'
                )
            }
        }
        
        return params
    
    def generate_report(self) -> str:
        """Generate weather report"""
        
        if not self.current_weather:
            self.update()
        
        w = self.current_weather
        
        report = f"""
        === WEATHER REPORT ===
        Time: {w['timestamp'].strftime('%Y-%m-%d %H:%M')}
        Location: {self.config['location']['latitude']:.2f}°N, {self.config['location']['longitude']:.2f}°E
        
        CURRENT CONDITIONS:
        • Temperature: {w['temperature']:.1f}°C (feels like {w.get('apparent_temperature', w['temperature']):.1f}°C)
        • Humidity: {w['humidity']}%
        • Wind: {w['wind_speed']:.1f} m/s from {w['wind_direction']}°
        • Pressure: {w.get('pressure', 1013):.1f} hPa
        • Cloud Cover: {w.get('cloud_cover', 0)}%
        
        COMFORT:
        • Thermal Sensation: {w.get('thermal_sensation', 'comfortable')}
        • Discomfort Index: {w.get('discomfort_index', 0):.1f}
        """
        
        if w.get('heat_index'):
            report += f"        • Heat Index: {w['heat_index']:.1f}°C\n"
        if w.get('wind_chill'):
            report += f"        • Wind Chill: {w['wind_chill']:.1f}°C\n"
        
        return report
    
    def export_for_web(self) -> Dict:
        """Export weather data for web interface"""
        
        if not self.current_weather:
            self.update()
        
        return {
            'current': {
                'temperature': self.current_weather['temperature'],
                'humidity': self.current_weather['humidity'],
                'wind_speed': self.current_weather['wind_speed'],
                'wind_direction': self.current_weather['wind_direction'],
                'apparent_temp': self.current_weather.get('apparent_temperature'),
                'condition': self.current_weather.get('thermal_sensation'),
                'timestamp': self.current_weather['timestamp'].isoformat()
            },
            'forecast': self.forecast.to_dict('records') if self.forecast is not None else [],
            'climate': self.climate.get_climate_normals(),
            'extremes': self.weather_api.get_extreme_scenarios()
        }

# Integration function for existing code
def integrate_weather_system(config: Dict) -> IntegratedWeatherSystem:
    """Create and initialize weather system"""
    
    weather_system = IntegratedWeatherSystem(config)
    weather_system.update()
    
    print(weather_system.generate_report())
    
    return weather_system
