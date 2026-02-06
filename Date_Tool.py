from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import sys
import tempfile
import time
import unittest
from enum import Enum
from typing import Any, Callable, Final, Iterable, Mapping, MutableMapping, TypedDict

from colorama import Fore, Style, init
from jsonschema import ValidationError, validate
import requests
from requests.exceptions import RequestException, Timeout
from tqdm import tqdm

init(autoreset=True)


class HolidayInfo(TypedDict):
    name: str
    is_holiday: bool


class Color(Enum):
    HEADER = Fore.LIGHTCYAN_EX
    SUCCESS = Fore.GREEN
    ERROR = Fore.RED
    WARNING = Fore.YELLOW
    INFO = Fore.BLUE
    RESET = Style.RESET_ALL


def _c(text: str, color: Color) -> str:
    return f"{color.value}{text}{Color.RESET.value}"


def _is_exit_command(text: str) -> bool:
    return text.strip().lower() in {"q", "quit", "exit"}


if platform.system() == "Windows":
    import msvcrt as _msvcrt

    def _kbhit() -> bool:
        return _msvcrt.kbhit()

    def _getch() -> bytes:
        return _msvcrt.getch()

else:
    import select
    import termios
    import tty

    _termios: Any = termios
    _tty: Any = tty

    def _kbhit() -> bool:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)

    def _getch() -> bytes:
        fd = sys.stdin.fileno()
        old_settings = _termios.tcgetattr(fd)
        try:
            _tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old_settings)
        return ch.encode("utf-8", errors="ignore")


def wait_for_any_key(prompt: str | None = None, exit_key: str = "q") -> bool:
    if prompt:
        print(_c(prompt, Color.INFO))
    else:
        print(_c(f"按任意键继续，或按'{exit_key}'退出...", Color.INFO))

    if not sys.stdin.isatty():
        try:
            line = input()
        except EOFError:
            return False
        return line.strip().lower() != exit_key.lower()

    while True:
        try:
            hit = _kbhit()
        except OSError:
            hit = False

        if hit:
            key = _getch()
            char = key.decode("utf-8", errors="ignore").lower()
            if char == exit_key.lower() or key.lower() == b"q":
                return False
            return True
        time.sleep(0.05)


def get_input(prompt: str, *, default: str | None = None) -> str:
    if default is None:
        raw = input(_c(f"{prompt}: ", Color.INFO)).strip()
    else:
        raw = input(_c(f"{prompt}（直接回车使用{default}）: ", Color.INFO)).strip()
        if raw == "":
            raw = default
    return raw


_DATE_NUMS_RE: Final[re.Pattern[str]] = re.compile(r"\d+")


def validate_date_input(raw: str) -> dt.date:
    nums = _DATE_NUMS_RE.findall(raw.strip())
    if len(nums) >= 3 and len(nums[0]) == 4:
        year = int(nums[0])
        month = int(nums[1])
        day = int(nums[2])
        if not (1900 <= year <= 2100):
            raise ValueError("年份超出范围(1900-2100)")
        return dt.date(year, month, day)

    if len(nums) >= 2:
        month = int(nums[0])
        day = int(nums[1])
        return dt.date(2000, month, day)

    raise ValueError("日期格式错误")


def validate_year(raw: str) -> int:
    year = int(raw)
    if not (1900 <= year <= 2100):
        raise ValueError("年份必须在 1900-2100 之间")
    return year


def validate_year_range(raw: str) -> tuple[int, int]:
    raw = raw.strip()
    if raw == "":
        return 2020, 2030
    parts = raw.split("-", maxsplit=1)
    if len(parts) == 1:
        start = end = int(parts[0])
    else:
        start = int(parts[0])
        end = int(parts[1])
    if not (1900 <= start <= end <= 2100):
        raise ValueError("年份必须在 1900-2100 之间，且起始不大于结束")
    return start, end


class HolidayAPIError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Holiday API error {code}: {message}")
        self.code = code
        self.message = message


_SCHEMA_API1: Final[dict[str, Any]] = {
    "type": "object",
    "required": ["code", "holiday"],
    "properties": {
        "code": {"type": "integer"},
        "holiday": {"type": "object"},
        "msg": {"type": "string"},
    },
}

_SCHEMA_API2: Final[dict[str, Any]] = {
    "type": "object",
    "required": ["code", "data"],
    "properties": {
        "code": {"type": "integer"},
        "msg": {"type": "string"},
        "data": {
            "type": "object",
            "required": ["list"],
            "properties": {
                "list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["date", "workday"],
                        "properties": {
                            "date": {"type": "integer"},
                            "workday": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    },
                }
            },
        },
    },
}

_HOLIDAY_SCHEMA: Final[dict[str, Any]] = {"oneOf": [_SCHEMA_API1, _SCHEMA_API2]}


def _parse_downloaded_at(line: str) -> dt.datetime | None:
    prefix = "# downloaded_at:"
    if not line.startswith(prefix):
        return None
    value = line[len(prefix) :].strip()
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _now_local() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone()


def _is_expired(downloaded_at: dt.datetime, max_age_days: int) -> bool:
    return (_now_local() - downloaded_at) > dt.timedelta(days=max_age_days)


class HolidayProvider:
    COLORS: Mapping[str, Color] = {
        "success": Color.SUCCESS,
        "warning": Color.WARNING,
        "info": Color.INFO,
        "error": Color.ERROR,
    }

    def __init__(self, cache_dir: str = ".") -> None:
        self._cache_dir = cache_dir
        self._cache: dict[int, dict[dt.date, HolidayInfo]] = {}
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/91.0.4472.124 Safari/537.36"
                )
            }
        )

    def cache_path(self, year: int) -> str:
        return os.path.join(self._cache_dir, f"holidays_{year}.json")

    def clear_cache(self, year: int | None = None) -> None:
        if year is None:
            for name in os.listdir(self._cache_dir):
                if name.startswith("holidays_") and name.endswith(".json"):
                    os.remove(os.path.join(self._cache_dir, name))
            self._cache.clear()
            print(_c("✓ 缓存已清理", Color.SUCCESS))
            return

        path = self.cache_path(year)
        if os.path.exists(path):
            os.remove(path)
        self._cache.pop(year, None)
        print(_c(f"✓ {year} 年缓存已清理", Color.SUCCESS))

    def get_holidays(
        self, year: int, *, refresh: bool = False, max_age_days: int = 7
    ) -> dict[dt.date, HolidayInfo]:
        if not refresh and year in self._cache:
            return self._cache[year]

        cached, expired = self._read_cache(year, max_age_days=max_age_days)
        if cached is not None and not refresh and not expired:
            self._cache[year] = cached
            return cached

        try:
            holidays = self._download(year)
            self._cache[year] = holidays
            return holidays
        except (HolidayAPIError, ValueError, RequestException, Timeout) as exc:
            if cached is not None:
                print(_c(f"⚠ 使用缓存兜底：{exc}", Color.WARNING))
                self._cache[year] = cached
                return cached
            raise

    def parse_holiday_data(
        self, data: Mapping[str, Any], year: int
    ) -> dict[dt.date, HolidayInfo]:
        code = data.get("code")
        if code != 0:
            msg = str(data.get("msg") or data.get("message") or "")
            raise HolidayAPIError(int(code) if isinstance(code, int) else -1, msg)

        try:
            validate(instance=dict(data), schema=_HOLIDAY_SCHEMA)
        except ValidationError as err:
            path = ".".join(str(p) for p in err.path)
            raise ValueError(f"schema 校验失败 at '{path}': {err.message}") from err

        if "holiday" in data and isinstance(data["holiday"], dict):
            return self._parse_api1(data["holiday"], year)

        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("list"), list):
            return self._parse_api2(inner["list"], year)

        raise ValueError("未知的节假日数据结构")

    def _parse_api1(
        self, holiday: Mapping[str, Any], year: int
    ) -> dict[dt.date, HolidayInfo]:
        result: dict[dt.date, HolidayInfo] = {}
        for date_key, info in holiday.items():
            if not isinstance(date_key, str):
                continue
            if isinstance(info, dict) and isinstance(info.get("date"), str):
                date_str = info["date"]
            elif len(date_key) <= 5:
                date_str = f"{year}-{date_key}"
            else:
                date_str = date_key
            try:
                date_obj = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if date_obj.year != year:
                continue
            if isinstance(info, dict):
                name = str(info.get("name") or "节假日")
                is_holiday = bool(info.get("holiday", True))
            else:
                name = str(info)
                is_holiday = True
            result[date_obj] = {"name": name, "is_holiday": is_holiday}
        if not result:
            raise ValueError("未解析到有效节假日数据")
        return result

    def _parse_api2(
        self, items: Iterable[Mapping[str, Any]], year: int
    ) -> dict[dt.date, HolidayInfo]:
        result: dict[dt.date, HolidayInfo] = {}
        for item in items:
            try:
                date_int = int(item["date"])
                workday = int(item["workday"])
            except (KeyError, ValueError, TypeError):
                continue
            try:
                date_obj = dt.datetime.strptime(str(date_int), "%Y%m%d").date()
            except ValueError:
                continue
            if date_obj.year != year:
                continue
            name = str(item.get("name") or "节假日")
            result[date_obj] = {"name": name, "is_holiday": workday == 2}
        if not result:
            raise ValueError("未解析到有效节假日数据")
        return result

    def _download(self, year: int) -> dict[dt.date, HolidayInfo]:
        url = f"http://timor.tech/api/holiday/year/{year}"
        print(_c(f"正在下载 {year} 年节假日数据...", Color.INFO))
        try:
            resp = self._session.get(url, timeout=10, stream=True)
        except Timeout as exc:
            raise exc
        except RequestException as exc:
            raise exc

        with resp:
            resp.raise_for_status()
            total = 0
            try:
                total = int(resp.headers.get("Content-Length", "0"))
            except ValueError:
                total = 0

            if total > 0:
                with tqdm(
                    total=total, unit="B", unit_scale=True, desc="下载", leave=False
                ) as bar:
                    wrapped = tqdm.wrapattr(
                        resp.raw, "read", total=total, callback=bar.update
                    )
                    content = wrapped()
            else:
                content = resp.content

        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("API 响应不是有效 JSON") from exc

        if not isinstance(data, dict):
            raise ValueError("API 响应结构异常")

        holidays = self.parse_holiday_data(data, year)
        self._write_cache(year, holidays)
        print(_c(f"✓ 下载成功（{len(holidays)} 条）", Color.SUCCESS))
        return holidays

    def _read_cache(
        self, year: int, *, max_age_days: int
    ) -> tuple[dict[dt.date, HolidayInfo] | None, bool]:
        path = self.cache_path(year)
        if not os.path.exists(path):
            return None, True

        try:
            with open(path, "r", encoding="utf-8") as f:
                first = f.readline()
                downloaded_at = _parse_downloaded_at(first.strip())
                rest = f.read()
        except OSError:
            return None, True

        expired = True
        json_text = rest

        if downloaded_at is None:
            json_text = first + rest
        else:
            expired = _is_expired(downloaded_at, max_age_days=max_age_days)

        try:
            raw = json.loads(json_text)
        except json.JSONDecodeError:
            return None, True

        if not isinstance(raw, dict):
            return None, True

        result: dict[dt.date, HolidayInfo] = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            try:
                date_obj = dt.datetime.strptime(k, "%Y-%m-%d").date()
            except ValueError:
                continue
            name_val = v.get("name")
            is_holiday_val = v.get("is_holiday")
            if not isinstance(name_val, str) or not isinstance(is_holiday_val, bool):
                continue
            result[date_obj] = {"name": name_val, "is_holiday": is_holiday_val}
        if not result:
            return None, True
        return result, expired

    def _write_cache(self, year: int, holidays: Mapping[dt.date, HolidayInfo]) -> None:
        path = self.cache_path(year)
        downloaded_at = _now_local().isoformat()
        payload: dict[str, HolidayInfo] = {
            d.strftime("%Y-%m-%d"): info for d, info in holidays.items()
        }
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"# downloaded_at: {downloaded_at}\n")
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError:
            return


class DateQueryTool:
    COLORS: Mapping[str, Color] = {
        "header": Color.HEADER,
        "success": Color.SUCCESS,
        "warning": Color.WARNING,
        "info": Color.INFO,
        "error": Color.ERROR,
    }

    def __init__(self, provider: HolidayProvider, *, refresh: bool) -> None:
        self._provider = provider
        self._refresh = refresh
        self.date: dt.date | None = None

    def run(self) -> None:
        print(_c("🔍 具体日期查询工具", Color.SUCCESS))
        print(_c("退出：输入 q/quit/exit", Color.WARNING))

        current_year = dt.datetime.now().year
        year_raw = get_input("请输入查询年份", default=str(current_year))
        if _is_exit_command(year_raw):
            return
        try:
            year = validate_year(year_raw)
        except ValueError as exc:
            print(_c(f"输入错误：{exc}", Color.ERROR))
            return

        self._provider.get_holidays(year, refresh=self._refresh)

        while True:
            user_raw = get_input("请输入月份和日期（如 4.5 或 4月5日）")
            if _is_exit_command(user_raw):
                return
            raw = f"{year}-{user_raw}"
            try:
                self.date = validate_date_input(raw)
            except ValueError as exc:
                print(_c(f"输入错误：{exc}", Color.ERROR))
                continue
            print(self._format_result(self.date))
            if not wait_for_any_key():
                return

    def _format_result(self, date_obj: dt.date) -> str:
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = date_obj.weekday()

        holidays = self._provider.get_holidays(date_obj.year, refresh=False)
        if date_obj in holidays:
            info = holidays[date_obj]
            if info["is_holiday"]:
                status = _c("节假日", Color.ERROR)
                detail = info["name"]
            else:
                status = _c("调休工作", Color.SUCCESS)
                detail = f"{info['name']} (调休)"
        elif weekday >= 5:
            status = _c("周末", Color.WARNING)
            detail = "休息日"
        else:
            status = _c("工作日", Color.SUCCESS)
            detail = "正常工作日"

        header = _c("=" * 50, Color.HEADER)
        lines = [
            "",
            header,
            _c(f"📅 查询结果：{date_obj:%Y-%m-%d}", Color.SUCCESS),
            _c("-" * 50, Color.HEADER),
            _c(f"星期：{weekday_names[weekday]}", Color.INFO),
            f"{status}：{detail}",
            _c(f"天数：该年第 {date_obj.timetuple().tm_yday} 天", Color.INFO),
            header,
            "",
        ]
        return "\n".join(lines)


class WeekdayPossibilityAnalyzer:
    COLORS: Mapping[str, Color] = {
        "header": Color.HEADER,
        "success": Color.SUCCESS,
        "warning": Color.WARNING,
        "info": Color.INFO,
        "error": Color.ERROR,
    }

    def __init__(self) -> None:
        self.date: dt.date | None = None

    def run(self) -> None:
        print(_c("📊 星期几可能性分析工具", Color.SUCCESS))
        print(_c("退出：输入 q/quit/exit", Color.WARNING))

        while True:
            raw = get_input("请输入要分析的月日（如 4.5 或 4月5日）")
            if _is_exit_command(raw):
                return
            try:
                self.date = validate_date_input(raw)
            except ValueError as exc:
                print(_c(f"输入错误：{exc}", Color.ERROR))
                continue

            range_raw = get_input(
                "请输入分析年份范围（如 2020-2030）", default="2020-2030"
            )
            if _is_exit_command(range_raw):
                return
            try:
                start, end = validate_year_range(range_raw)
            except ValueError as exc:
                print(_c(f"输入错误：{exc}", Color.ERROR))
                continue
            if end - start > 200:
                end = start + 200
                print(_c(f"范围过大，已自动调整为 {start}-{end}", Color.WARNING))

            month = self.date.month
            day = self.date.day
            print(self._format_possibilities(month, day, start, end))
            if not wait_for_any_key("按任意键继续分析，或按 'q' 退出..."):
                return

    def _format_possibilities(self, month: int, day: int, start: int, end: int) -> str:
        weekday_stats: dict[int, list[int]] = {i: [] for i in range(7)}
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

        for year in range(start, end + 1):
            try:
                d = dt.date(year, month, day)
            except ValueError:
                continue
            weekday_stats[d.weekday()].append(year)

        weekend_years = sorted(weekday_stats[5] + weekday_stats[6])
        workday_years = sorted(y for i in range(5) for y in weekday_stats[i])

        header = _c("=" * 65, Color.HEADER)
        lines: list[str] = [
            "",
            header,
            _c(f"📊 {month}月{day}日 星期几可能性分析 ({start}-{end})", Color.SUCCESS),
            header,
            _c("📈 各星期几出现情况：", Color.INFO),
        ]

        for weekday, years in weekday_stats.items():
            if not years:
                continue
            color = Color.WARNING if weekday >= 5 else Color.SUCCESS
            years_str = ", ".join(str(y) for y in years[:8])
            if len(years) > 8:
                years_str += f" 等{len(years)}年"
            lines.append(f"  {_c(weekday_names[weekday], color)}：{years_str}")

        valid_years = len(weekend_years) + len(workday_years)
        lines.append("")
        lines.append(_c("📋 统计摘要：", Color.INFO))
        lines.append(f"  分析年份范围：{end - start + 1}年")
        lines.append(f"  有效年份数量：{valid_years}年")
        if valid_years > 0:
            weekend_pct = len(weekend_years) / valid_years * 100
            workday_pct = len(workday_years) / valid_years * 100
            lines.append(f"  周末概率：{weekend_pct:.1f}%")
            lines.append(f"  工作日概率：{workday_pct:.1f}%")
        lines.append(header)
        lines.append("")
        return "\n".join(lines)


_COMMANDS: MutableMapping[str, Callable[[], None]] = {}


def register_command(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def decorator(func: Callable[[], None]) -> Callable[[], None]:
        _COMMANDS[name] = func
        return func

    return decorator


def _show_menu() -> None:
    print(_c("=" * 70, Color.HEADER))
    print(_c("🗓️  日期查询工具套件", Color.SUCCESS))
    print(_c("=" * 70, Color.HEADER))
    print(_c("1) 查询具体日期", Color.INFO))
    print(_c("2) 分析日期可能性", Color.INFO))
    print(_c("3) 清理本地缓存", Color.INFO))
    print(_c("0) 退出", Color.INFO))
    print(_c("=" * 70, Color.HEADER))


def _run_self_test() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


class TestValidators(unittest.TestCase):
    def test_month_day_valid(self) -> None:
        d = validate_date_input("4.5")
        self.assertEqual((d.month, d.day), (4, 5))

    def test_year_month_day_valid(self) -> None:
        d = validate_date_input("2026-01-01")
        self.assertEqual(d, dt.date(2026, 1, 1))

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            validate_date_input("abc")

    def test_edge_leap(self) -> None:
        d = validate_date_input("2000-02-29")
        self.assertEqual(d, dt.date(2000, 2, 29))


class TestCache(unittest.TestCase):
    def test_cache_header_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = HolidayProvider(cache_dir=tmp)
            year = 2026
            provider._write_cache(
                year, {dt.date(2026, 1, 1): {"name": "元旦", "is_holiday": True}}
            )
            with open(provider.cache_path(year), "r", encoding="utf-8") as f:
                first = f.readline().strip()
            self.assertTrue(first.startswith("# downloaded_at: "))

    def test_cache_expired_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = HolidayProvider(cache_dir=tmp)
            year = 2026
            provider._write_cache(
                year, {dt.date(2026, 1, 1): {"name": "元旦", "is_holiday": True}}
            )
            path = provider.cache_path(year)
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            old_ts = (_now_local() - dt.timedelta(days=8)).isoformat()
            lines[0] = f"# downloaded_at: {old_ts}\n"
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines)

            cached, expired = provider._read_cache(year, max_age_days=7)
            self.assertIsNotNone(cached)
            self.assertTrue(expired)


class TestHolidayParser(unittest.TestCase):
    def test_api_error_code(self) -> None:
        provider = HolidayProvider()
        with self.assertRaises(HolidayAPIError):
            provider.parse_holiday_data({"code": 1, "msg": "bad"}, 2026)

    def test_schema_invalid(self) -> None:
        provider = HolidayProvider()
        with self.assertRaises(ValueError):
            provider.parse_holiday_data({"code": 0, "holiday": []}, 2026)

    def test_api1_parse(self) -> None:
        provider = HolidayProvider()
        data = {
            "code": 0,
            "holiday": {
                "01-01": {"holiday": True, "name": "元旦", "date": "2026-01-01"}
            },
        }
        holidays = provider.parse_holiday_data(data, 2026)
        self.assertIn(dt.date(2026, 1, 1), holidays)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Date_Tool")
    parser.add_argument("-r", "--refresh", action="store_true", help="强制刷新缓存")
    parser.add_argument("--self-test", action="store_true", help="运行内置单元测试")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    provider = HolidayProvider()

    @register_command("1")
    def _cmd_query() -> None:
        DateQueryTool(provider, refresh=args.refresh).run()

    @register_command("2")
    def _cmd_analyze() -> None:
        WeekdayPossibilityAnalyzer().run()

    @register_command("3")
    def _cmd_clear_cache() -> None:
        provider.clear_cache()
        wait_for_any_key("按任意键继续...")

    while True:
        _show_menu()
        choice = get_input("请输入功能编号", default="0")
        if choice == "0" or _is_exit_command(choice):
            print(_c("感谢使用！再见", Color.HEADER))
            return 0
        action = _COMMANDS.get(choice)
        if action is None:
            print(_c("无效选择，请输入 0-3", Color.ERROR))
            wait_for_any_key("按任意键继续...")
            continue
        try:
            action()
        except KeyboardInterrupt:
            print(_c("\n已退出当前操作", Color.WARNING))
            continue


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(_c("\n程序已退出", Color.WARNING))
        raise SystemExit(0)
