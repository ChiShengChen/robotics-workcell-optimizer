import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import cad, chat, examples, extract, layout, optimize, score

load_dotenv()

app = FastAPI(
    title="XYZ Robotics Workcell Layout Optimizer",
    version="0.1.0",
    description="LLM-driven palletizing workcell layout optimizer.",
)

# CORS: localhost for dev, Vercel preview + production for deployed demo,
# plus any domains the operator wants to allow via CORS_EXTRA_ORIGINS env
# var (comma-separated). The Vercel preview pattern matches every PR
# preview URL (`<project>-<hash>-<team>.vercel.app`).
_extra = [o.strip() for o in os.getenv("CORS_EXTRA_ORIGINS", "").split(",") if o.strip()]
_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    *_extra,
]
_origin_regex = (
    r"https://([a-z0-9-]+\.)?vercel\.app"  # Vercel previews + production
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


app.include_router(extract.router, prefix="/api")
app.include_router(layout.router, prefix="/api")
app.include_router(score.router, prefix="/api")
app.include_router(optimize.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(examples.router, prefix="/api")
app.include_router(cad.router, prefix="/api")
