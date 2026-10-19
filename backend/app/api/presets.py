from fastapi import Depends, HTTPException
from app.api.contracts import ContractRouter as APIRouter
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
    from app.services.registry.presets import ProtectedPresetError

    try:
        ok = delete_preset(db, preset_id)
    except ProtectedPresetError:
        raise HTTPException(status_code=403, detail=f"正式预设 {preset_id} 受保护，禁止删除（可改为停用/编辑）")
    if not ok:
        raise HTTPException(status_code=404, detail="preset not found")
    return {"deleted": preset_id}


@router.patch("/{preset_id}")
def patch_preset(preset_id: str, payload: dict, db: Session = Depends(get_db)):
    preset = update_preset(db, preset_id, payload)
    if preset is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return preset
