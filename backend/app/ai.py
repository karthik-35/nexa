import os

from dotenv import load_dotenv
from openai import OpenAI
import base64

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def ask_ai(message: str):
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=message,
    )

    return response.output_text

def analyze_screenshot(image_path: str):
    with open(image_path, "rb") as image_file:
        image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Analyze this screenshot. Briefly describe what is "
                            "visible and identify important buttons, links, "
                            "inputs, or other interactive elements."
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{image_base64}",
                    },
                ],
            }
        ],
    )

    return response.output_text