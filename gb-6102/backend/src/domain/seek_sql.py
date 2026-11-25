from contextlib import contextmanager

from sqlalchemy import text


@contextmanager
def stage_feed(db):
    db.execute(text("""
        CREATE TEMPORARY TABLE chart_notice_feed (
            seq INT NOT NULL PRIMARY KEY, issue_date DATE NOT NULL,
            chart VARCHAR(24) NOT NULL, notice VARCHAR(140) NOT NULL,
            INDEX bulletin_seek (issue_date DESC, seq DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))
    try:
        yield
    finally:
        db.execute(text("DROP TEMPORARY TABLE chart_notice_feed"))


def add_notices(db, records):
    if records:
        db.execute(text("""
            INSERT INTO chart_notice_feed (seq, issue_date, chart, notice)
            VALUES (:seq, :issue_date, :chart, :notice)
        """), records)


def fetch_offset(db, limit, offset):
    return db.execute(text("""
        SELECT seq, issue_date, chart, notice FROM chart_notice_feed
        ORDER BY issue_date DESC, seq DESC LIMIT :limit OFFSET :offset
    """), {"limit": limit, "offset": offset}).mappings().all()


def fetch_cursor(db, limit, cursor, high_water=None):
    if cursor is None:
        return []
    params = {"limit": limit, "day": cursor["issue_date"], "seq": cursor["seq"]}
    fence = ""
    if high_water is not None:
        fence = "AND seq <= :high_water"
        params["high_water"] = high_water
    return db.execute(text(f"""
        SELECT seq, issue_date, chart, notice FROM chart_notice_feed
        WHERE (issue_date < :day OR (issue_date = :day AND seq < :seq)) {fence}
        ORDER BY issue_date DESC, seq DESC LIMIT :limit
    """), params).mappings().all()
