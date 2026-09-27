"""Deterministic Cypher temporal construction and expression semantics."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

from ...temporal import (
    TemporalDate,
    TemporalDuration,
    TemporalInstant,
    TemporalLocalDateTime,
    TemporalLocalTime,
    TemporalTime,
)

MICROS_PER_SECOND = 1_000_000
MICROS_PER_DAY = 86_400 * MICROS_PER_SECOND
TEMPORAL_TYPES = (
    TemporalDate,
    TemporalTime,
    TemporalLocalTime,
    TemporalInstant,
    TemporalLocalDateTime,
    TemporalDuration,
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOCAL_TIME = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$")
_OFFSET_TIME = re.compile(r"^(.*?)(Z|z|[+-]\d{2}:\d{2})$")
_LOCAL_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$"
)
_DURATION = re.compile(
    r"^(?P<sign>-)?P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)(?:\.(?P<fraction>\d{1,6}))?S)?)?$"
)


def construct_temporal(name: str, value: object) -> object:
    """Construct one Cypher temporal value from a string, map, or typed value."""
    if value is None:
        return None
    constructors = {
        "date": _date,
        "time": _time,
        "localtime": _local_time,
        "datetime": _datetime,
        "localdatetime": _local_datetime,
        "duration": _duration,
    }
    try:
        return constructors[name](value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid value for {name}(): {value!r}") from exc


def normalize_parameter(value: object) -> object:
    """Recursively normalize Python temporal query parameters."""
    if isinstance(value, TEMPORAL_TYPES):
        return value
    if isinstance(value, datetime):
        return TemporalInstant.from_datetime(value) if value.tzinfo else TemporalLocalDateTime(value)
    if isinstance(value, date):
        return TemporalDate(value)
    if isinstance(value, time):
        if value.tzinfo is None:
            return TemporalLocalTime.from_time(value)
        offset = value.utcoffset()
        if offset is None:
            raise ValueError("time parameter has an indeterminate UTC offset")
        seconds = offset.total_seconds()
        if not seconds.is_integer() or int(seconds) % 60:
            raise ValueError("time parameter UTC offset must use whole minutes")
        return TemporalTime(_time_micros(value), int(seconds))
    if isinstance(value, timedelta):
        return TemporalDuration.from_timedelta(value)
    if isinstance(value, list):
        return [normalize_parameter(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_parameter(item) for item in value)
    if isinstance(value, dict):
        return {key: normalize_parameter(item) for key, item in value.items()}
    return value


def temporal_property(value: object, name: str) -> tuple[bool, object]:
    """Return whether ``name`` is a temporal component and its value."""
    parts: dict[str, object]
    if isinstance(value, TemporalDate):
        parts = _date_parts(value.value)
    elif isinstance(value, TemporalLocalDateTime):
        parts = {**_date_parts(value.value.date()), **_clock_parts(_time_micros(value.value.time()))}
    elif isinstance(value, TemporalInstant):
        moment = value.to_datetime()
        parts = {**_date_parts(moment.date()), **_clock_parts(_time_micros(moment.time())), "offsetSeconds": 0}
    elif isinstance(value, TemporalLocalTime):
        parts = _clock_parts(value.microseconds)
    elif isinstance(value, TemporalTime):
        parts = {**_clock_parts(value.local_microseconds), "offsetSeconds": value.offset_seconds}
    elif isinstance(value, TemporalDuration):
        parts = {
            "days": value.total_microseconds // MICROS_PER_DAY,
            "seconds": value.total_microseconds / MICROS_PER_SECOND,
            "microseconds": value.total_microseconds,
        }
    else:
        return False, None
    if name not in parts:
        raise ValueError(f"Temporal value has no component {name!r}")
    return True, parts[name]


def temporal_compare_key(value: object) -> tuple[str, object] | None:
    """Return a type-tagged chronological key, or ``None`` for non-temporal values."""
    if isinstance(value, TemporalDate):
        return "date", value.value.toordinal()
    if isinstance(value, TemporalLocalTime):
        return "localtime", value.microseconds
    if isinstance(value, TemporalTime):
        return "time", value.utc_microseconds
    if isinstance(value, TemporalInstant):
        return "datetime", value.epoch_microseconds
    if isinstance(value, TemporalLocalDateTime):
        return "localdatetime", value.value
    if isinstance(value, TemporalDuration):
        return "duration", value.total_microseconds
    return None


def temporal_arithmetic(operator: str, left: object, right: object) -> tuple[bool, object]:
    """Evaluate supported exact temporal arithmetic."""
    if isinstance(left, TemporalDuration) and isinstance(right, TemporalDuration):
        if operator in {"+", "-"}:
            sign = 1 if operator == "+" else -1
            return True, TemporalDuration(left.total_microseconds + sign * right.total_microseconds)
    if isinstance(right, TemporalDuration) and operator in {"+", "-"}:
        delta = right.total_microseconds * (1 if operator == "+" else -1)
        if isinstance(left, TemporalInstant):
            return True, TemporalInstant(left.epoch_microseconds + delta)
        if isinstance(left, TemporalLocalDateTime):
            return True, TemporalLocalDateTime(left.value + timedelta(microseconds=delta))
        if isinstance(left, TemporalDate):
            if delta % MICROS_PER_DAY:
                raise ValueError("date arithmetic requires a whole-day duration")
            return True, TemporalDate(left.value + timedelta(days=delta // MICROS_PER_DAY))
        if isinstance(left, TemporalLocalTime):
            return True, TemporalLocalTime((left.microseconds + delta) % MICROS_PER_DAY)
        if isinstance(left, TemporalTime):
            return True, TemporalTime((left.local_microseconds + delta) % MICROS_PER_DAY, left.offset_seconds)
    if isinstance(left, TemporalDuration) and operator == "+":
        handled, result = temporal_arithmetic("+", right, left)
        if handled:
            return True, result
    if operator == "-" and type(left) is type(right):
        if isinstance(left, TemporalDate):
            return True, TemporalDuration((left.value - right.value).days * MICROS_PER_DAY)
        if isinstance(left, TemporalLocalDateTime):
            return True, TemporalDuration.from_timedelta(left.value - right.value)
        if isinstance(left, TemporalInstant):
            return True, TemporalDuration(left.epoch_microseconds - right.epoch_microseconds)
        if isinstance(left, TemporalLocalTime):
            return True, TemporalDuration(left.microseconds - right.microseconds)
        if isinstance(left, TemporalTime):
            return True, TemporalDuration(left.utc_microseconds - right.utc_microseconds)
    return isinstance(left, TEMPORAL_TYPES) or isinstance(right, TEMPORAL_TYPES), None


def temporal_to_string(value: object) -> str | None:
    """Format a temporal value in canonical ISO form."""
    if isinstance(value, TemporalDate):
        return value.value.isoformat()
    if isinstance(value, TemporalLocalTime):
        return _format_clock(value.microseconds)
    if isinstance(value, TemporalTime):
        offset = value.offset_seconds
        suffix = "Z" if offset == 0 else f"{'+' if offset >= 0 else '-'}{abs(offset) // 3600:02d}:{abs(offset) % 3600 // 60:02d}"
        return _format_clock(value.local_microseconds) + suffix
    if isinstance(value, TemporalInstant):
        return value.to_datetime().isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, TemporalLocalDateTime):
        return value.value.isoformat(timespec="microseconds")
    if isinstance(value, TemporalDuration):
        total = value.total_microseconds
        sign = "-" if total < 0 else ""
        total = abs(total)
        days, remainder = divmod(total, MICROS_PER_DAY)
        hours, remainder = divmod(remainder, 3_600 * MICROS_PER_SECOND)
        minutes, remainder = divmod(remainder, 60 * MICROS_PER_SECOND)
        seconds, micros = divmod(remainder, MICROS_PER_SECOND)
        fraction = f".{micros:06d}".rstrip("0") if micros else ""
        date_part = f"{days}D" if days else ""
        time_part = f"{hours}H{minutes}M{seconds}{fraction}S"
        return f"{sign}P{date_part}T{time_part}"
    return None


def _date(value: object) -> TemporalDate:
    if isinstance(value, TemporalDate):
        return value
    if isinstance(value, datetime):
        raise TypeError("date() does not accept datetime")
    if isinstance(value, date):
        return TemporalDate(value)
    if isinstance(value, str) and _DATE.fullmatch(value):
        return TemporalDate(date.fromisoformat(value))
    if isinstance(value, dict):
        if value.keys() != {"year", "month", "day"}:
            raise ValueError("date map requires exactly year, month, and day")
        return TemporalDate(date(_component(value, "year"), _component(value, "month"), _component(value, "day")))
    raise TypeError


def _local_time(value: object) -> TemporalLocalTime:
    if isinstance(value, TemporalLocalTime):
        return value
    if isinstance(value, time):
        return TemporalLocalTime.from_time(value)
    if isinstance(value, str):
        return TemporalLocalTime(_parse_clock(value))
    if isinstance(value, dict):
        return TemporalLocalTime(_clock_from_map(value))
    raise TypeError


def _time(value: object) -> TemporalTime:
    if isinstance(value, TemporalTime):
        return value
    if isinstance(value, time) and value.tzinfo is not None:
        offset = value.utcoffset()
        if offset is not None:
            seconds = offset.total_seconds()
            if seconds.is_integer() and int(seconds) % 60 == 0:
                return TemporalTime(_time_micros(value), int(seconds))
    if isinstance(value, str):
        match = _OFFSET_TIME.fullmatch(value)
        if match:
            return TemporalTime(_parse_clock(match.group(1)), _parse_offset(match.group(2)))
    if isinstance(value, dict):
        if "offset" not in value:
            raise ValueError("time map requires offset")
        return TemporalTime(_clock_from_map(value), _parse_offset(str(value["offset"])))
    raise TypeError


def _datetime(value: object) -> TemporalInstant:
    if isinstance(value, TemporalInstant):
        return value
    if isinstance(value, datetime) and value.tzinfo is not None:
        return TemporalInstant.from_datetime(value)
    if isinstance(value, str):
        return TemporalInstant.parse(value)
    if isinstance(value, dict):
        if "offset" not in value:
            raise ValueError("datetime map requires offset")
        offset = timezone(timedelta(seconds=_parse_offset(str(value["offset"]))))
        moment = datetime(*_datetime_components(value), tzinfo=offset)
        return TemporalInstant.from_datetime(moment)
    raise TypeError


def _local_datetime(value: object) -> TemporalLocalDateTime:
    if isinstance(value, TemporalLocalDateTime):
        return value
    if isinstance(value, datetime) and value.tzinfo is None:
        return TemporalLocalDateTime(value)
    if isinstance(value, str) and _LOCAL_DATETIME.fullmatch(value):
        return TemporalLocalDateTime(datetime.fromisoformat(value))
    if isinstance(value, dict):
        return TemporalLocalDateTime(datetime(*_datetime_components(value)))
    raise TypeError


def _duration(value: object) -> TemporalDuration:
    if isinstance(value, TemporalDuration):
        return value
    if isinstance(value, timedelta):
        return TemporalDuration.from_timedelta(value)
    if isinstance(value, dict):
        allowed = {"days", "hours", "minutes", "seconds", "milliseconds", "microseconds"}
        if not value.keys() <= allowed:
            raise ValueError("unknown duration component")
        total = sum(_number(value, key, 0) * factor for key, factor in {
            "days": MICROS_PER_DAY, "hours": 3_600 * MICROS_PER_SECOND,
            "minutes": 60 * MICROS_PER_SECOND, "seconds": MICROS_PER_SECOND,
            "milliseconds": 1_000, "microseconds": 1,
        }.items())
        if not float(total).is_integer():
            raise ValueError("duration must resolve to whole microseconds")
        return TemporalDuration(int(total))
    if isinstance(value, str):
        match = _DURATION.fullmatch(value)
        if match and any(match.group(key) for key in ("days", "hours", "minutes", "seconds")):
            fraction = (match.group("fraction") or "").ljust(6, "0")
            total = (
                int(match.group("days") or 0) * MICROS_PER_DAY
                + int(match.group("hours") or 0) * 3_600 * MICROS_PER_SECOND
                + int(match.group("minutes") or 0) * 60 * MICROS_PER_SECOND
                + int(match.group("seconds") or 0) * MICROS_PER_SECOND
                + int(fraction or 0)
            )
            return TemporalDuration(-total if match.group("sign") else total)
    raise TypeError


def _component(values: dict, name: str, default: int | None = None) -> int:
    if name not in values:
        if default is not None:
            return default
        raise ValueError(f"missing {name}")
    value = values[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    return value


def _number(values: dict, name: str, default: int) -> int | float:
    value = values.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    return value


def _datetime_components(values: dict) -> tuple[int, ...]:
    allowed = {"year", "month", "day", "hour", "minute", "second", "microsecond", "offset"}
    if not values.keys() <= allowed:
        raise ValueError("unknown datetime component")
    return (
        _component(values, "year"), _component(values, "month"), _component(values, "day"),
        _component(values, "hour", 0), _component(values, "minute", 0),
        _component(values, "second", 0), _component(values, "microsecond", 0),
    )


def _clock_from_map(values: dict) -> int:
    allowed = {"hour", "minute", "second", "microsecond", "offset"}
    if not values.keys() <= allowed:
        raise ValueError("unknown time component")
    return (((_component(values, "hour") * 60 + _component(values, "minute", 0)) * 60
             + _component(values, "second", 0)) * MICROS_PER_SECOND
            + _component(values, "microsecond", 0))


def _parse_clock(value: str) -> int:
    match = _LOCAL_TIME.fullmatch(value)
    if match is None:
        raise ValueError("invalid local time")
    hour, minute, second = (int(match.group(index) or 0) for index in range(1, 4))
    micros = int((match.group(4) or "").ljust(6, "0") or 0)
    result = ((hour * 60 + minute) * 60 + second) * MICROS_PER_SECOND + micros
    return TemporalLocalTime(result).microseconds


def _parse_offset(value: str) -> int:
    if value in {"Z", "z", "+00:00", "-00:00"}:
        return 0
    if re.fullmatch(r"[+-]\d{2}:\d{2}", value) is None:
        raise ValueError("invalid UTC offset")
    hours, minutes = map(int, value[1:].split(":"))
    if hours > 23 or minutes > 59:
        raise ValueError("invalid UTC offset")
    return (1 if value[0] == "+" else -1) * (hours * 3600 + minutes * 60)


def _time_micros(value: time) -> int:
    return ((value.hour * 60 + value.minute) * 60 + value.second) * MICROS_PER_SECOND + value.microsecond


def _date_parts(value: date) -> dict[str, int]:
    return {"year": value.year, "month": value.month, "day": value.day,
            "quarter": (value.month - 1) // 3 + 1, "week": value.isocalendar().week,
            "dayOfWeek": value.isoweekday(), "ordinalDay": value.timetuple().tm_yday}


def _clock_parts(value: int) -> dict[str, int]:
    hour, remainder = divmod(value, 3_600 * MICROS_PER_SECOND)
    minute, remainder = divmod(remainder, 60 * MICROS_PER_SECOND)
    second, microsecond = divmod(remainder, MICROS_PER_SECOND)
    return {"hour": hour, "minute": minute, "second": second,
            "millisecond": microsecond // 1_000, "microsecond": microsecond}


def _format_clock(value: int) -> str:
    parts = _clock_parts(value)
    fraction = f".{parts['microsecond']:06d}" if parts["microsecond"] else ""
    return f"{parts['hour']:02d}:{parts['minute']:02d}:{parts['second']:02d}{fraction}"
