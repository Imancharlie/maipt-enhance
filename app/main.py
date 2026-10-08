from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import weekly
from app.config import settings

app = FastAPI(title="MiPT AI Enhancement Service")

# CORS - allow PythonAnywhere backend and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mipt.pythonanywhere.com",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8010",
        "http://127.0.0.1:8010",
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(weekly.router, prefix="/api/weekly", tags=["weekly"])

@app.get("/")
async def root():
    return {
        "service": "MiPT AI Enhancement Service",
        "version": "1.0.0",
        "status": "running"
    }
