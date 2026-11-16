from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

app = FastAPI(title="AI Security & Deepfake Lab API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"message": "AI Security & Deepfake Lab Backend đang hoạt động!"}


@app.get("/api/v1/labs")
def get_labs():
    return [
        {"id": 1, "name": "Deepfake & Identity Spoofing", "status": "active"},
        {"id": 2, "name": "Prompt Injection trên Chatbot", "status": "active"},
        {"id": 3, "name": "API Security & Rate Limiting", "status": "active"},
        {"id": 4, "name": "Data Poisoning (Nâng cao)", "status": "planned"},
    ]