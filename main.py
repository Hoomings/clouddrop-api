import os, uuid, datetime
from io import BytesIO
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from google.cloud import storage, firestore

app = FastAPI(title="CloudDrop")
BUCKET_NAME = os.getenv("BUCKET_NAME", "clouddrop-project-files")
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "clouddrop-project")

storage_client = storage.Client(project=PROJECT_ID)
db = firestore.Client(project=PROJECT_ID)
FILES_COLLECTION = "files"

@app.get("/", response_class=HTMLResponse)
async def home():
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            content = f.read().replace("{{ bucket_name }}", BUCKET_NAME)
        return HTMLResponse(content=content)
    except Exception as e:
        return HTMLResponse(content=f"<h3>Error loading index.html: {str(e)}</h3>", status_code=500)

@app.get("/api/files")
async def list_files():
    try:
        docs = db.collection(FILES_COLLECTION).stream()
        files = []
        for doc in docs:
            d = doc.to_dict()
            d["id"] = doc.id
            files.append(d)
        files.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected")
    file_id = str(uuid.uuid4())
    safe_name = os.path.basename(file.filename)
    gcs_path = f"uploads/{file_id}/{safe_name}"
    try:
        contents = await file.read()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(contents, content_type=file.content_type or "application/octet-stream")

        metadata = {
            "name": safe_name,
            "size": len(contents),
            "content_type": file.content_type or "application/octet-stream",
            "gcs_path": gcs_path,
            "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "download_count": 0
        }
        db.collection(FILES_COLLECTION).document(file_id).set(metadata)
        return {"success": True, "file": {"id": file_id, **metadata}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{file_id}")
async def download_file(file_id: str):
    doc_ref = db.collection(FILES_COLLECTION).document(file_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="File not found")
    data = doc.to_dict()
    doc_ref.update({"download_count": firestore.Increment(1)})
    blob = storage_client.bucket(BUCKET_NAME).blob(data["gcs_path"])
    return StreamingResponse(
        BytesIO(blob.download_as_bytes()),
        media_type=data.get("content_type", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{data["name"]}"'}
    )

@app.delete("/api/files/{file_id}")
async def delete_file(file_id: str):
    doc_ref = db.collection(FILES_COLLECTION).document(file_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="File not found")
    data = doc.to_dict()
    blob = storage_client.bucket(BUCKET_NAME).blob(data["gcs_path"])
    if blob.exists():
        blob.delete()
    doc_ref.delete()
    return {"success": True}
