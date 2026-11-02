from fastapi import APIRouter, Depends

from .helpers import clean

router = APIRouter()
app = None


def get_db():
    return None


def verify_token():
    return True


@router.post("/answer")
def answer(db=Depends(get_db), user=Depends(verify_token)):
    return clean(db)


@router.delete("/document/{doc_id}")
def delete_document(doc_id: int):
    return doc_id


@app.middleware("http")
async def add_header(request, call_next):
    return await call_next(request)
