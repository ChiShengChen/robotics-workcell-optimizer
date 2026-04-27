from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, extract, layout, optimize, score

load_dotenv()

app = FastAPI(
    title="XYZ Robotics Workcell Layout Optimizer",
    version="0.1.0",
    description="LLM-driven palletizing workcell layout optimizer.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
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
