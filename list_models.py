import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ No GEMINI_API_KEY found in .env")
else:
    client = genai.Client(api_key=api_key)
    print(f"🔍 Listing models available for API Key: {api_key[:8]}...")
    
    try:
        # The list_models method in google-genai
        models = client.models.list()
        print("\nAvailable Models:")
        for m in models:
            print(f" - {m.name}")
    except Exception as e:
        print(f"❌ Error listing models: {e}")
