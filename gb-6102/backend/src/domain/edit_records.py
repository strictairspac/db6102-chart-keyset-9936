from datetime import date


def _records(payload, field, allow_empty):
    records = payload.get(field)
    if not isinstance(records, list) or len(records) > 300 or (not records and not allow_empty):
        raise ValueError(f"{field} 须为符合行数限制的列表（最多 300 行）")
    parsed = []
    for row in records:
        if not isinstance(row, dict) or set(row) != {"seq", "issue_date", "chart", "notice"}:
            raise ValueError(f"{field} 须含 seq、issue_date、chart、notice")
        seq = row["seq"]
        if type(seq) is not int or not 1 <= seq <= 2147483647:
            raise ValueError("收录流水号须为正整数，最大 2147483647")
        issue_date = row["issue_date"]
        try:
            issue = date.fromisoformat(issue_date)
        except (ValueError, TypeError) as exc:
            raise ValueError("签发日须用 YYYY-MM-DD") from exc
        if issue.year < 1970 or issue.isoformat() != issue_date:
            raise ValueError("签发日须用 1970 年以后的 YYYY-MM-DD 日期")
        for key, limit in (("chart", 24), ("notice", 140)):
            if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > limit:
                raise ValueError(f"{key} 须为不超过 {limit} 字符的内容")
        parsed.append({**row, "issue_date": issue})
    return parsed


def parse(payload):
    initial = _records(payload, "initial", False)
    arrivals = _records(payload, "arrivals", True)
    ids = [row["seq"] for row in initial + arrivals]
    if len(ids) != len(set(ids)):
        raise ValueError("收录流水号不能重复")
    high_water = max(row["seq"] for row in initial)
    if any(row["seq"] <= high_water for row in arrivals):
        raise ValueError("新到记录的收录流水号必须超过首屏水位")
    return initial, arrivals, high_water


def validate(payload):
    parse(payload)
