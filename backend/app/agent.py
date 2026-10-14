import json

from openai import OpenAIError

from app.ai import client
from app.mac_tools import open_app, open_website, open_path
from app.browser_tools import search_google


# Functions Nexa is allowed to execute
TOOLS = {
    "open_app": open_app,
    "open_website": open_website,
    "open_path": open_path,
}


# Tell OpenAI which tools exist
TOOL_DEFINITIONS = []

for name, description, parameter in [
    (
    "open_app",
    "Open an approved macOS application. Use a short lowercase app name such as teams, chrome, safari, whatsapp, zoom, spotify, finder, or vscode.",
    "app_name"
    ),
    ("open_website", "Open a website in the browser", "url"),
    ("open_path", "Open an approved local file or folder", "path"),
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


def run_agent(message: str):

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            instructions=(
                "You are Nexa, an AI desktop agent for macOS. "
                "Use tools only when the user requests an action. "
                "Never claim an action succeeded without a tool result."
            ),
            input=message,
            tools=TOOL_DEFINITIONS,
            parallel_tool_calls=False,
        )

    except OpenAIError as error:
        return {
            "success": False,
            "message": f"OpenAI API error: {error}"
        }

    # Check whether OpenAI requested a tool
    for item in response.output:

        if item.type != "function_call":
            continue

        function_name = item.name

        # Safety check
        if function_name not in TOOLS:
            return {
                "success": False,
                "message": "Tool not allowed."
            }

        try:
            arguments = json.loads(item.arguments)

            if not isinstance(arguments, dict):
                raise ValueError("Invalid tool arguments")

            result = TOOLS[function_name](**arguments)

            return {
                "tool": function_name,
                "arguments": arguments,
                "result": result,
            }

        except (ValueError, TypeError, OSError) as error:
            return {
                "success": False,
                "message": f"Tool failed: {error}"
            }

    # Normal conversation
    return {
        "success": True,
        "message": response.output_text or "No response generated."
    }