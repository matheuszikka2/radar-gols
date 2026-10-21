
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ["API_FOOTBALL_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE_URL = "https://v3.football.api-sports.io"
MAX_STATS_REQUESTS = 40
STATE_FILE = "seen.json"


def api_get(endpoint, params=None):
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}/{endpoint}"
    if query:
        url += f"?{query}"

    request = urllib.request.Request(
        url, headers={"x-apisports-key": API_KEY}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def telegram_send(message):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": message,
    }).encode()

    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def number(value):
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def get_stats(fixture_id):
    data = api_get("fixtures/statistics", {"fixture": fixture_id})
    teams = {}

    for entry in data.get("response", []):
        team = entry.get("team", {}).get("name", "Equipa")
        values = {}
        for stat in entry.get("statistics", []):
            values[stat.get("type", "")] = number(stat.get("value"))
        teams[team] = values

    return list(teams.items())


def stat(stats, *names):
    for name in names:
        value = stats.get(name)
        if value is not None:
            return value
    return 0


def analyse(teams):
    if len(teams) != 2:
        return None

    (name1, a), (name2, b) = teams

    shots = (
        stat(a, "Total Shots") + stat(b, "Total Shots")
    )
    on_target_a = stat(a, "Shots on Goal", "Shots on Target")
    on_target_b = stat(b, "Shots on Goal", "Shots on Target")
    on_target = on_target_a + on_target_b
    corners = stat(a, "Corner Kicks") + stat(b, "Corner Kicks")

    # O xG é opcional: só é usado se vier nos dados da API.
    xg_a = number(a.get("Expected Goals"))
    xg_b = number(b.get("Expected Goals"))
    xg_total = (
        xg_a + xg_b
        if xg_a is not None and xg_b is not None
        else None
    )

    very_strong = (
        shots >= 15
        and on_target >= 6
        and corners >= 6
        and on_target_a >= 2
        and on_target_b >= 2
        and (xg_total is None or xg_total >= 1.5)
    )

    strong = (
        shots >= 12
        and on_target >= 5
        and corners >= 5
        and max(on_target_a, on_target_b) >= 3
    )

    if very_strong:
        level = "MUITO FORTE"
    elif strong:
        level = "FORTE"
    else:
        return None

    # Distribuição ofensiva: uma equipa pode dominar
    # ou as duas podem criar perigo.
    xg_text = (
        f"\nxG total: {xg_total:.2f}"
        if xg_total is not None
        else "\nxG: indisponível"
    )

    return (
        f"🚨 RADAR DE GOLS — {level}\n"
        f"{name1} 0–0 {name2}\n"
        f"Remates: {int(shots)} | À baliza: {int(on_target)}\n"
        f"Cantos: {int(corners)}"
        f"{xg_text}\n"
        "⚽ Sinal estatístico para a 2.ª parte; "
        "não garante golos nem lucro."
    )


def load_seen():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return set(json.load(file))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(sorted(seen), file, ensure_ascii=False, indent=2)


def main():
    seen = load_seen()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Radar iniciado: {now}")

    fixtures = api_get("fixtures", {"live": "all"}).get("response", [])
    candidates = [
        f for f in fixtures
        if f.get("fixture", {}).get("status", {}).get("short") == "HT"
        and f.get("goals", {}).get("home") == 0
        and f.get("goals", {}).get("away") == 0
        and str(f.get("fixture", {}).get("id")) not in seen
    ]

    # Limite de pedidos de estatísticas por execução.
    for fixture in candidates[:MAX_STATS_REQUESTS]:
        fixture_info = fixture["fixture"]
        fixture_id = str(fixture_info["id"])
        teams = get_stats(fixture_info["id"])
        message = analyse(teams)

        if message:
            home = fixture.get("teams", {}).get("home", {}).get("name", "")
            away = fixture.get("teams", {}).get("away", {}).get("name", "")
            kickoff = fixture_info.get("date", "")
            telegram_send(
                f"{message}\nInício: {kickoff}\nID do jogo: {fixture_id}"
            )
            print(f"Alerta enviado: {home} - {away}")

        # Não volta a analisar este jogo em execuções futuras.
        seen.add(fixture_id)

    save_seen(seen)
    print(f"Jogos ao intervalo analisados: {len(candidates[:MAX_STATS_REQUESTS])}")


if __name__ == "__main__":
    main()

