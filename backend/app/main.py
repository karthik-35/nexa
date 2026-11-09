from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.mac_tools import open_app, open_website, open_path
from app.ai import ask_ai
from app.agent import run_agent
from app.browser_tools import close_browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A whole observe/act workflow owns the synchronous Playwright thread.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="nexa-agent") as worker:
        app.state.agent_worker = worker
        try:
            yield
        finally:
            worker.submit(close_browser).result()


app = FastAPI(
    title="Nexa",
    description="Local-first AI desktop agent for macOS",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
def home():
    return {
        "name": "Nexa",
        "description": "AI Desktop Agent for macOS",
        "version": "0.1.0",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/tools/open-app/{app_name}")
def launch_app(app_name: str):
    return open_app(app_name)


@app.post("/tools/open-website", summary="Open a website in the macOS default browser (outside Nexa)")
def launch_website(url: str):
    return open_website(url)

@app.post("/tools/open-path")
def launch_path(path: str):
    return open_path(path)

@app.post("/ai/chat")
def chat(message: str):
    return {
        "response": ask_ai(message)
    }
  
@app.post("/agent")
def agent(message: str, request: Request):
    return request.app.state.agent_worker.submit(run_agent, message).result()
