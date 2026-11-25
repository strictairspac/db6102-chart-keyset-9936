from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from src.constants.job_state import ReceiptState
from src.models.reviews import reviews
from . import audit, runs


def get(db, run_id):
    row = db.execute(select(reviews).where(reviews.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def sign(db, run_id, data, actor):
    run = runs.get(db, run_id)
    if run["status"] != ReceiptState.DONE:
        raise HTTPException(409, "回执已就绪后才能签核")
    try:
        db.execute(insert(reviews).values(run_id=run_id, note=data.note, actor=actor, created_at=int(time())))
    except IntegrityError as exc:
        raise HTTPException(409, "该回执已经签核") from exc
    audit.record(db, "review", actor, "run", run_id)
    return get(db, run_id)
