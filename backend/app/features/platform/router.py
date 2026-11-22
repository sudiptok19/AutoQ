from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.platform import service

router = APIRouter(tags=["platform"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    return service.health(db)
