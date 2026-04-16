# main.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import requests
import time
import re
import secrets
import base64
import hashlib
import json
from datetime import datetime
from emailnator import Emailnator
import uvicorn

# ============================================================
# CONFIGURATION
# ============================================================
SUPABASE_URL = "https://liuvfhbmbtunebdwhiqh.supabase.co"
API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxpdXZmaGJtYnR1bmViZHdoaXFoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ2MTY0MTYsImV4cCI6MjA5MDE5MjQxNn0.R8Ybduar3YilzBwbK3V8bgNSUQO66VDQmDgmNNjeVsI"

HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "apikey": API_KEY,
    "content-type": "application/json;charset=UTF-8",
    "origin": "https://www.lunostudio.ai",
    "referer": "https://www.lunostudio.ai/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "x-client-info": "supabase-ssr/0.9.0 createBrowserClient",
    "x-supabase-api-version": "2024-01-01"
}

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def generate_code_challenge():
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().replace('=', '')
    return code_challenge, code_verifier

def get_temp_email():
    emailnator = Emailnator()
    email_data = emailnator.generate_email()
    email = email_data["email"][0]
    print(f"[+] Generated email: {email}")
    return emailnator, email

def wait_for_verification_code(emailnator, email, timeout=120):
    print("\n[*] Waiting for verification code...")
    start_time = time.time()
    seen_messages = set()
    
    while time.time() - start_time < timeout:
        try:
            inbox_result = emailnator.inbox(email)
            messages = []
            if isinstance(inbox_result, dict) and "messageData" in inbox_result:
                messages = inbox_result["messageData"]
            
            for msg in messages:
                msg_id = str(msg)
                if msg_id in seen_messages:
                    continue
                seen_messages.add(msg_id)
                
                try:
                    full_message = emailnator.get_message(email, msg if isinstance(msg, str) else msg.get('messageID', ''))
                    message_str = str(full_message)
                    
                    if 'luno' in message_str.lower() or 'confirm your signup' in message_str.lower():
                        code_match = re.search(r'\b(\d{6})\b', message_str)
                        if code_match:
                            code = code_match.group(1)
                            print(f"✅ VERIFICATION CODE: {code}")
                            return code
                except:
                    pass
        except:
            pass
        time.sleep(0.5)
    
    raise Exception("Timeout: No verification code received")

def signup(email, password, code_challenge):
    url = f"{SUPABASE_URL}/auth/v1/signup"
    payload = {
        "email": email,
        "password": password,
        "data": {},
        "gotrue_meta_security": {},
        "code_challenge": code_challenge,
        "code_challenge_method": "s256"
    }
    
    print(f"\n[*] Sending signup request...")
    response = requests.post(url, headers=HEADERS, json=payload)
    print(f"[*] Signup response: {response.status_code}")
    
    if response.status_code != 200:
        print(f"[!] Error: {response.text}")
        return None
    
    return response.json()

def verify_email(email, verification_code):
    url = f"{SUPABASE_URL}/auth/v1/verify"
    payload = {
        "email": email,
        "token": verification_code,
        "type": "signup",
        "gotrue_meta_security": {}
    }
    
    print(f"\n[*] Verifying with code: {verification_code}")
    response = requests.post(url, headers=HEADERS, json=payload)
    print(f"[*] Verify response: {response.status_code}")
    
    if response.status_code != 200:
        print(f"[!] Error: {response.text}")
        return None
    
    return response.json()

def create_cookie_value(verify_result):
    """Create the exact cookie value format from the verify result"""
    cookie_data = {
        "access_token": verify_result['access_token'],
        "token_type": verify_result.get('token_type', 'bearer'),
        "expires_in": verify_result.get('expires_in', 3600),
        "expires_at": verify_result.get('expires_at'),
        "refresh_token": verify_result.get('refresh_token'),
        "user": verify_result.get('user')
    }
    
    json_str = json.dumps(cookie_data)
    base64_encoded = base64.b64encode(json_str.encode()).decode()
    return f"base64-{base64_encoded}"

def create_project(cookie_value, project_id, timestamp):
    """Create a new project with the cookie"""
    url = "https://www.lunostudio.ai/api/projects"
    
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "cookie": f"geo-country=US; sb-liuvfhbmbtunebdwhiqh-auth-token={cookie_value}",
        "origin": "https://www.lunostudio.ai",
        "referer": "https://www.lunostudio.ai/dashboard",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    payload = {
        "id": project_id,
        "name": "Generated Project",
        "createdAt": timestamp,
        "updatedAt": timestamp
    }
    
    response = requests.post(url, headers=headers, json=payload)
    print(f"[*] Create project response: {response.status_code}")
    
    if response.status_code == 200:
        print(f"[+] Project created successfully!")
        return response.json()
    else:
        print(f"[!] Failed: {response.text}")
        return None

def generate_image(cookie_value, project_id, prompt, reference_images):
    """Generate AI image with the cookie"""
    url = "https://www.lunostudio.ai/api/generate"
    
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "cookie": f"geo-country=US; sb-liuvfhbmbtunebdwhiqh-auth-token={cookie_value}",
        "origin": "https://www.lunostudio.ai",
        "referer": f"https://www.lunostudio.ai/project/{project_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    payload = {
        "prompt": prompt,
        "aspectRatio": "1:1",
        "model": "google/nano-banana-2",
        "imageInput": reference_images,
        "duration": 4,
        "generateAudio": True,
        "resolution": "1K",
        "modelOptions": {
            "grounding": "off"
        }
    }
    
    print(f"\n[*] Generating image with prompt: '{prompt}'")
    print(f"[*] Reference images: {len(reference_images)}")
    response = requests.post(url, headers=headers, json=payload)
    print(f"[*] Generate response: {response.status_code}")
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"[!] Failed: {response.text}")
        return None

async def generate_and_return_image(prompt: str, reference_images: List[str]):
    """Main function to generate image and return URL"""
    try:
        print("=" * 70)
        print("Luno Studio Image Generation")
        print("=" * 70)
        
        # Step 1: Generate temporary email
        print("\n[Step 1] Generating temporary email...")
        emailnator, email = get_temp_email()
        
        password = secrets.token_urlsafe(12)
        code_challenge, code_verifier = generate_code_challenge()
        print(f"[+] Password: {password}")
        
        # Step 2: Sign up
        print("\n[Step 2] Creating account...")
        signup_result = signup(email, password, code_challenge)
        
        if not signup_result or 'id' not in signup_result:
            raise Exception("Signup failed")
        
        user_id = signup_result['id']
        print(f"[+] User ID: {user_id}")
        
        # Step 3: Get verification code
        print("\n[Step 3] Getting verification code...")
        try:
            verification_code = wait_for_verification_code(emailnator, email)
        except Exception as e:
            raise Exception(f"Failed to get verification code: {e}")
        
        # Step 4: Verify email
        print("\n[Step 4] Verifying email...")
        verify_result = verify_email(email, verification_code)
        
        if not verify_result or 'access_token' not in verify_result:
            raise Exception("Verification failed")
        
        print(f"[+] Email verified!")
        
        # Create the cookie value from the verify result
        cookie_value = create_cookie_value(verify_result)
        print(f"[+] Cookie created")
        
        # Step 5: Create project
        print("\n[Step 5] Creating project...")
        timestamp = int(time.time() * 1000)
        project_id = f"proj-{timestamp}-{secrets.token_urlsafe(5).replace('-', '')}"
        
        project_result = create_project(cookie_value, project_id, timestamp)
        
        if not project_result:
            raise Exception("Project creation failed")
        
        print(f"[+] Project ID: {project_id}")
        
        # Step 6: Generate image
        print("\n[Step 6] Generating AI image...")
        generation_result = generate_image(cookie_value, project_id, prompt, reference_images)
        
        if generation_result and 'output' in generation_result:
            image_url = generation_result['output'][0]
            print("\n" + "=" * 70)
            print("✅ IMAGE GENERATED SUCCESSFULLY!")
            print("=" * 70)
            print(f"\n🔗 {image_url}\n")
            return image_url
        else:
            raise Exception("Image generation failed")
            
    except Exception as e:
        print(f"\n[!] Error: {e}")
        import traceback
        traceback.print_exc()
        return None

# ============================================================
# FASTAPI APPLICATION
# ============================================================
app = FastAPI(title="Luno Studio Image Generator API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GenerateResponse(BaseModel):
    success: bool
    image_url: Optional[str] = None
    prompt: str
    reference_count: int
    error: Optional[str] = None

@app.get("/")
async def root():
    return {
        "service": "Luno Studio Image Generator API",
        "endpoints": {
            "/generate": "Generate image with prompt and reference images",
            "/health": "Health check"
        },
        "usage": {
            "basic": "/generate?prompt=your prompt here",
            "with_reference": "/generate?prompt=your prompt&ref1=https://example.com/image.png",
            "multiple_refs": "/generate?prompt=your prompt&ref1=url1.png&ref2=url2.jpg&ref3=url3.png"
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/generate", response_class=RedirectResponse)
async def generate_image_endpoint(
    prompt: str = Query(..., description="The prompt for image generation"),
    ref1: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref2: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref3: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref4: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref5: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref6: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref7: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref8: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref9: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    return_json: bool = Query(False, description="If true, return JSON instead of redirect")
):
    """
    Generate an AI image based on prompt and optional reference images.
    
    The endpoint will:
    1. Create a temporary email account
    2. Sign up for Luno Studio
    3. Verify the email
    4. Create a project
    5. Generate the image
    6. Redirect to the generated image URL (or return JSON)
    
    Reference images must end with .png, .jpg, or .jpeg
    """
    
    # Collect reference images
    reference_images = []
    for i in range(1, 10):
        ref = locals().get(f'ref{i}')
        if ref:
            # Validate image URL extension
            if re.search(r'\.(png|jpg|jpeg)$', ref, re.I):
                reference_images.append(ref)
            else:
                print(f"[!] Warning: Invalid reference image format for ref{i}: {ref}")
    
    print(f"\n[API] Received generation request:")
    print(f"  Prompt: {prompt}")
    for i, img in enumerate(reference_images, 1):
        print(f"  ref{i}: {img}")
    
    try:
        image_url = await generate_and_return_image(prompt, reference_images)
        
        if image_url:
            if return_json:
                return GenerateResponse(
                    success=True,
                    image_url=image_url,
                    prompt=prompt,
                    reference_count=len(reference_images)
                )
            else:
                # Redirect to the generated image
                return RedirectResponse(url=image_url, status_code=302)
        else:
            if return_json:
                raise HTTPException(status_code=500, detail="Failed to generate image")
            else:
                # Return a placeholder image or error page
                return RedirectResponse(url="https://via.placeholder.com/512?text=Generation+Failed", status_code=302)
                
    except Exception as e:
        print(f"[API] Error: {e}")
        if return_json:
            raise HTTPException(status_code=500, detail=str(e))
        else:
            return RedirectResponse(url="https://via.placeholder.com/512?text=Error", status_code=302)

@app.get("/generate/json")
async def generate_image_json(
    prompt: str = Query(..., description="The prompt for image generation"),
    ref1: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref2: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref3: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref4: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref5: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref6: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref7: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref8: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)"),
    ref9: Optional[str] = Query(None, description="Reference image URL (must end with .png or .jpg)")
):
    """Same as /generate but returns JSON instead of redirect"""
    # Reuse the same logic as above with return_json=True
    return await generate_image_endpoint(
        prompt=prompt,
        ref1=ref1, ref2=ref2, ref3=ref3, ref4=ref4, ref5=ref5,
        ref6=ref6, ref7=ref7, ref8=ref8, ref9=ref9,
        return_json=True
    )

# ============================================================
# MAIN EXECUTION
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("Luno Studio Image Generator API")
    print("=" * 70)
    print("\nStarting FastAPI server...")
    print("\nUsage Examples:")
    print("  http://localhost:8000/generate?prompt=a beautiful sunset")
    print("  http://localhost:8000/generate?prompt=cyberpunk city&ref1=https://example.com/image.png")
    print("  http://localhost:8000/generate?prompt=portrait&ref1=img1.png&ref2=img2.jpg&ref3=img3.png")
    print("  http://localhost:8000/generate/json?prompt=test&ref1=image.png  # Returns JSON")
    print("\n" + "=" * 70)
    
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
