from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.workbench_service import create_preset, delete_preset, list_presets, update_preset

router = APIRouter(prefix="/api/presets", tags=["presets"])


@router.get("")
def get_presets(db: Session = Depends(get_db)):
    return list_presets(db)


@router.post("")
def post_preset(payload: dict, db: Session = Depends(get_db)):
    return create_preset(db, payload)


@router.delete("/{preset_id}")
def delete(preset_id: str, db: Session = Depends(get_db)):
    ok = delete_preset(db, preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="preset not found")
    return {"deleted": preset_id}


@router.patch("/{preset_id}")
def patch_preset(preset_id: str, payload: dict, db: Session = Depends(get_db)):
    preset = update_preset(db, preset_id, payload)
    if preset is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return preset
