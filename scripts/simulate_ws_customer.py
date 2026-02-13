import asyncio
import websockets
import json
import uuid
import sys

async def simulate_streaming_conversation():
    tenant_id = "barber_shop_demo"
    customer_phone = "+15550000000"
    uri = f"ws://localhost:8000/ws/call/{tenant_id}?customer_phone={customer_phone}"
    
    print(f"Connecting to: {uri}")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected!")
            
            # Wait for initial "connected" message
            response = await websocket.recv()
            print(f"Received: {response}")
            
            # Wait for "ready" message
            response = await websocket.recv()
            print(f"Received: {response}")
            
            # Send a user message (Text)
            user_msg = {
                "type": "message",
                "content": "Hi, I'd like to book a haircut for tomorrow morning."
            }
            print(f"\nSending: {user_msg}")
            await websocket.send(json.dumps(user_msg))
            
            # Listen for streaming response
            print("\nListening for stream...")
            full_response = ""
            
            while True:
                try:
                    response_text = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                    response = json.loads(response_text)
                    
                    if response.get("type") == "response_part":
                        chunk = response.get("content", "")
                        print(f"Chunk: {chunk}", end="", flush=True)
                        full_response += chunk
                    elif response.get("type") == "error":
                        print(f"\nError: {response}")
                        break
                    else:
                        print(f"\nUnknown message: {response}")
                        
                except asyncio.TimeoutError:
                    print("\nStream timeout (completed?)")
                    break
                except websockets.exceptions.ConnectionClosed:
                    print("\nConnection closed")
                    break
            
            print(f"\n\nFull Response: {full_response}")
            
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(simulate_streaming_conversation())
