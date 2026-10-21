import json

from openai import OpenAIError

from app.ai import client
from app.mac_tools import open_app, open_website, open_path
from app.browser_tools import (
    search_youtube,
    open_first_youtube_result,
)


# Functions Nexa is allowed to execute
TOOLS = {
    "open_app": open_app,
    "open_website": open_website,
    "open_path": open_path,
    "search_youtube": search_youtube,
    "open_first_youtube_result": open_first_youtube_result,
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
    ("search_youtube", "Search YouTube for videos using the browser.", "query"),
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
    "description": "Open the first video from the current YouTube search results page.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
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
                "Use tools when the user requests an action. "
                "You may use multiple tools to complete one request. "
                "Never claim an action succeeded without a tool result."
            ),
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

            # If there are no more tools, the task is finished
            if not tool_calls:
                return {
                    "success": True,
                    "actions": actions,
                    "message": response.output_text or "Task completed."
                }

            tool_outputs = []

            for item in tool_calls:
                function_name = item.name

                # Safety check
                if function_name not in TOOLS:
                    return {
                        "success": False,
                        "message": "Tool not allowed."
                    }

                arguments = json.loads(item.arguments)

                # Execute the selected tool
                result = TOOLS[function_name](**arguments)

                actions.append({
                    "tool": function_name,
                    "arguments": arguments,
                    "result": result,
                })

                # Send the result back to OpenAI
                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result),
                })

            response = client.responses.create(
                model="gpt-4.1-mini",
                instructions=(
                    "You are Nexa, an AI desktop agent for macOS. "
                    "Continue using tools until the user's request is complete."
                ),
                previous_response_id=response.id,
                input=tool_outputs,
                tools=TOOL_DEFINITIONS,
                parallel_tool_calls=False,
            )

        return {
            "success": False,
            "actions": actions,
            "message": "Maximum tool steps reached."
        }

    except (OpenAIError, ValueError, TypeError, OSError) as error:
        return {
            "success": False,
            "message": f"Nexa error: {error}"
        }