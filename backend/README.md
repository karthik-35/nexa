# Nexa

**Nexa** is a local-first AI desktop agent for macOS that combines large language models with browser and desktop automation.

Instead of only answering questions, Nexa can understand natural-language commands, decide which tools are needed, execute actions on the computer, observe the result, and continue working through multi-step tasks.

The project is currently under active development.

---

## Overview

Traditional AI assistants mainly generate text. Nexa is designed to go one step further by allowing an AI model to interact with software through a controlled set of tools.

Example commands:

```text
Search YouTube for AI agents for beginners
```

```text
Open Spotify
```

```text
Read the current webpage and summarize it
```

```text
Click Filters
```

```text
Scroll down
```

```text
Look at the current screen and identify the important elements
```

Nexa uses an AI agent to determine which approved tool should be called and what arguments should be passed to it.

---

## How Nexa Works

```text
User Command
     ↓
FastAPI Backend
     ↓
OpenAI Agent
     ↓
Tool Selection
     ↓
Safety / Validation Layer
     ↓
macOS / Browser / Vision Tools
     ↓
Action Executed
     ↓
Result Returned to Agent
```

The AI model handles reasoning and tool selection, while the actual computer actions are performed by deterministic Python functions.

This keeps the language model separated from direct system execution.

---

## Current Features

### AI Agent

Nexa uses OpenAI tool calling to translate natural-language instructions into structured actions.

The agent can perform multiple steps when required.

Example:

```text
Search YouTube for Python tutorials and open the first result.
```

Nexa can perform:

```text
search_youtube
        ↓
open_first_youtube_result
```

The result of each tool call is returned to the model before it decides whether another action is necessary.

---

## macOS Application Control

Nexa can open approved macOS applications.

Currently supported applications include:

- Google Chrome
- Safari
- Visual Studio Code
- Spotify
- Finder
- Zoom
- Photos
- Microsoft Teams
- WhatsApp

Applications are restricted through an allowlist instead of allowing arbitrary shell commands.

Example:

```text
Open Spotify
```

---

## Browser Automation

Browser automation is powered by **Playwright** using a dedicated persistent Chromium profile.

Current browser capabilities include:

### Open a Website in Nexa

```text
Open YouTube
```

`open_browser_url(url)` opens HTTP/HTTPS websites in the existing Nexa Chromium
page, using `~/.nexa/browser-profile`. Subsequent searches, reading, clicks, and
vision actions use that same controlled browser. Opening YouTube alone navigates
to its homepage without searching or opening a video.

`open_website(url)` and `POST /tools/open-website` are separate: they launch the
normal macOS default browser. The agent should choose this tool only for an
explicit request such as "Open YouTube in my default browser." Nexa cannot use
its Playwright tools to control that browser.

Both URL tools accept only HTTP/HTTPS addresses with a hostname, reject embedded
credentials and malformed URLs, and default bare domains such as `youtube.com`
to HTTPS. Include the scheme for local services with ports, such as
`http://localhost:8000`. This is URL validation, not a website trust allowlist.

### YouTube Search

```text
Search YouTube for machine learning tutorials
```

### Open First Search Result

```text
Open the first result
```

### Navigate Back

```text
Go back
```

### Refresh Page

```text
Refresh the page
```

### Read Current Page

```text
Read this page
```

Nexa extracts visible webpage text and sends relevant information back to the agent.

### Click Visible Text

```text
Click Filters
```

Nexa can interact with webpage elements using their visible text.

### Type Into Input Fields

```text
Type "AI agents" into Search
```

### Press Enter

```text
Press Enter
```

### Continuous Scrolling

```text
Scroll down
```

or:

```text
Scroll up
```

Scrolling continues smoothly until it is explicitly stopped or interrupted.

### Inspect Page Elements

Nexa can inspect visible buttons and links, including icon buttons identified by
ARIA labels, associated labels, or titles. Observations include an `observation_id`
and element IDs. `click_element` uses the observed DOM element with Playwright's
visibility and actionability checks; the model cannot supply an arbitrary selector.

---

## Computer Vision

Nexa can capture the current browser screen and use an AI vision model to understand what is visible.

The vision system can identify:

- Buttons
- Links
- Input fields
- Tabs
- Menus
- Icons
- Other interactive elements

Example:

```text
Look at the current screen and tell me what is visible.
```

Nexa captures the current browser viewport and sends the screenshot to the vision model for analysis.

---

## Visual Coordinate Actions

Some webpage elements do not contain visible text.

Examples include:

- Microphone icons
- Menu icons
- Image buttons
- Graphical controls

For these cases, Nexa uses:

```text
Screenshot + DOM observation
    ↓
Vision Analysis
    ↓
Observed DOM target ID (preferred) or recorded vision coordinates
    ↓
Click → inspect resulting page/dialog state
```

Screenshots use `scale="css"`: one image pixel equals one viewport CSS pixel,
including on Retina displays. Nexa checks the PNG dimensions against the viewport
and tells the vision model the original image dimensions. Vision returns structured
page, summary, and element data; visual descriptions are never assumed to be visible text.

`click_coordinates(x, y, observation_id)` accepts only exact fallback coordinates
returned by the latest vision observation. Observations expire after two minutes
and are invalidated by actions or new observations. Page, viewport, and scroll
changes reject stale targets. Coordinate fallback also requires an unchanged
screenshot; animated/video pages may therefore require DOM targeting.

Coordinate localization remains experimental: these checks prevent invented or
stale agent coordinates, but cannot guarantee that the vision model localized an
unlabeled target correctly. A successful click dispatch is **not** task completion.
Click tools return `action_executed` separately from `verification`, including new
dialogs and the resulting page text. The agent must compare that evidence with the
requested outcome and report uncertainty when it cannot verify it.

Text and DOM-based interaction remain the preferred method whenever possible.

---

## Safety Design

Nexa does **not** give the AI unrestricted access to the computer.

Instead, the model can only request approved tools.

For example:

```python
open_app(...)
click_text(...)
type_text(...)
scroll_page(...)
analyze_current_screen(...)
```

The Python backend validates and executes the action.

This architecture prevents the model from directly running arbitrary shell commands.

Future versions will add explicit confirmation before sensitive actions such as:

- Sending messages
- Making purchases
- Deleting files
- Changing account settings
- Submitting forms
- Performing irreversible actions

---

## Technology Stack

### Backend

- Python
- FastAPI
- Uvicorn

### AI

- OpenAI API
- OpenAI Responses API
- Function / Tool Calling
- Vision

### Browser Automation

- Playwright
- Chromium

### macOS Automation

- Python
- macOS `open` command
- Controlled application allowlists

### Development

- Git
- GitHub
- Pytest

---

## Project Structure

```text
nexa/
│
└── backend/
    │
    ├── app/
    │   ├── __init__.py
    │   ├── main.py
    │   ├── ai.py
    │   ├── agent.py
    │   ├── mac_tools.py
    │   └── browser_tools.py
    │
    ├── tests/
    │   ├── __init__.py
    │   └── test_health.py
    │
    ├── .env
    ├── .env.example
    ├── .gitignore
    ├── README.md
    └── requirements.txt
```

---

## File Responsibilities

### `main.py`

Contains the FastAPI application and API endpoints.

Important endpoints include:

```text
GET  /
GET  /health

POST /tools/open-app/{app_name}
POST /tools/open-website
POST /tools/open-path

POST /ai/chat
POST /agent
```

---

### `ai.py`

Handles communication with OpenAI.

Responsibilities include:

- Creating the OpenAI client
- Sending prompts to the AI model
- Sending screenshots to the vision model
- Returning AI-generated responses

---

### `agent.py`

Contains the main Nexa agent.

Responsibilities include:

- Defining available tools
- Sending user commands to the AI model
- Receiving tool calls
- Validating requested tools
- Executing tools
- Returning tool results to the model
- Supporting multi-step actions

---

### `mac_tools.py`

Contains macOS-specific actions such as:

```text
open_app
open_website
open_path
```

Applications are restricted through an allowlist.

---

### `browser_tools.py`

Contains browser automation and browser observation tools.

Current tools include:

```text
open_browser_url
search_youtube
open_first_youtube_result
go_back
refresh_page
read_page
click_text
type_text
press_enter
scroll_page
stop_scrolling
get_page_elements
take_screenshot
analyze_current_screen
click_coordinates
click_element
```

---

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/karthik-35/nexa.git
cd nexa/backend
```

---

### 2. Create a Virtual Environment

```bash
python -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

---

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

### 4. Install Playwright Chromium

```bash
playwright install chromium
```

---

### 5. Configure Environment Variables

Create a `.env` file inside:

```text
backend/.env
```

Add:

```env
OPENAI_API_KEY=your_openai_api_key
```

Never commit the `.env` file to GitHub.

A safe `.env.example` can contain:

```env
OPENAI_API_KEY=
```

---

## Running Nexa

From the `backend` directory:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Agent requests run serially on a dedicated worker so synchronous Playwright stays
on its owning thread and observe/action sequences cannot interleave. Run a single
Uvicorn worker with the dedicated browser profile. OpenAI initialization is lazy;
the backend loads `backend/.env` independently of the working directory, while
health and browser-only checks work without an API key.

The API will run locally at:

```text
http://127.0.0.1:8000
```

FastAPI development documentation is available at:

```text
http://127.0.0.1:8000/docs
```

Swagger is currently used only for development and testing.

The final Nexa application is intended to provide its own macOS interface instead of requiring the user to manually interact with Swagger.

---

## Example Agent Request

Using the `/agent` endpoint:

```text
Search YouTube for AI agents for beginners
```

The model may generate a structured tool call similar to:

```text
search_youtube(
    query="AI agents for beginners"
)
```

The Python backend executes the function and returns the result.

For a multi-step request:

```text
Search YouTube for AI agents and open the first video
```

Nexa can execute:

```text
search_youtube
        ↓
tool result
        ↓
OpenAI
        ↓
open_first_youtube_result
        ↓
tool result
        ↓
final response
```

---

## Dedicated Browser Profile

Nexa uses its own Chromium profile:

```text
~/.nexa/browser-profile
```

This separates the agent-controlled browser from the user's normal Chrome profile.

This design reduces exposure of personal browsing sessions, cookies, and logged-in accounts.

---

## Known Limitations

Nexa is still under development.

Current limitations include:

- Coordinate-based visual clicking is experimental.
- Some websites may block automated browsers.
- Google Search may trigger CAPTCHA challenges.
- Some websites require more robust DOM or accessibility-based selectors.
- Browser input detection currently depends on webpage structure.
- Authentication-heavy websites may require official APIs or OAuth.
- Sensitive actions do not yet have a complete confirmation system.
- Expected Playwright failures return structured tool errors; broader recovery is still being improved.
- DOM target discovery currently covers the main frame and common interactive controls; iframe targeting is not yet implemented.

Nexa does not attempt to bypass CAPTCHA or website security mechanisms.

### Browser reliability checks

Run from `backend`:

```bash
.venv/bin/python -m pytest -q
```

The suite uses local HTML in temporary headless Chromium contexts and mocked
OpenAI responses. It checks DPR 2 screenshots, real DOM/canvas activation,
stale-coordinate rejection, timeout recovery, existing browser controls,
the application allowlist, and serialized agent requests. Chromium must already
be installed; the suite makes no OpenAI API calls.

For the live model check, with YouTube open in Nexa's browser, send `/agent`:

```text
Look at the current screen and click the microphone icon next to the search bar.
```

Verify the voice-search dialog actually opens. A "Waiting for permission" dialog
confirms the button activated; granting microphone permission is not needed for
this test. Check the returned `actions` for observation, DOM targeting, and dialog
evidence. Do not infer completion from `success: true` alone.

---

## Roadmap

Planned features include:

### Voice Control

```text
Voice
 ↓
Speech-to-Text
 ↓
Nexa Agent
 ↓
Action
```

Potential technology:

- Whisper

---

### Native macOS Interface

A small desktop interface or keyboard shortcut for interacting with Nexa without manually opening Swagger.

Potential technology:

- SwiftUI
- Local web UI

---

### Better Browser Understanding

Improve interaction using:

- Accessibility information
- ARIA labels
- DOM element IDs
- Semantic element matching
- Vision fallback

---

### Confirmation System

Require explicit user confirmation before risky actions.

---

### Memory

Allow Nexa to remember useful preferences and previous tasks.

Potential technologies:

- SQLite
- Embeddings
- Vector database
- Retrieval-Augmented Generation

---

### Local AI Models

Experiment with local inference for privacy and offline functionality.

Potential technologies:

- Ollama
- MLX
- Qwen
- Llama

---

### Advanced Agent Workflows

Support longer and more reliable multi-step workflows.

Potential technologies:

- LangGraph
- Planning systems
- Agent state machines

---

### Testing and Evaluation

Expand automated testing for:

- Tool execution
- Agent decisions
- Browser actions
- Safety rules
- Multi-step workflows
- Failure recovery

---

## Design Philosophy

Nexa follows several core principles:

**Local-first**

Computer interaction happens through a local backend running on the user's machine.

**Controlled tool access**

The AI can only use explicitly approved tools.

**AI for reasoning, code for execution**

The model decides what should happen, while deterministic Python code performs the actual action.

**Observe before acting**

When the target is unclear, Nexa should inspect the webpage or screen before interacting with it.

**Prefer reliable interaction**

DOM and text-based actions are preferred over coordinate-based clicking whenever possible.

**User control**

Sensitive or irreversible actions should require explicit confirmation.

---

## Development Status

Nexa currently supports the core agent architecture, macOS application launching, multi-step tool calling, browser automation, webpage reading, scrolling, screenshot capture, and visual screen analysis.

Current development is focused on making vision-based browser interaction more reliable before expanding into voice control, memory, and a native macOS interface.

---

## Future Goal

The long-term goal is for Nexa to behave like a practical desktop AI assistant:

```text
User
 ↓
Voice / Text
 ↓
Nexa
 ↓
Understand
 ↓
Plan
 ↓
Observe
 ↓
Act
 ↓
Verify
 ↓
Complete Task
```

Instead of simply telling the user how to complete a computer task, Nexa aims to safely perform supported tasks on the user's behalf.

---

## Author

**Karthik Pullemla**

Computer Science Student  
University of North Texas

---

## Disclaimer

Nexa is an experimental educational project.

Users should review actions performed by the agent, especially when interacting with external websites, accounts, files, or sensitive information.
