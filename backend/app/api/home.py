import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.market_data import MarketDataError
from app.services.workbench_service import bootstrap_workbench_state, get_initial_workbench_state

router = APIRouter(prefix="/api/home", tags=["home"])
logger = logging.getLogger(__name__)


@router.get("/bootstrap")
def bootstrap(db: Session = Depends(get_db)):
    try:
        return bootstrap_workbench_state(db)
    except MarketDataError as exc:
        logger.exception("bootstrap failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/initial")
def initial(db: Session = Depends(get_db)):
    try:
        return get_initial_workbench_state(db)
    except MarketDataError as exc:
        logger.exception("initial bootstrap failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
