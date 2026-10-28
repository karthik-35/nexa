import json

from openai import OpenAIError

from app.ai import client
from app.mac_tools import open_app, open_website, open_path
from app.browser_tools import (
    search_youtube,
    open_first_youtube_result,
    go_back,
    refresh_page,
    read_page,
    click_text,
    type_text,
    press_enter,
    scroll_page,
    stop_scrolling,
    get_page_elements,
    take_screenshot,
    analyze_current_screen,
)


# Functions Nexa is allowed to execute
TOOLS = {
    "open_app": open_app,
    "open_website": open_website,
    "open_path": open_path,
    "search_youtube": search_youtube,
    "open_first_youtube_result": open_first_youtube_result,
    "go_back": go_back,
    "refresh_page": refresh_page,
    "read_page": read_page,
    "click_text": click_text,
    "type_text": type_text,
    "press_enter": press_enter,
    "scroll_page": scroll_page,
    "stop_scrolling": stop_scrolling,
    "get_page_elements": get_page_elements,
    "take_screenshot": take_screenshot,
    "analyze_current_screen": analyze_current_screen,
}


# Tell OpenAI which tools exist
TOOL_DEFINITIONS = []

for name, description, parameter in [
    (
        "open_app",
        "Open an approved macOS application. Use a short lowercase app name such as teams, chrome, safari, whatsapp, zoom, spotify, finder, or vscode.",
        "app_name",
    ),
    (
        "open_website",
        "Open a website in the browser.",
        "url",
    ),
    (
        "open_path",
        "Open an approved local file or folder.",
        "path",
    ),
    (
        "search_youtube",
        "Search YouTube for videos using the browser. Only search; do not open a video.",
        "query",
    ),
]:
    TOOL_DEFINITIONS.append({
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                parameter: {"type": "string"}
            },
            "required": [parameter],
            "additionalProperties": False,
        },
        "strict": True,
    })


TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "open_first_youtube_result",
    "description": "Open the first video from the current YouTube search results page. Only use this when the user explicitly asks to open a video.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})


TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "go_back",
    "description": "Go back to the previous page in the Nexa browser.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})


TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "refresh_page",
    "description": "Refresh the current page in the Nexa browser.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})


TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "read_page",
    "description": (
        "Read the visible text from the webpage currently open in the Nexa browser. "
        "Use this when the user says things like 'read this page', "
        "'what is this page about', or 'summarize this page'. "
        "Do not ask the user for a URL if a page is already open in the Nexa browser."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "click_text",
    "description": (
        "Click an element on the current Nexa browser page using its visible text. "
        "Use this when the user explicitly asks to click a button, link, or other visible element."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string"
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "type_text",
    "description": (
        "Type text into an input field on the current webpage. "
        "Use the visible placeholder of the input field to identify it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "field": {
                "type": "string"
            },
            "text": {
                "type": "string"
            }
        },
        "required": ["field", "text"],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "press_enter",
    "description": (
        "Press the Enter key in the current Nexa browser. "
        "Use this when the user explicitly asks to press Enter or submit the current input."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "scroll_page",
    "description": "Scroll the current webpage up or down.",
    "parameters": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down"]
            }
        },
        "required": ["direction"],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "stop_scrolling",
    "description": "Stop the current automatic scrolling in the Nexa browser.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "get_page_elements",
    "description": (
        "Inspect the current webpage and return visible buttons and links. "
        "Use this when the user asks what can be clicked or what options are available."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "take_screenshot",
    "description": (
        "Capture a screenshot of the currently visible area of the Nexa browser. "
        "Use this when the user asks to take or capture a screenshot."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})

TOOL_DEFINITIONS.append({
    "type": "function",
    "name": "analyze_current_screen",
    "description": (
        "Take a screenshot of the current Nexa browser and visually analyze it. "
        "Use this when the user asks what is visible on the screen or asks Nexa to look at the screen."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
})

AGENT_INSTRUCTIONS = (
    "You are Nexa, an AI desktop agent for macOS. "
    "Perform exactly the actions requested by the user. "
    "Do not perform additional actions that were not requested. "
    "You may use multiple tools only when the user's request explicitly requires multiple actions. "
    "If the requested action is already complete, stop and respond. "
    "If the user asks to search YouTube, only search YouTube unless they also explicitly ask to open a video. "
    "Never claim an action succeeded without a tool result."
    "If the user refers to 'this page' or 'the current page', use the read_page tool."
    "If the user asks to open or click something based on its meaning rather than exact visible text, first use read_page to inspect the page, then use click_text with the best matching visible text."
    "If the user asks you to visually inspect the screen and then interact with something visible, "
    "first use analyze_current_screen to understand the screen. "
    "Then use the appropriate browser tool to perform the requested action. "
)


def run_agent(message: str):
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            instructions=AGENT_INSTRUCTIONS,
            input=message,
            tools=TOOL_DEFINITIONS,
            parallel_tool_calls=False,
        )

        actions = []

        # Maximum 5 tool steps
        for _ in range(5):

            tool_calls = [
                item for item in response.output
                if item.type == "function_call"
            ]

            # No more tools needed
            if not tool_calls:
                return {
                    "success": True,
                    "actions": actions,
                    "message": response.output_text or "Task completed.",
                }

            tool_outputs = []

            for item in tool_calls:
                function_name = item.name

                # Safety check
                if function_name not in TOOLS:
                    return {
                        "success": False,
                        "message": "Tool not allowed.",
                    }

                arguments = json.loads(item.arguments)

                # Execute selected tool
                result = TOOLS[function_name](**arguments)

                actions.append({
                    "tool": function_name,
                    "arguments": arguments,
                    "result": result,
                })
 
                # Send tool result back to OpenAI
                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result),
                })

            response = client.responses.create(
                model="gpt-4.1-mini",
                instructions=AGENT_INSTRUCTIONS,
                previous_response_id=response.id,
                input=tool_outputs,
                tools=TOOL_DEFINITIONS,
                parallel_tool_calls=False,
            )

        return {
            "success": False,
            "actions": actions,
            "message": "Maximum tool steps reached.",
        }

    except (OpenAIError, ValueError, TypeError, OSError) as error:
        return {
            "success": False,
            "message": f"Nexa error: {error}",
        }