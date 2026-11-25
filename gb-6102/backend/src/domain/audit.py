from .seek_sql import add_notices, fetch_cursor, fetch_offset, stage_feed


def _walk_remaining(fetch, first, size):
    seen = []
    cursor = first
    page = 2
    while cursor is not None:
        fetched = fetch(cursor, (page - 1) * size)
        seen.extend((page, row) for row in fetched)
        if len(fetched) < size:
            break
        cursor = fetched[-1]
        page += 1
    return seen


def compare(db, initial, arrivals, water, size):
    with stage_feed(db):
        add_notices(db, initial)
        first_page = fetch_offset(db, size, 0)
        add_notices(db, arrivals)
        last = first_page[-1]
        strategies = {
            "实时 OFFSET": lambda cursor, offset: fetch_offset(db, size, offset),
            "实时游标": lambda cursor, offset: fetch_cursor(db, size, cursor),
            "水位游标": lambda cursor, offset: fetch_cursor(db, size, cursor, water),
        }
        return [
            (strategy, [(1, row) for row in first_page] + _walk_remaining(fetch, last, size))
            for strategy, fetch in strategies.items()
        ]
