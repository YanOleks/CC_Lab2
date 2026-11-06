import sys
import io
import json
import argparse
from typing import Dict, Any, Tuple, List
import requests

if sys.platform == "win32":
    if hasattr(sys.stdin, "buffer"):
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


def search_locations(query: str) -> List[Dict[str, Any]]:
    try:
        params = {"name": query, "count": 5, "language": "uk", "format": "json"}
        resp = requests.get(GEOCODING_URL, params=params, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("results", [])
    except Exception:
        pass
    return []


def resolve_location(city_arg: str = None, lat_arg: float = None, lon_arg: float = None) -> Tuple[str, float, float]:
    if lat_arg is not None and lon_arg is not None:
        return f"Координати ({lat_arg:.2f}, {lon_arg:.2f})", lat_arg, lon_arg

    if city_arg:
        query = city_arg.strip()
    else:
        query = input("Введіть назву міста (Enter для Києва): ").strip()

    if not query:
        return "Київ (Україна)", 50.45, 30.52

    results = search_locations(query)
    if not results:
        print(f"Місто '{query}' не знайдено, використовуємо Київ.")
        return "Київ (Україна)", 50.45, 30.52

    if len(results) == 1 or city_arg:
        item = results[0]
        admin = f", {item['admin1']}" if item.get("admin1") else ""
        return f"{item.get('name')} ({item.get('country', '')}{admin})", item["latitude"], item["longitude"]

    print("\nЗнайдені варіанти:")
    for i, item in enumerate(results, 1):
        country = item.get("country", "")
        admin = f", {item['admin1']}" if item.get("admin1") else ""
        print(f"  {i}. {item.get('name')} ({country}{admin})")

    choice = input(f"\nОберіть номер [1-{len(results)}] (Enter = 1): ").strip()
    idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(results) else 0
    selected = results[idx]
    admin = f", {selected['admin1']}" if selected.get("admin1") else ""
    return f"{selected.get('name')} ({selected.get('country', '')}{admin})", selected["latitude"], selected["longitude"]


def fetch_cloud_telemetry(lat: float, lon: float) -> Dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": [
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
            "wind_gusts_10m",
        ],
        "hourly": [
            "precipitation_probability",
            "precipitation",
            "soil_moisture_0_to_1cm",
        ],
        "wind_speed_unit": "ms",
        "forecast_days": 1,
        "timezone": "auto",
    }

    try:
        response = requests.get(FORECAST_URL, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:
        print(f"\nПомилка хмарного запиту: {exc}")
        sys.exit(1)


def analyze_wind_turbine(wind_speed: float, wind_gusts: float) -> Dict[str, Any]:
    RATED_POWER_KW = 2000.0
    V_CUT_IN = 3.0
    V_RATED = 12.0
    V_CUT_OUT = 25.0
    GUST_SAFETY_LIMIT = 28.0

    if wind_speed >= V_CUT_OUT or wind_gusts >= GUST_SAFETY_LIMIT:
        return {
            "status": "Аварійна зупинка (Шторм)",
            "power_kw": 0.0,
            "load_percentage": 0.0,
            "co2_saved_kg_per_hour": 0.0,
        }

    if wind_speed < V_CUT_IN:
        return {
            "status": "Штиль (Очікування вітру)",
            "power_kw": 0.0,
            "load_percentage": 0.0,
            "co2_saved_kg_per_hour": 0.0,
        }

    if wind_speed >= V_RATED:
        current_power = RATED_POWER_KW
        status = "Повна потужність (100%)"
    else:
        ratio = (wind_speed - V_CUT_IN) / (V_RATED - V_CUT_IN)
        current_power = RATED_POWER_KW * (ratio ** 3)
        status = "Активна генерація"

    load_pct = (current_power / RATED_POWER_KW) * 100.0
    co2_saved = current_power * 0.85

    return {
        "status": status,
        "power_kw": round(current_power, 1),
        "load_percentage": round(load_pct, 1),
        "co2_saved_kg_per_hour": round(co2_saved, 1),
    }


def analyze_smart_irrigation(current_precip: float, wind_speed: float, hourly_data: Dict[str, Any]) -> Dict[str, Any]:
    prob_next_6h = hourly_data.get("precipitation_probability", [])[:6]
    rain_next_6h = hourly_data.get("precipitation", [])[:6]
    soil_moisture_list = hourly_data.get("soil_moisture_0_to_1cm", [])[:6]

    max_rain_prob = max(prob_next_6h) if prob_next_6h else 0
    expected_rain_sum = sum(rain_next_6h) if rain_next_6h else 0.0
    avg_soil_moisture = (sum(soil_moisture_list) / len(soil_moisture_list)) if soil_moisture_list else 0.25

    if current_precip > 0.0 or expected_rain_sum >= 1.0 or max_rain_prob >= 40:
        decision = "Скасовано (Очікується дощ)"
        water_saved = 200.0
        reason = f"Ймовірність опадів {max_rain_prob}%, дощ ~{expected_rain_sum:.1f} мм."
    elif wind_speed >= 7.5:
        decision = "Відкладено (Сильний вітер)"
        water_saved = 0.0
        reason = f"Вітер {wind_speed:.1f} м/с розвіюватиме воду в повітрі."
    elif avg_soil_moisture >= 0.32:
        decision = "Не потрібен (Вологий ґрунт)"
        water_saved = 150.0
        reason = f"Вологість ґрунту {avg_soil_moisture:.2f} м³/м³ у нормі."
    else:
        decision = "Рекомендовано полив"
        water_saved = 0.0
        reason = f"Сухий ґрунт ({avg_soil_moisture:.2f} м³/м³), вітер помірний."

    return {
        "decision": decision,
        "reason": reason,
        "water_saved_m3_per_ha": water_saved,
    }


def render_dashboard(location_name: str, lat: float, lon: float, data: Dict[str, Any], wind_res: Dict[str, Any], hydro_res: Dict[str, Any]) -> None:
    curr = data.get("current", {})
    t = curr.get("temperature_2m", 0.0)
    hum = curr.get("relative_humidity_2m", 0.0)
    w_spd = curr.get("wind_speed_10m", 0.0)
    w_gst = curr.get("wind_gusts_10m", 0.0)

    print("-" * 56)
    print(f" Локація:  {location_name} ({lat:.2f}, {lon:.2f})")
    print(f" Погода:   {t:+.1f}°C | Вологість: {hum:.0f}% | Вітер: {w_spd:.1f} м/с (пориви {w_gst:.1f} м/с)")
    print("-" * 56)

    print(" ВІТРОГЕНЕРАЦІЯ (Турбіна 2.0 МВт):")
    print(f"    - Статус:      {wind_res['status']}")
    print(f"    - Потужність:  {wind_res['power_kw']:.1f} кВт ({wind_res['load_percentage']:.1f}%)")
    print(f"    - Еко-ефект:   Заощадження ~{wind_res['co2_saved_kg_per_hour']:.1f} кг CO2/год")

    print("\n РОЗУМНИЙ ПОЛИВ (Водозбереження):")
    print(f"    - Рішення:     {hydro_res['decision']}")
    print(f"    - Причина:     {hydro_res['reason']}")
    if hydro_res['water_saved_m3_per_ha'] > 0:
        print(f"    - Економія:    ~{hydro_res['water_saved_m3_per_ha']:.0f} м³ води на кожен 1 га")
    print("-" * 56 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Розрахунок вітрової генерації та розумного поливу.")
    parser.add_argument("--city", type=str, help="Назва міста")
    parser.add_argument("--lat", type=float, help="Широта")
    parser.add_argument("--lon", type=float, help="Довгота")
    parser.add_argument("--json", action="store_true", help="Зберегти звіт у report.json")

    args = parser.parse_args()

    loc_name, lat, lon = resolve_location(args.city, args.lat, args.lon)
    raw_telemetry = fetch_cloud_telemetry(lat, lon)

    curr = raw_telemetry.get("current", {})
    hourly = raw_telemetry.get("hourly", {})

    wind_analysis = analyze_wind_turbine(
        wind_speed=curr.get("wind_speed_10m", 0.0),
        wind_gusts=curr.get("wind_gusts_10m", 0.0),
    )

    irrigation_analysis = analyze_smart_irrigation(
        current_precip=curr.get("precipitation", 0.0),
        wind_speed=curr.get("wind_speed_10m", 0.0),
        hourly_data=hourly,
    )

    render_dashboard(loc_name, lat, lon, raw_telemetry, wind_analysis, irrigation_analysis)

    if args.json:
        report_data = {
            "location": loc_name,
            "coordinates": {"latitude": lat, "longitude": lon},
            "telemetry": curr,
            "wind_power": wind_analysis,
            "irrigation": irrigation_analysis,
        }
        with open("report.json", "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        print("Звіт збережено у 'report.json'.\n")


if __name__ == "__main__":
    main()
