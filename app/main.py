import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.database import init_db
from app.poller import poll_all
from app.routes import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized")

    async def run_poller():
        while True:
            await asyncio.to_thread(poll_all)
            await asyncio.sleep(600)
    
    task = asyncio.create_task(run_poller())
    logger.info("Poller task started")

    yield

    task.cancel()
    logger.info("Poller task cancelled")


app = FastAPI(lifespan=lifespan)
app.include_router(router)