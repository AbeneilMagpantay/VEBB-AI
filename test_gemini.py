"""Test async Gemini API connection."""
import asyncio
import os

# Clear proxy settings
os.environ["NO_PROXY"] = "*"
for v in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(v, None)

from google import genai

API_KEY = "AIzaSyA460BVzUI4TheuaPiH2Oszp9fGppxPE6s"

async def test_async():
    client = genai.Client(api_key=API_KEY)
    print("Testing ASYNC Gemini call...")
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents="Say hello in one word"
        )
        print(f"ASYNC SUCCESS: {response.text}")
    except Exception as e:
        print(f"ASYNC ERROR: {e}")

def test_sync():
    client = genai.Client(api_key=API_KEY)
    print("Testing SYNC Gemini call...")
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash", 
            contents="Say hello in one word"
        )
        print(f"SYNC SUCCESS: {response.text}")
    except Exception as e:
        print(f"SYNC ERROR: {e}")

if __name__ == "__main__":
    test_sync()
    asyncio.run(test_async())
