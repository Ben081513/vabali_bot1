import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import requests

# ==========================================
# FESTE KONFIGURATION (Hardcoded)
# ==========================================
START_DATUM = "2026-09-27"
END_DATUM = "2026-09-27"

ZEIT_MIN = "10:00"
ZEIT_MAX = "16:00"
ANZAHL_PERSONEN = "2"

# WICHTIG: Trage hier deinen ntfy-Namen ein
NTFY_TOPIC = "vabali_bot"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

# ==========================================

BASE_URL = "https://www.vabali.de/berlin/reservierung/"
PROXY_URL = "https://wellness.vs.sparkleapp.sparkle.plus/proxy.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"
}


def send_ntfy_notification(title, message, tags="bath"):
    """Sendet eine Push-Benachrichtigung via ntfy.sh."""
    if not NTFY_TOPIC or NTFY_TOPIC == "DEIN_NTFY_NAME_HIER":
        print("NTFY_TOPIC wurde nicht angepasst. Überspringe Benachrichtigung.")
        return
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title, "Tags": tags, "Priority": "default"}
        )
    except Exception as e:
        print(f"Fehler beim Senden der ntfy-Nachricht: {e}")


def get_session_data():
    """Sucht aggressiv nach API-Key und Session-Key im gesamten Quelltext."""
    session = requests.Session()
    response = session.get(BASE_URL, headers=HEADERS)

    apikey = None
    key = None

    # STRATEGIE 1: Suche direkt im HTML der Hauptseite nach den Keys
    ap_match = re.search(r'apikey=([a-zA-Z0-9]+)', response.text)
    k_match = re.search(r'key=([a-zA-Z0-9]+)', response.text)
    
    if ap_match: apikey = ap_match.group(1)
    if k_match: key = k_match.group(1)

    # STRATEGIE 2: Suche nach einem beliebigen Sparkle-Link und folge ihm
    if not apikey or not key:
        sparkle_link = re.search(r'(https?://[^"\']*sparkle[^"\']*)', response.text)
        if sparkle_link:
            iframe_url = sparkle_link.group(1)
            
            # Prüfe, ob die Keys schon direkt im gefundenen Link stehen
            parsed = parse_qs(urlparse(iframe_url).query)
            if not apikey and 'apikey' in parsed: apikey = parsed['apikey'][0]
            if not key and 'key' in parsed: key = parsed['key'][0]
            
            # Wenn immer noch nicht, lade den gefundenen Link herunter und suche dort
            if not apikey or not key:
                iframe_res = session.get(iframe_url, headers=HEADERS)
                ap_match_if = re.search(r'apikey=([a-zA-Z0-9]+)', iframe_res.text)
                k_match_if = re.search(r'key=([a-zA-Z0-9]+)', iframe_res.text)
                if ap_match_if: apikey = ap_match_if.group(1)
                if k_match_if: key = k_match_if.group(1)

    if not apikey or not key:
        print(f"DEBUG HTML SNIPPET: {response.text[:500]}")
        raise ValueError("Konnte API-Keys nicht finden. Entweder hat Vabali die Seite stark umgebaut oder einen Bot-Schutz (z.B. Cloudflare) aktiviert.")
        
    return session, apikey, key


def generate_date_range(start_str, end_str):
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    
    dates = []
    curr = start
    while curr <= end:
        dates.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)
    return dates


def is_time_in_range(time_str, min_time, max_time):
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
        t_min = datetime.strptime(min_time, "%H:%M").time()
        t_max = datetime.strptime(max_time, "%H:%M").time()
        return t_min <= t <= t_max
    except ValueError:
        return True


def check_vabali():
    dates_to_check = generate_date_range(START_DATUM, END_DATUM)
    found_slots = {}

    try:
        session, dyn_apikey, dyn_key = get_session_data()
        print(f"Erfolgreich autorisiert. (Key: {dyn_key[:5]}...)")
    except Exception as e:
        err_msg = f"Fehler bei der Vabali-Verbindung: {e}"
        print(err_msg)
        send_ntfy_notification("Vabali Checker Fehler", err_msg, tags="warning")
        return

    for date_str in dates_to_check:
        query_params = {
            "key": dyn_key,
            "apikey": dyn_apikey,
            "modul": "sparkleTicketingOnline",
            "file": "ajaxResponder.php",
            "action": "getPossibleUhrzeiten" 
        }

        payload = {
            "datum": date_str,
            "bereich": "",
            "Artikel_ID": "2948",
            "anzahlPersonen": str(ANZAHL_PERSONEN)
        }

        try:
            res = session.post(PROXY_URL, params=query_params, data=payload, headers=HEADERS)
            json_data = res.json()
            
            print(f"DEBUG-ANTWORT: {json_data}")

            if json_data.get("success"):
                raw_data = json_data.get("data", {})
                uhrzeiten_raw = raw_data.get("uhrzeiten") or raw_data.get("slots") or raw_data.get("timeSlots") or []
                matching_times = []

                if isinstance(uhrzeiten_raw, list):
                    for item in uhrzeiten_raw:
                        if isinstance(item, dict):
                            t_str = item.get("uhrzeit") or item.get("time") or item.get("label")
                            is_avail = item.get("available", True) and not item.get("ausgebucht", False)
                        else:
                            t_str = str(item)
                            is_avail = True

                        if t_str and is_avail and is_time_in_range(t_str, ZEIT_MIN, ZEIT_MAX):
                            matching_times.append(t_str)

                elif isinstance(uhrzeiten_raw, dict):
                    for t_str, info in uhrzeiten_raw.items():
                        is_avail = info.get("available", True) if isinstance(info, dict) else bool(info)
                        if is_avail and is_time_in_range(t_str, ZEIT_MIN, ZEIT_MAX):
                            matching_times.append(t_str)

                if matching_times:
                    found_slots[date_str] = matching_times

        except Exception as e:
            print(f"Fehler bei der Abfrage für Datum {date_str}: {e}")

    if found_slots:
        title = "🎉 Vabali Plätze frei!"
        lines = [f"Freie Slots für {ANZAHL_PERSONEN} Personen ({ZEIT_MIN} - {ZEIT_MAX} Uhr):\n"]
        for d, times in found_slots.items():
            lines.append(f"• {d}: {', '.join(times)}")

        msg = "\n".join(lines)
        print(msg)
        send_ntfy_notification(title, msg, tags="tada,bath")
    else:
        title = "ℹ️ Vabali Checker: Keine Plätze"
        msg = (f"Keine freien Plätze für {ANZAHL_PERSONEN} Personen "
               f"am {START_DATUM} zwischen {ZEIT_MIN} und {ZEIT_MAX} Uhr gefunden.")
        print(msg)
        send_ntfy_notification(title, msg, tags="x,bath")


if __name__ == "__main__":
    check_vabali()
