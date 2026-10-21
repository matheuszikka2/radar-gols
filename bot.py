
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

API_KEY = os.environ["API_FOOTBALL_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE_URL = "https://v3.football.api-sports.io"
STATE_FILE = "seen.json"

# Limite de segurança: no máximo 95 pedidos à API por dia.
DAILY_API_LIMIT = 95

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    # Compatibilidade com o ficheiro antigo, que podia conter uma lista.
    if isinstance(data, list):
        data = {"alerted": data}

    today = datetime.now(timezone.utc).date().isoformat()

    if data.get("date") != today:
        data = {"date": today, "requests": 0, "alerted": []}

    data.setdefault("requests", 0)
    data.setdefault("alerted", [])
    return data

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def api_get(endpoint, params=None):
    if state["requests"] >= DAILY_API_LIMIT:
        print("Limite diário de segurança atingido.")
        return None

    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}/{endpoint}"
    if query:
        url += f"?{query}"

    request = urllib.request.Request(
        url,
        headers={"x-apisports-key": API_KEY}
    )

    # Conta cada tentativa para evitar ultrapassar o limite.
    state["requests"] += 1
    save_state(state)

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            result = json.loads(response.read().decode("utf-8"))

        errors = result.get("errors")
        if errors:
            print(f"Erro da API: {errors}")
            return None

        return result.get("response", [])

    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"Falha no pedido à API: {exc}")
        return None

def telegram_send(message):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": message
    }).encode()

    request = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            print(f"Telegram não confirmou o envio: {result}")
            return False

        print("Mensagem enviada ao Telegram.")
        return True

    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"Falha ao enviar mensagem ao Telegram: {exc}")
        return False

def get_number(stats, *names):
    for item in stats:
        if item.get("type", "").strip().lower() in {
            name.lower() for name in names
        }:
            value = item.get("value")
            if isinstance(value, str) and value.endswith("%"):
                value = value[:-1]
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None

def get_stats(fixture_id):
    response = api_get("fixtures/statistics", {"fixture": fixture_id})
    if not response or len(response) < 2:
        return None

    home_stats = response[0].get("statistics", [])
    away_stats = response[1].get("statistics", [])

    home = {
        "shots": get_number(home_stats, "Total Shots"),
        "target": get_number(home_stats, "Shots on Goal", "Shots on Target"),
        "corners": get_number(home_stats, "Corner Kicks"),
        "xg": get_number(home_stats, "Expected Goals", "xG")
    }
    away = {
        "shots": get_number(away_stats, "Total Shots"),
        "target": get_number(away_stats, "Shots on Goal", "Shots on Target"),
        "corners": get_number(away_stats, "Corner Kicks"),
        "xg": get_number(away_stats, "Expected Goals", "xG")
    }

    # Sem estes dados essenciais, não classifica o jogo.
    required = ("shots", "target", "corners")
    if any(home[k] is None or away[k] is None for k in required):
        print(f"Estatísticas incompletas no jogo {fixture_id}.")
        return None

    return home, away

def analyse(stats):
    home, away = stats

    total_shots = home["shots"] + away["shots"]
    total_target = home["target"] + away["target"]
    total_corners = home["corners"] + away["corners"]

    # xG é opcional: só calculamos o total se existir para ambas.
    xg_available = home["xg"] is not None and away["xg"] is not None
    total_xg = home["xg"] + away["xg"] if xg_available else None

    very_strong = (
        total_shots >= 15
        and total_target >= 6
        and total_corners >= 6
        and home["target"] >= 2
        and away["target"] >= 2
        and (not xg_available or total_xg >= 1.5)
    )

    strong = (
        total_shots >= 12
        and total_target >= 5
        and total_corners >= 5
        and max(home["target"], away["target"]) >= 3
        and (not xg_available or total_xg >= 0.8)
    )

    if very_strong:
        level = "MUITO FORTE"
    elif strong:
        level = "FORTE"
    else:
        return None

    xg_text = (
        f"{total_xg:.2f}"
        if total_xg is not None
        else "indisponível"
    )

    return (
        f"🚨 ALERTA {level}\n"
        f"Jogo 0-0 ao intervalo\n\n"
        f"Remates: {total_shots:.0f}\n"
        f"Remates à baliza: {total_target:.0f}\n"
        f"Cantos: {total_corners:.0f}\n"
        f"xG total: {xg_text}\n\n"
        "Sinal estatístico, não garantia de golo."
    )

def main():
    print("Radar iniciado:", datetime.now(timezone.utc).isoformat())
    print(f"Pedidos usados hoje: {state['requests']}/{DAILY_API_LIMIT}")

    fixtures = api_get("fixtures", {"live": "all"})
    if fixtures is None:
        print("Não foi possível consultar os jogos.")
        return

    candidates = [
        f for f in fixtures
        if f.get("fixture", {}).get("status", {}).get("short") == "HT"
        and f.get("goals", {}).get("home") == 0
        and f.get("goals", {}).get("away") == 0
    ]

    print(f"Jogos 0-0 ao intervalo encontrados: {len(candidates)}")

    for game in candidates:
        fixture = game.get("fixture", {})
        fixture_id = str(fixture.get("id", ""))

        if not fixture_id or fixture_id in state["alerted"]:
            continue

        if state["requests"] >= DAILY_API_LIMIT:
            print("Limite diário atingido; restantes jogos ficam por analisar.")
            break

        stats = get_stats(fixture_id)
        if stats is None:
            continue

        message = analyse(stats)
        if not message:
            continue

        home = game.get("teams", {}).get("home", {}).get("name", "Casa")
        away = game.get("teams", {}).get("away", {}).get("name", "Fora")
        kickoff = fixture.get("date", "")

        full_message = (
            f"{message}\n\n{home} - {away}\n"
            f"Início: {kickoff}\nID: {fixture_id}"
        )

        if telegram_send(full_message):
            state["alerted"].append(fixture_id)
            save_state(state)
            print(f"Alerta enviado: {home} - {away}")

    print(f"Pedidos usados hoje: {state['requests']}/{DAILY_API_LIMIT}")
    save_state(state)

state = load_state()

if __name__ == "__main__":
    main()
