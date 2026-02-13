import google.generativeai as genai
import os
import asyncio
from dotenv import load_dotenv

# Force REST
os.environ["GEMINI_TRANSPORT"] = "rest"

async def test_types():
    print("="*50)
    print("Testing Gemini REST Async Types")
    print("="*50)
    
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=api_key, transport="rest")
    
    model = genai.GenerativeModel("gemini-2.0-flash")
    chat = model.start_chat()
    
    print("Calling send_message_async(stream=True)...")
    
    try:
        # Inspect what this returns without awaiting first? 
        # No, it's a function call.
        
        # Test 1: Await it
        coro = chat.send_message_async("Hi", stream=True)
        print(f"Type of chat.send_message_async output: {type(coro)}")
        
        try:
            response = await coro
            print(f"Type of awaited response: {type(response)}")
            
            # Check if it is async iterable
            print(f"Is Async Iterable? {hasattr(response, '__aiter__')}")
            print(f"Is Sync Iterable? {hasattr(response, '__iter__')}")
            
        except TypeError as e:
            print(f"Await failed: {e}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_types())
