from fastapi import FastAPI

from app.mac_tools import open_app, open_website, open_path
from app.ai import ask_ai
from app.agent import run_agent


app = FastAPI(
    title="Nexa",
    description="Local-first AI desktop agent for macOS",
    version="0.1.0",
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


@app.post("/tools/open-website")
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
def agent(message: str):
    return run_agent(message)