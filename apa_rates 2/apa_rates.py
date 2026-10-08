#!/usr/bin/env python3
"""
アパホテル料金トラッカー（楽天トラベルAPI版）

使い方:
  python3 apa_rates.py hotels   # 全国のアパホテル一覧を取得 → data/hotels.json
  python3 apa_rates.py fetch    # 対象ホテルの日別最安値を取得 → data/prices/年-月.csv に追記
  python3 apa_rates.py report   # ダッシュボードを生成 → dashboard.html
  python3 apa_rates.py all      # 上の3つを順に実行（ホテル一覧は7日以上古い時だけ再取得）

設定は同じフォルダの config.json に書きます（初回実行時にひな形を作成します）。
Python 3.8 以上、追加インストール不要です。
"""
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
CONFIG_PATH = os.path.join(BASE, "config.json")
HOTELS_PATH = os.path.join(DATA, "hotels.json")
PRICES_DIR = os.path.join(DATA, "prices")
LEGACY_PRICES_PATH = os.path.join(DATA, "prices.csv")
REPORT_PATH = os.environ.get("APA_REPORT_PATH", os.path.join(BASE, "dashboard.html"))
REPORT_SNAPSHOTS = 14   # ダッシュボードに詳細を載せる直近の取得回数
TEMPLATE_PATH = os.path.join(BASE, "dashboard_template.html")

API_HOST = os.environ.get("RAKUTEN_API_HOST", "https://openapi.rakuten.co.jp")
KEYWORD_URL = API_HOST + "/engine/api/Travel/KeywordHotelSearch/20260731"
VACANT_URL = API_HOST + "/engine/api/Travel/VacantHotelSearch/20170426"

DEFAULT_CONFIG = {
    "application_id": "ここに楽天のアプリIDを入れる",
    "access_key": "ここにアクセスキーを入れる",
    "origin": "https://example.com",
    "_origin_note": "楽天のアプリ設定で登録した『許可されたWebサイト』と同じURLにしてください",
    "keyword": "アパホテル",
    "fetch_scope": ["福岡市", "東京23区"],
    "_fetch_scope_note": "料金を取る範囲。エリア名（福岡市・東京23区・大阪市など）か都道府県名を並べる。全国なら [\"全国\"]",
    "days_ahead": 45,
    "start_offset_days": 0,
    "adults": 1,
    "request_interval_sec": 1.2,
}

CITY_AREAS = {
    # 都道府県: [(エリア名, address2 の先頭パターン)]
    "福岡県": [("福岡市", r"^福岡市"), ("北九州市", r"^北九州市")],
    "大阪府": [("大阪市", r"^大阪市")],
    "愛知県": [("名古屋市", r"^名古屋市")],
    "北海道": [("札幌市", r"^札幌市")],
    "京都府": [("京都市", r"^京都市")],
    "神奈川県": [("横浜市", r"^横浜市"), ("川崎市", r"^川崎市")],
    "兵庫県": [("神戸市", r"^神戸市")],
    "宮城県": [("仙台市", r"^仙台市")],
    "広島県": [("広島市", r"^広島市")],
}


JST = dt.timezone(dt.timedelta(hours=9))


def now_jst():
    return dt.datetime.now(JST).replace(tzinfo=None)


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- config
ENV_KEYS = {"application_id": "RAKUTEN_APP_ID", "access_key": "RAKUTEN_ACCESS_KEY", "origin": "RAKUTEN_ORIGIN"}


def load_config():
    on_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    if not os.path.exists(CONFIG_PATH) and not on_ci:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        log(f"config.json を作成しました。アプリID・アクセスキー・origin を書き込んでから再実行してください:\n  {CONFIG_PATH}")
        sys.exit(1)
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    # GitHub の Secrets（環境変数）があればそちらを優先
    for k, env in ENV_KEYS.items():
        if os.environ.get(env):
            merged[k] = os.environ[env].strip()
    if not os.environ.get("RAKUTEN_ORIGIN") and os.environ.get("GITHUB_REPOSITORY_OWNER") and \
            merged["origin"] == DEFAULT_CONFIG["origin"]:
        merged["origin"] = f"https://{os.environ['GITHUB_REPOSITORY_OWNER'].lower()}.github.io"
    if "ここに" in merged["application_id"] or "ここに" in merged["access_key"]:
        log("アプリIDとアクセスキーが未設定です（config.json か、GitHub の Secrets: RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY）。")
        sys.exit(1)
    return merged


# ---------------------------------------------------------------- API
class NotFound(Exception):
    pass


def api_get(url, params, cfg, retries=4):
    params = dict(params)
    params.update({"applicationId": cfg["application_id"], "format": "json"})
    full = url + "?" + urllib.parse.urlencode(params)
    headers = {
        "accessKey": cfg["access_key"],
        "Origin": cfg["origin"],
        "Referer": cfg["origin"].rstrip("/") + "/",
        "User-Agent": "apa-rate-tracker/1.0",
    }
    wait = 5
    for attempt in range(retries + 1):
        req = urllib.request.Request(full, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 404:
                raise NotFound(body)
            if e.code in (429, 500, 503) and attempt < retries:
                log(f"  HTTP {e.code}、{wait}秒待って再試行します")
                time.sleep(wait)
                wait *= 2
                continue
            hint = ""
            if e.code in (401, 403):
                hint = ("\n  → アプリID・アクセスキー、または config.json の origin が"
                        "楽天の『許可されたWebサイト』と一致しているか確認してください。")
            raise SystemExit(f"API エラー HTTP {e.code}: {body[:300]}{hint}")
        except urllib.error.URLError as e:
            if attempt < retries:
                log(f"  通信エラー（{e.reason}）、{wait}秒待って再試行します")
                time.sleep(wait)
                wait *= 2
                continue
            raise SystemExit(f"通信エラー: {e.reason}")


def walk(obj):
    """dict/list をたどってすべての dict を返す（formatVersion 1/2 どちらでも動くように）"""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def find_key(obj, key):
    for d in walk(obj):
        if key in d:
            return d[key]
    return None


# ---------------------------------------------------------------- area
def classify(address1, address2):
    pref = (address1 or "").strip()
    a2 = (address2 or "").strip()
    if pref == "東京都":
        return "東京23区" if re.match(r"^[^市町村]{1,4}区", a2) else "東京都(23区外)"
    for name, pat in CITY_AREAS.get(pref, []):
        if re.match(pat, a2):
            return name
    return pref or "不明"


def in_scope(hotel, scope):
    if "全国" in scope:
        return True
    return hotel["area"] in scope or hotel["prefecture"] in scope


# ---------------------------------------------------------------- hotels
def cmd_hotels(cfg):
    os.makedirs(DATA, exist_ok=True)
    hotels = {}
    page = 1
    while True:
        params = {
            "keyword": cfg["keyword"],
            "searchField": 1,
            "hits": 30,
            "page": page,
            "datumType": 1,
            "responseType": "small",
        }
        try:
            res = api_get(KEYWORD_URL, params, cfg)
        except NotFound:
            break
        paging = res.get("pagingInfo") or find_key(res, "pagingInfo") or {}
        found = 0
        for d in walk(res.get("hotels", [])):
            info = d.get("hotelBasicInfo")
            if not isinstance(info, dict) or "hotelNo" not in info:
                continue
            name = info.get("hotelName", "")
            if "アパ" not in name and "APA" not in name.upper():
                continue
            no = int(info["hotelNo"])
            hotels[no] = {
                "hotelNo": no,
                "name": name,
                "prefecture": info.get("address1", ""),
                "address": (info.get("address1", "") or "") + (info.get("address2", "") or ""),
                "area": classify(info.get("address1"), info.get("address2")),
                "lat": info.get("latitude"),
                "lng": info.get("longitude"),
                "review": info.get("reviewAverage"),
                "url": info.get("hotelInformationUrl"),
            }
            found += 1
        page_count = int(paging.get("pageCount") or 1)
        log(f"  ホテル一覧 {page}/{page_count} ページ（このページのアパホテル {found} 件）")
        if page >= page_count or page >= 100:
            break
        page += 1
        time.sleep(cfg["request_interval_sec"])
    out = sorted(hotels.values(), key=lambda h: (h["prefecture"], h["name"]))
    with open(HOTELS_PATH, "w", encoding="utf-8") as f:
        json.dump({"updated": now_jst().isoformat(timespec="seconds"), "hotels": out},
                  f, ensure_ascii=False, indent=1)
    areas = {}
    for h in out:
        areas[h["area"]] = areas.get(h["area"], 0) + 1
    top = ", ".join(f"{k} {v}" for k, v in sorted(areas.items(), key=lambda x: -x[1])[:8])
    log(f"全国のアパホテル {len(out)} 件を保存しました（{top} …）")
    return out


def load_hotels():
    if not os.path.exists(HOTELS_PATH):
        return None, None
    with open(HOTELS_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return d["hotels"], d.get("updated")


# ---------------------------------------------------------------- prices
def extract_prices(res, stay_date):
    """ホテル番号 → その日の最安値（1室1泊、税サ込）"""
    out = {}
    for h in res.get("hotels", []) if isinstance(res, dict) else []:
        info = find_key(h, "hotelBasicInfo") or {}
        no = info.get("hotelNo")
        if no is None:
            continue
        totals = []
        for d in walk(h):
            dc = d.get("dailyCharge")
            if isinstance(dc, dict) and dc.get("total"):
                if dc.get("stayDate") in (None, "", stay_date):
                    totals.append(int(dc["total"]))
        if not totals and info.get("hotelMinCharge"):
            totals.append(int(info["hotelMinCharge"]))
        if totals:
            out[int(no)] = min(totals)
    return out


PRICE_FIELDS = ["fetched_at", "stay_date", "hotel_no", "hotel_name", "area", "prefecture", "price", "status"]


def cmd_fetch(cfg):
    hotels, _ = load_hotels()
    if not hotels:
        hotels = cmd_hotels(cfg)
    targets = [h for h in hotels if in_scope(h, cfg["fetch_scope"])]
    if not targets:
        raise SystemExit(f"fetch_scope {cfg['fetch_scope']} に当てはまるホテルがありません。")
    batches = [targets[i:i + 15] for i in range(0, len(targets), 15)]
    today = now_jst().date()
    dates = [today + dt.timedelta(days=cfg["start_offset_days"] + i) for i in range(cfg["days_ahead"])]
    total_req = len(batches) * len(dates)
    est = total_req * (cfg["request_interval_sec"] + 0.4) / 60
    log(f"{len(targets)} 軒 × {len(dates)} 日を取得します（{total_req} リクエスト、目安 {est:.0f} 分）")

    now = now_jst()
    fetched_at = now.isoformat(timespec="minutes")
    os.makedirs(PRICES_DIR, exist_ok=True)
    out_path = os.path.join(PRICES_DIR, now.strftime("%Y-%m") + ".csv")
    new_file = not os.path.exists(out_path)
    n_ok = n_full = 0
    with open(out_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
        if new_file:
            w.writeheader()
        done = 0
        for day in dates:
            ci, co = day.isoformat(), (day + dt.timedelta(days=1)).isoformat()
            for batch in batches:
                params = {
                    "hotelNo": ",".join(str(h["hotelNo"]) for h in batch),
                    "checkinDate": ci,
                    "checkoutDate": co,
                    "adultNum": cfg["adults"],
                    "roomNum": 1,
                    "searchPattern": 0,
                    "hits": 30,
                    "responseType": "small",
                }
                try:
                    prices = extract_prices(api_get(VACANT_URL, params, cfg), ci)
                except NotFound:
                    prices = {}
                for h in batch:
                    p = prices.get(h["hotelNo"])
                    w.writerow({
                        "fetched_at": fetched_at, "stay_date": ci, "hotel_no": h["hotelNo"],
                        "hotel_name": h["name"], "area": h["area"], "prefecture": h["prefecture"],
                        "price": p if p is not None else "", "status": "ok" if p is not None else "soldout",
                    })
                    if p is None:
                        n_full += 1
                    else:
                        n_ok += 1
                done += 1
                time.sleep(cfg["request_interval_sec"])
            f.flush()
            log(f"  {ci} 完了（{done}/{total_req}）")
    log(f"取得完了: 料金あり {n_ok} 件 / 空室なし {n_full} 件 → {out_path}")


# ---------------------------------------------------------------- report
def price_files():
    files = []
    if os.path.exists(LEGACY_PRICES_PATH):
        files.append(LEGACY_PRICES_PATH)
    if os.path.isdir(PRICES_DIR):
        files += sorted(os.path.join(PRICES_DIR, n) for n in os.listdir(PRICES_DIR) if n.endswith(".csv"))
    return files


def iter_prices():
    for path in price_files():
        with open(path, encoding="utf-8") as f:
            yield from csv.DictReader(f)


def cmd_report(cfg):
    if not price_files():
        raise SystemExit("料金データがありません。先に fetch を実行してください。")
    hotels, hotels_updated = load_hotels()
    meta = {}
    for h in hotels or []:
        meta[h["hotelNo"]] = {"name": h["name"], "area": h["area"], "pref": h["prefecture"],
                              "review": h.get("review"), "url": h.get("url")}
    snaps = set()
    for r in iter_prices():
        snaps.add(r["fetched_at"])
        no = int(r["hotel_no"])
        meta.setdefault(no, {"name": r["hotel_name"], "area": r["area"], "pref": r["prefecture"],
                             "review": None, "url": None})
    keep = set(sorted(snaps)[-REPORT_SNAPSHOTS:])
    rows = []
    groups = {}  # (取得時点, 宿泊日, エリア) → 料金リスト（ブッキングカーブ用）
    for r in iter_prices():
        no = int(r["hotel_no"])
        price = int(r["price"]) if r["price"] else None
        if r["fetched_at"] in keep:
            rows.append([r["fetched_at"], r["stay_date"], no, price])
        if price is not None:
            groups.setdefault((r["fetched_at"], r["stay_date"], meta[no]["area"]), []).append(price)
    curve = []
    for (snap, stay, area), ps in groups.items():
        ps.sort()
        n = len(ps)
        m = ps[n // 2] if n % 2 else (ps[n // 2 - 1] + ps[n // 2]) / 2
        curve.append([snap, stay, area, round(m)])
    curve.sort()
    payload = {
        "generated": now_jst().isoformat(timespec="minutes"),
        "hotelsUpdated": hotels_updated,
        "snapshotsTotal": len(snaps),
        "hotels": {str(k): v for k, v in meta.items()},
        "rows": rows,
        "curve": curve,
    }
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        tpl = f.read()
    data_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", data_js)
    os.makedirs(os.path.dirname(os.path.abspath(REPORT_PATH)), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"ダッシュボードを作成しました → {REPORT_PATH}")


# ---------------------------------------------------------------- main
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "report":
        cfg = dict(DEFAULT_CONFIG)
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        return cmd_report(cfg)
    cfg = load_config()
    if cmd == "hotels":
        cmd_hotels(cfg)
    elif cmd == "fetch":
        cmd_fetch(cfg)
    elif cmd == "all":
        hotels, updated = load_hotels()
        stale = (not hotels or not updated or
                 now_jst() - dt.datetime.fromisoformat(updated) > dt.timedelta(days=7))
        if stale:
            cmd_hotels(cfg)
        cmd_fetch(cfg)
        cmd_report(cfg)
    else:
        log(__doc__)


if __name__ == "__main__":
    main()
