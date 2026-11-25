from .audit import compare
from .edit_records import parse, validate

CONFIG = {
    "title": "澄锚海道·海图勘误翻页核收",
    "team": "澄锚海图发行处（9 人）",
    "description": "勘误签发和入库顺序可能不同，换页中到达的新单不能让旧单漏领或领两次。",
    "direction": "查询优化 / 复合游标分页",
    "mechanism": "MySQL 复合索引按签发日和收录流水号寻址，用固定收录水位隔离后入队的勘误。",
    "modules": ["勘误交班", "签发与收录", "翻页回放", "勘误核收单"],
    "unit": "勘误收录批次",
    "max_parameter": 50,
    "parameter_label": "每页条数",
    "default_parameter": 2,
    "facts": ["OFFSET 重复", "实时游标混入", "水位游标异常"],
    "palette": 2,
    "fixture": "CN-1124-航标勘误",
}


def notice(seq, issue_date, chart, amendment):
    return {"seq": seq, "issue_date": issue_date, "chart": chart, "notice": amendment}


BULLETIN = [
    notice(105, "2026-11-23", "CN-12301", "外堤浮标灯质更正为黄闪"),
    notice(104, "2026-11-23", "CN-12301", "东锚地界线北移"),
    notice(103, "2026-11-23", "CN-12302", "西汊浅点深度更正"),
    notice(102, "2026-11-20", "CN-12310", "航道施工区撤销"),
    notice(101, "2026-11-19", "CN-12310", "南引桥限高复核"),
]

FIXTURES = [
    {
        "code": "CN-1124-NAV", "title": "CN-1124-航标勘误", "description": "领首屏后收两单，一新一补录。",
        "payload": {
            "initial": BULLETIN,
            "arrivals": [
                notice(106, "2026-11-24", "CN-12311", "新增通航浮标"),
                notice(107, "2026-11-21", "CN-12311", "补录北港灯塔弧区"),
            ],
        },
    },
    {
        "code": "CN-1124-QT", "title": "安静窗口回放", "description": "交接窗口没有后到勘误。",
        "payload": {"initial": BULLETIN[2:], "arrivals": []},
    },
]


def run(payload, parameter, db):
    if type(parameter) is not int or not 1 <= parameter <= 50:
        raise ValueError("每页条数须为 1–50")
    initial, arrivals, water = parse(payload)
    expected = {row["seq"] for row in initial}
    streams = compare(db, initial, arrivals, water, parameter)
    metrics = {}
    rows = []
    for strategy, stream in streams:
        seen = set()
        duplicate = 0
        mixed = 0
        for page, row in stream:
            seq = row["seq"]
            repeated = seq in seen
            mixed += seq not in expected
            duplicate += repeated
            seen.add(seq)
            mark = "重复" if repeated else "后到混入" if seq not in expected else "首屏批原件"
            rows.append({
                "方式": strategy, "页": page, "流水": seq, "签发": row["issue_date"].isoformat(),
                "海图": row["chart"], "勘误": row["notice"], "核收标记": mark,
            })
        missing_ids = expected - seen
        for notice in initial:
            if notice["seq"] in missing_ids:
                rows.append({
                    "方式": strategy, "页": None, "流水": notice["seq"], "签发": notice["issue_date"].isoformat(),
                    "海图": notice["chart"], "勘误": notice["notice"], "核收标记": "漏领",
                })
        metrics[strategy] = (duplicate, mixed, len(missing_ids))
    duplicates, _, _ = metrics["实时 OFFSET"]
    _, intrusions, _ = metrics["实时游标"]
    fenced = sum(metrics["水位游标"])
    return {
        "summary": f"首屏水位 {water}，应核收 {len(initial)} 条。水位游标检出 {fenced} 项差异。",
        "metrics": {"OFFSET 重复": duplicates, "实时游标混入": intrusions, "水位游标异常": fenced},
        "rows": rows,
    }
