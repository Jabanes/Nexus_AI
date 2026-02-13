import requests
import os
import json
from dotenv import load_dotenv

import socket
import requests.packages.urllib3.util.connection as urllib3_cn

def allowed_gai_family():
    return socket.AF_INET

urllib3_cn.allowed_gai_family = allowed_gai_family

def test_gemini_rest():
    print("="*50)
    print("Testing Gemini REST API (requests library)")
    print("="*50)
    
    # Check Proxy
    print("Checking Proxy Settings:")
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        val = os.environ.get(key)
        print(f"  {key}: {val}")
        if val:
            print(f"  Removing {key}...")
            del os.environ[key]
            
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("No API Key")
        return
        
    model = "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{
            "parts": [{"text": "Hello, world!"}]
        }]
    }
    
    print(f"POST {url}")
    try:
        # Disable SSL verification to rule out certificate issues
        response = requests.post(url, json=payload, timeout=10, verify=False)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:200]}...")
        
        if response.status_code == 200:
            print("✅ REST API Success!")
        else:
            print("❌ REST API Failure!")
            
    except Exception as e:
        print(f"❌ Connection Error: {e}")

if __name__ == "__main__":
    test_gemini_rest()
