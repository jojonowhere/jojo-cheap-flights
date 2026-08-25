#!/usr/bin/env python3
"""
flight_deal_check.py

查一條航線,順便告訴你:這個價格跟過去兩個月的歷史價格比起來,算不算優惠。

資料來源:
  - 用 fast-flights 產生 Google Flights 的查詢參數(tfs)
  - 直接用一般 HTTP 請求把該查詢的完整網頁原始碼抓回來(不需要任何 API key)
  - 從網頁裡藏的 AF_initDataCallback 資料包(Google 自己算好的價格洞察)解析出:
      * 目前最便宜報價
      * 該路線「典型」價格區間(低 / 高)
      * price_level(low / typical / high)
      * 過去約 61 天,每天的真實票價(price_history)
      * 每一班班機的報價、航空公司、起降時間

用法(不用設定任何東西就能跑):
  python3 flight_deal_check.py --from TPE --to NRT --depart 2026-10-09 --return 2026-10-16

如果短時間內查很多次(例如一次批次比對很多條航線、很多天),Google 可能會暫時擋下
直接查詢。這時候程式會清楚告訴你原因,並附上申請免費 Bright Data 帳號(每月 5,000 次
額度)的說明——那是 Bright Data 自己的免費方案,設定好以後才需要這兩個環境變數:

  export BRIGHTDATA_API_KEY="你的 API key"
  export BRIGHTDATA_SERP_ZONE="serp_api1"   # 你在 Bright Data 建的 zone 名稱

不需要在程式碼裡寫死 API key —— 一律從環境變數讀,避免不小心把金鑰存進檔案裡。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, median, pstdev

import requests

try:
    from fast_flights import FlightQuery, Passengers, create_query
except ImportError:
    sys.exit(
        "缺少 fast-flights 套件(用它來產生 Google Flights 的查詢參數)。\n"
        "請先安裝: pip install fast-flights typing_extensions primp"
    )

BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"


# ---------------------------------------------------------------------------
# 1. 建立查詢網址(重用 fast-flights 的 protobuf 編碼,不自己重造輪子)
# ---------------------------------------------------------------------------

def build_flights_url(
    from_airport: str,
    to_airport: str,
    depart_date: str,
    return_date: str | None = None,
    currency: str = "TWD",
    language: str = "en",
    geo: str = "us",
) -> str:
    flights = [FlightQuery(date=depart_date, from_airport=from_airport, to_airport=to_airport)]
    trip = "one-way"
    if return_date:
        flights.append(FlightQuery(date=return_date, from_airport=to_airport, to_airport=from_airport))
        trip = "round-trip"

    query = create_query(
        flights=flights,
        trip=trip,
        seat="economy",
        passengers=Passengers(adults=1),
        language=language,
        currency=currency,
    )
    # 沒有 gl(地區代碼)Google 常常只回傳空殼頁面,ds:1(報價+歷史價格)要等瀏覽器另外發請求才補齊。
    # 帶上 gl 強制拿到內建完整資料的版本,不用重試就能一次成功。
    return query.url() + f"&gl={geo}"


# ---------------------------------------------------------------------------
# 2. 抓網頁原始碼 —— 先直接查(不用任何 API key),查不到才用 Bright Data 當備援
# ---------------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchBlocked(Exception):
    """直接查跟 Bright Data 備援都失敗時丟出來,附上要不要設定 Bright Data 的說明。"""


def fetch_raw_html_direct(target_url: str) -> str:
    """不用任何 API key,直接當一般瀏覽器去問 Google。

    實測過:偶爾查詢(不是短時間內連續大量查)第一次就會成功,不需要任何設定。
    Bright Data 的價值是在短時間內大量查詢會被 Google 盯上時,用它的代理網路繞過去——
    不是每次查詢都必要,只在直接查被擋的時候才需要。
    """
    resp = requests.get(target_url, headers=_BROWSER_HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"直接查詢收到 HTTP {resp.status_code}")
    html = resp.text
    if len(html) < 5000:
        raise RuntimeError(f"直接查詢回傳內容異常短({len(html)} 字元),可能被擋了")
    return html


def fetch_raw_html_brightdata(target_url: str, api_key: str, zone: str) -> str:
    resp = requests.post(
        BRIGHTDATA_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"zone": zone, "url": target_url, "format": "raw", "data_format": "html"},
        timeout=120,
    )
    resp.raise_for_status()
    html = resp.text
    if len(html) < 5000:
        # 正常的機票頁面原始碼有幾百萬字元,太短代表被擋、查詢失敗、或格式跟預期不同
        raise RuntimeError(f"回傳內容異常短({len(html)} 字元),可能查詢失敗。內容開頭: {html[:300]!r}")
    return html


def fetch_raw_html(target_url: str, api_key: str | None, zone: str | None) -> str:
    """先直接查;失敗的話,有設定 Bright Data 就用它當備援,沒有就老實說明怎麼辦。"""
    try:
        return fetch_raw_html_direct(target_url)
    except Exception as direct_error:
        if api_key and zone:
            return fetch_raw_html_brightdata(target_url, api_key, zone)
        raise FetchBlocked(
            "直接查詢這次沒成功"
            f"(原因:{direct_error})。這通常是短時間內查太多次,被 Google 暫時擋下來了,\n"
            "不是你的電腦或帳號有問題。\n\n"
            "如果只是偶爾查一兩次,通常過一陣子再試就會恢復正常,不用做任何設定。\n"
            "如果你要一次查很多條航線、很多天(例如批次比價),建議申請一個免費的 Bright Data "
            "帳號(https://brightdata.com),開一個 SERP API 的 zone,每月有 5,000 次免費額度,\n"
            "設定好 BRIGHTDATA_API_KEY 跟 BRIGHTDATA_SERP_ZONE 這兩個環境變數之後,查詢會自動改走這條路,\n"
            "不會再被擋。這是 Bright Data 自己的免費方案,跟這個工具的作者沒有任何關係,\n"
            "不是要你為了誰付費或註冊什麼——單純是查詢量大的時候需要的技術手段。"
        ) from direct_error


# ---------------------------------------------------------------------------
# 3. 從網頁原始碼裡挖出 Google 自己算好的資料包(AF_initDataCallback)
# ---------------------------------------------------------------------------

_BLOB_PATTERN = re.compile(
    r"AF_initDataCallback\(\{key:\s*'([^']+)'.*?data:(\[.*?\])\s*,\s*sideChannel", re.DOTALL
)


def extract_data_blobs(html: str) -> dict[str, list]:
    blobs = {}
    for key, raw in _BLOB_PATTERN.findall(html):
        try:
            blobs[key] = json.loads(raw)
        except json.JSONDecodeError:
            continue
    return blobs


def _is_flight_item(item) -> bool:
    """判斷這個節點看起來像不像『一筆航班資料』(用形狀比對,不死綁巢狀層數)。

    真實結構(2026-08 驗證,含轉機航班):
      item[0] = [航空公司代碼, [航空公司名稱], [[航段1, 航段2, ...]],
                 出發機場, 出發日期, 出發時間, 抵達機場, 抵達日期, 抵達時間,
                 總飛行時間(分), 轉機次數, ...]
      item[1] = [[None, 價格], booking_token]

    注意:出發/抵達時間跟總時間要從 item[0] 這一層讀(整趟行程),不能只讀
    第一段航段(item[0][2][0])的時間——那樣轉機航班會只顯示第一段的時間跟時長。
    """
    try:
        return (
            isinstance(item, list)
            and len(item) >= 2
            and isinstance(item[0], list)
            and len(item[0]) >= 11
            and isinstance(item[0][0], str)
            and len(item[0][0]) <= 3  # 航空公司代碼通常 2~3 碼
            and isinstance(item[0][2], list)
            and len(item[0][2]) >= 1
            and isinstance(item[0][2][0], list)
            and isinstance(item[0][3], str)  # 出發機場代碼
            and isinstance(item[0][6], str)  # 抵達機場代碼
            and isinstance(item[0][9], int)  # 總飛行時間
            and isinstance(item[1], list)
            and isinstance(item[1][0], list)
            and isinstance(item[1][0][1], (int, float))  # 價格
        )
    except (IndexError, TypeError):
        return False


def _collect_flight_items(node, found: list) -> None:
    """遞迴找出每一筆『看起來像航班資料』的節點,一筆一筆獨立判斷。

    先前的版本要求整組清單裡每一筆都符合形狀才承認這組清單,結果只要
    同一組裡有一筆缺價格(Google 對某些重複時段的選項確實不給價格,
    這是真實資料裡會出現的情況,不是例外),就會連其他有效報價一起漏掉。
    改成逐筆判斷、逐筆收集,不會因為一顆老鼠屎壞了一整鍋粥。
    """
    if not isinstance(node, list):
        return
    if _is_flight_item(node):
        found.append(node)
        return  # 一筆航班內部的子結構(航段、代碼)不會再獨立符合形狀,不用往下挖
    for child in node:
        _collect_flight_items(child, found)


def _is_price_history_block(block) -> bool:
    """判斷這個節點看起來像不像『每日歷史價格時間序列』的容器。"""
    try:
        series = block[10][0]
        return (
            isinstance(series, list)
            and len(series) > 10
            and all(
                isinstance(pt, list) and len(pt) == 2 and isinstance(pt[0], int) and pt[0] > 10**12
                for pt in series[:5]
            )
        )
    except (IndexError, TypeError):
        return False


def _fmt_time(parts) -> str:
    """[hour, minute] → 'HH:MM'。Google 省略掉分鐘=0 時只給 [hour],要補回去。"""
    hour = parts[0] if len(parts) > 0 else 0
    minute = parts[1] if len(parts) > 1 else 0
    return f"{hour:02d}:{minute:02d}"


@dataclass
class FlightOffer:
    airline: str
    price: int
    dep_date: str
    dep_time: str
    arr_time: str
    duration_min: int
    stops: int = 0

    @property
    def stops_label(self) -> str:
        return "直飛" if self.stops == 0 else f"轉機 {self.stops} 次"


@dataclass
class PriceInsights:
    current_price: int
    typical_low: int
    typical_high: int
    price_level_code: int
    history: list[tuple[str, int]]  # (YYYY-MM-DD, price) 過去約 61 天

    @property
    def price_level_label(self) -> str:
        return {0: "low", 1: "typical", 2: "high"}.get(self.price_level_code, f"unknown({self.price_level_code})")

    def evaluate(self) -> dict:
        """把『優惠程度』算成好懂的數字。"""
        hist_prices = [p for _, p in self.history]
        hist_mean = mean(hist_prices)
        hist_median = median(hist_prices)
        hist_sd = pstdev(hist_prices)

        # PR 值:跟大考成績單同一個概念——這張票贏過過去幾 % 的日子。
        # PR 越高,代表過去越多天比現在貴,現在買越划算。
        cheaper_than = sum(1 for p in hist_prices if p >= self.current_price)
        pr = round(100 * cheaper_than / len(hist_prices))
        z_score = (self.current_price - hist_mean) / hist_sd if hist_sd else 0.0

        return {
            "目前最便宜報價": self.current_price,
            "PR值": f"PR{pr}（跟過去 {len(hist_prices)} 天比,贏過 {pr}%,PR 越高越划算）",
            "Google 標示": self.price_level_label,
            "典型價格區間": f"${self.typical_low} - ${self.typical_high}",
            "過去61天歷史均價": round(hist_mean),
            "過去61天歷史中位數": round(hist_median),
            "標準差": round(hist_sd, 1),
            "95%信賴區間(常態分佈近似)": f"${round(hist_mean - 1.96 * hist_sd)} - ${round(hist_mean + 1.96 * hist_sd)}",
            "目前價格的Z分數": f"{z_score:+.2f} 個標準差",
            "比歷史均價便宜多少%": round(100 * (hist_mean - self.current_price) / hist_mean, 1),
        }

    def stats(self) -> dict:
        """給視覺化(鐘形分佈圖)用的原始統計數字。"""
        hist_prices = [p for _, p in self.history]
        hist_mean = mean(hist_prices)
        hist_sd = pstdev(hist_prices)
        cheaper_than = sum(1 for p in hist_prices if p >= self.current_price)
        return {
            "mean": hist_mean,
            "sd": hist_sd,
            "ci95_low": hist_mean - 1.96 * hist_sd,
            "ci95_high": hist_mean + 1.96 * hist_sd,
            "current": self.current_price,
            "pr": round(100 * cheaper_than / len(hist_prices)),
        }


def parse_flights(blobs: dict[str, list]) -> list[FlightOffer]:
    offers = []
    items: list = []
    for blob in blobs.values():
        _collect_flight_items(blob, items)

    for item in items:
        try:
            airline_code = item[0][0]
            airline_names = item[0][1]
            airline = airline_names[0] if airline_names else airline_code
            price = item[1][0][1]
            # 用 item[0] 這一層(整趟行程的總結),不是單一航段——
            # 轉機航班的話,單一航段只會有第一段的時間跟時長,不是全程的。
            dep_date_parts = item[0][4]
            dep_date = "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(dep_date_parts))
            dep_time = _fmt_time(item[0][5])
            arr_time = _fmt_time(item[0][8])
            duration = item[0][9]
            stops = item[0][10] if isinstance(item[0][10], int) else len(item[0][2]) - 1
            offers.append(FlightOffer(airline, price, dep_date, dep_time, arr_time, duration, stops))
        except (IndexError, TypeError, ValueError):
            continue
    # 去重(同一班機常常在資料包裡出現不只一次)
    seen = set()
    unique = []
    for o in offers:
        key = (o.airline, o.price, o.dep_time, o.arr_time)
        if key not in seen:
            seen.add(key)
            unique.append(o)
    return unique


def parse_price_insights(blobs: dict[str, list]) -> PriceInsights | None:
    for blob in blobs.values():
        if not isinstance(blob, list):
            continue
        for block in blob:
            if _is_price_history_block(block):
                current = block[1][1]
                typical_low = block[4][1]
                typical_high = block[5][1]
                level_code = block[6]
                series = block[10][0]
                history = [
                    (datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d"), price)
                    for ts, price in series
                ]
                return PriceInsights(current, typical_low, typical_high, level_code, history)
    return None


# ---------------------------------------------------------------------------
# 3b. 驗算 —— Google 網頁格式以後一定會變,這裡不假設 parser 永遠正確。
#     原則:先把解析到的結果印出來給你看(體驗要順),驗算是事後幫你把關,
#     不會擋住結果,只會在數字看起來不對勁時明確告訴你「哪裡看起來怪」。
# ---------------------------------------------------------------------------

def validate_result(offers: list[FlightOffer], insights: "PriceInsights | None") -> list[str]:
    warnings: list[str] = []

    if len(offers) == 0:
        warnings.append("完全沒解析到任何航班報價 —— _is_flight_item() 的形狀比對可能跟目前的 Google 網頁結構對不上了。")
    elif len(offers) < 3:
        warnings.append(f"只解析到 {len(offers)} 個航班報價,一般正常查詢通常有 10 個以上,可能只解析到部分資料。")

    for o in offers:
        if not (1 <= o.price <= 100_000):
            warnings.append(f"有一筆報價金額看起來不合理:{o.airline} ${o.price},可能抓錯欄位。")
            break
        if not o.airline:
            warnings.append("有一筆報價缺少航空公司名稱,可能抓錯欄位。")
            break

    if insights is None:
        return warnings

    n = len(insights.history)
    if not (40 <= n <= 100):
        warnings.append(
            f"歷史價格筆數是 {n} 筆(預期約 55~65 筆左右)。如果差很多,代表 parse_price_insights() "
            f"裡 block[10][0] 這個位置的巢狀結構可能已經跑掉,需要重新用瀏覽器實際查一次去對照真實結構。"
        )

    if insights.typical_low >= insights.typical_high:
        warnings.append(
            f"典型價格區間下限({insights.typical_low})大於等於上限({insights.typical_high}),"
            f"順序不對,可能是 block[4]/block[5] 的位置搞反了。"
        )

    hist_prices = [p for _, p in insights.history]
    if hist_prices:
        lo, hi = min(hist_prices), max(hist_prices)
        span = hi - lo
        if span > 0:
            out_of_range = sum(1 for p in hist_prices if p < lo - span or p > hi + span)
            if out_of_range:
                warnings.append(f"歷史價格裡有 {out_of_range} 個離群值,可能混進了非價格的數字。")

        if not (lo * 0.4 <= insights.current_price <= hi * 2.5):
            warnings.append(
                f"目前報價(${insights.current_price})跟歷史價格區間(${lo}~${hi})差距大到不太合理,"
                f"可能是 item[1][0][1] 抓到了別的欄位。"
            )

    return warnings


# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------

def check_deal(
    from_airport: str, to_airport: str, depart_date: str, return_date: str | None, json_out: str | None = None
) -> None:
    # 不強制要求 Bright Data ——先直接查,查不到才需要這兩個環境變數當備援。
    # 沒設定也能跑,只是遇到查太多次被擋的情況時,fetch_raw_html 會清楚告訴你怎麼辦。
    api_key = os.environ.get("BRIGHTDATA_API_KEY")
    zone = os.environ.get("BRIGHTDATA_SERP_ZONE")

    url = build_flights_url(from_airport, to_airport, depart_date, return_date)
    print(f"查詢: {from_airport} → {to_airport}, {depart_date}" + (f" 回程 {return_date}" if return_date else ""))

    # Google 有時候第一次回應就內建完整資料,有時候要多問幾次才會給——重試幾次找到完整的那次
    max_attempts = 4
    offers, insights = [], None
    for attempt in range(1, max_attempts + 1):
        print(f"正在抓取 Google Flights 頁面...(第 {attempt}/{max_attempts} 次嘗試)")
        try:
            html = fetch_raw_html(url, api_key, zone)
        except FetchBlocked as e:
            sys.exit(str(e))
        blobs = extract_data_blobs(html)
        offers = parse_flights(blobs)
        insights = parse_price_insights(blobs)
        if offers and insights:
            break
        print("  → 這次資料不完整(Google 這次沒有內建完整資料包),等幾秒後重試...")
        time.sleep(8)

    if not offers and insights is None:
        sys.exit(f"試了 {max_attempts} 次都沒拿到完整資料,可能是這條航線真的太冷門,或 Google 網頁格式已變動需要更新 parser。")

    print(f"\n找到 {len(offers)} 個航班報價:")
    for o in sorted(offers, key=lambda x: x.price)[:10]:
        print(f"  {o.airline:20s} {o.dep_time}-{o.arr_time}  {o.duration_min}分鐘  {o.stops_label:8s} ${o.price}")

    if insights is not None:
        print("\n=== 優惠程度分析 ===")
        for k, v in insights.evaluate().items():
            print(f"  {k}: {v}")

    # 結果先印給你看,驗算放在最後、不擋流程 —— 只有真的怪的時候才會有東西印出來。
    problems = validate_result(offers, insights)
    if problems:
        print("\n🔍 驗算發現幾個看起來不太對勁的地方(不代表一定錯,但建議留意):")
        for p in problems:
            print(f"  ⚠️ {p}")
    else:
        print("\n🔍 驗算通過,數字看起來合理。")

    if insights is None:
        print("\n⚠️ 沒有找到 price insights 區塊(可能是冷門航線,Google 沒有足夠歷史資料算)")
        return

    if json_out:
        payload = {
            "route": {"from": from_airport, "to": to_airport, "depart": depart_date, "return": return_date},
            "offers": [
                {
                    "airline": o.airline, "price": o.price, "dep_date": o.dep_date,
                    "dep_time": o.dep_time, "arr_time": o.arr_time, "duration_min": o.duration_min,
                    "stops": o.stops,
                }
                for o in sorted(offers, key=lambda x: x.price)
            ],
            "price_history": insights.history,
            "stats": insights.stats(),
            "typical_low": insights.typical_low,
            "typical_high": insights.typical_high,
            "price_level": insights.price_level_label,
        }
        with open(json_out, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n完整結果已存到: {json_out}")


def main():
    parser = argparse.ArgumentParser(description="查一條航線,並分析價格相對歷史的優惠程度")
    parser.add_argument("--from", dest="from_airport", required=True, help="出發機場代碼,例如 TPE")
    parser.add_argument("--to", dest="to_airport", required=True, help="目的地機場代碼,例如 NRT")
    parser.add_argument("--depart", required=True, help="出發日期 YYYY-MM-DD")
    parser.add_argument("--return", dest="return_date", default=None, help="回程日期 YYYY-MM-DD(不填代表單程)")
    parser.add_argument("--json-out", default=None, help="把完整結果存成 JSON 檔(給報告頁面用)")
    args = parser.parse_args()

    check_deal(args.from_airport, args.to_airport, args.depart, args.return_date, args.json_out)


if __name__ == "__main__":
    main()
