# main.py
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
from enum import Enum
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
import asyncio
from contextlib import asynccontextmanager
import uuid

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
# JOB MANAGEMENT
# ============================================================
class JobStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"

class Job:
    def __init__(self, job_id: str, prompt: str, reference_images: List[str]):
        self.job_id = job_id
        self.prompt = prompt
        self.reference_images = reference_images
        self.status = JobStatus.PENDING
        self.image_url = None
        self.error_message = None
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.progress = 0

jobs: Dict[str, Job] = {}
JOB_EXPIRY_HOURS = 1

def cleanup_old_jobs():
    now = datetime.now()
    expired_jobs = [
        job_id for job_id, job in jobs.items()
        if (now - job.created_at).total_seconds() > JOB_EXPIRY_HOURS * 3600
    ]
    for job_id in expired_jobs:
        del jobs[job_id]

async def process_job(job: Job):
    try:
        job.status = JobStatus.GENERATING
        job.started_at = datetime.now()
        job.progress = 10
        
        image_url = await generate_image_workflow(job)
        
        if image_url:
            job.status = JobStatus.COMPLETED
            job.image_url = image_url
            job.completed_at = datetime.now()
            job.progress = 100
        else:
            job.status = JobStatus.FAILED
            job.error_message = "Image generation failed"
            job.completed_at = datetime.now()
            
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        job.completed_at = datetime.now()

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
    
    response = requests.post(url, headers=HEADERS, json=payload)
    
    if response.status_code != 200:
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
    
    response = requests.post(url, headers=HEADERS, json=payload)
    
    if response.status_code != 200:
        return None
    
    return response.json()

def create_cookie_value(verify_result):
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
    
    if response.status_code == 200:
        return response.json()
    else:
        return None

def generate_image_request(cookie_value, project_id, prompt, reference_images):
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
        "imageInput": reference_images if reference_images else [],
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
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"[!] Failed: {response.text}")
        return None

async def generate_image_workflow(job: Job):
    try:
        job.progress = 20
        
        print("=" * 70)
        print("Luno Studio Image Generation")
        print("=" * 70)
        print(f"Prompt: {job.prompt}")
        print(f"Reference Images: {len(job.reference_images)}")
        
        # Step 1: Generate temporary email
        print("\n[Step 1] Generating temporary email...")
        job.progress = 25
        
        emailnator, email = get_temp_email()
        
        password = secrets.token_urlsafe(12)
        code_challenge, code_verifier = generate_code_challenge()
        
        # Step 2: Sign up
        print("\n[Step 2] Creating account...")
        job.progress = 35
        
        signup_result = signup(email, password, code_challenge)
        
        if not signup_result or 'id' not in signup_result:
            raise Exception("Signup failed")
        
        # Step 3: Get verification code
        print("\n[Step 3] Getting verification code...")
        job.progress = 45
        
        verification_code = wait_for_verification_code(emailnator, email)
        
        # Step 4: Verify email
        print("\n[Step 4] Verifying email...")
        job.progress = 55
        
        verify_result = verify_email(email, verification_code)
        
        if not verify_result or 'access_token' not in verify_result:
            raise Exception("Verification failed")
        
        print(f"[+] Email verified!")
        
        cookie_value = create_cookie_value(verify_result)
        
        # Step 5: Create project
        print("\n[Step 5] Creating project...")
        job.progress = 65
        
        timestamp = int(time.time() * 1000)
        project_id = f"proj-{timestamp}-{secrets.token_urlsafe(5).replace('-', '')}"
        
        project_result = create_project(cookie_value, project_id, timestamp)
        
        if not project_result:
            raise Exception("Project creation failed")
        
        print(f"[+] Project ID: {project_id}")
        
        # Step 6: Generate image
        print("\n[Step 6] Generating AI image...")
        job.progress = 80
        
        generation_result = generate_image_request(cookie_value, project_id, job.prompt, job.reference_images)
        
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
# FASTAPI APPLICATION - PURE API, NO HTML
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 70)
    print("Luno Studio Image Generator API")
    print("=" * 70)
    print("\n✅ API is ready")
    
    async def cleanup_task():
        while True:
            await asyncio.sleep(300)
            cleanup_old_jobs()
    
    asyncio.create_task(cleanup_task())
    
    yield
    print("Shutting down...")

app = FastAPI(
    title="Luno Studio Image Generator API",
    description="Generate AI images with your custom prompts and reference images",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GenerateResponse(BaseModel):
    success: bool
    job_id: str
    message: str
    status_url: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int
    image_url: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

@app.get("/")
async def root():
    return {
        "service": "Luno Studio Image Generator API",
        "version": "2.0.0",
        "endpoints": {
            "POST /generate": "Generate image (prompt required, ref1-ref5 optional)",
            "GET /status": "Check job status",
            "GET /image": "Get generated image URL"
        },
        "example": {
            "generate": "curl 'http://localhost:8000/generate?prompt=a beautiful cat&ref1=https://example.com/image.png'",
            "status": "curl 'http://localhost:8000/status?job_id=xxx'",
            "image": "curl 'http://localhost:8000/image?job_id=xxx'"
        }
    }

@app.get("/generate")
async def generate_image_endpoint(
    background_tasks: BackgroundTasks,
    prompt: str = Query(..., description="The prompt for image generation (required)"),
    ref1: Optional[str] = Query(None, description="Reference image URL 1"),
    ref2: Optional[str] = Query(None, description="Reference image URL 2"),
    ref3: Optional[str] = Query(None, description="Reference image URL 3"),
    ref4: Optional[str] = Query(None, description="Reference image URL 4"),
    ref5: Optional[str] = Query(None, description="Reference image URL 5"),
    wait: bool = Query(False, description="Wait for completion")
):
    """Generate an AI image using your prompt and reference images"""
    
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt parameter is required")
    
    reference_images = []
    for i in range(1, 6):
        ref = locals().get(f'ref{i}')
        if ref:
            reference_images.append(ref)
    
    job_id = str(uuid.uuid4())
    job = Job(job_id, prompt, reference_images)
    jobs[job_id] = job
    
    background_tasks.add_task(process_job, job)
    
    if wait:
        timeout = 120
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            if job.status == JobStatus.COMPLETED:
                return {"image_url": job.image_url, "job_id": job_id}
            elif job.status == JobStatus.FAILED:
                raise HTTPException(status_code=500, detail=job.error_message)
            await asyncio.sleep(1)
        
        return GenerateResponse(
            success=True,
            job_id=job_id,
            message="Job still processing",
            status_url=f"/status?job_id={job_id}"
        )
    else:
        return GenerateResponse(
            success=True,
            job_id=job_id,
            message="Job created",
            status_url=f"/status?job_id={job_id}"
        )

@app.get("/status")
async def get_job_status(job_id: str = Query(...)):
    """Check job status"""
    
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "image_url": job.image_url,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None
    }

@app.get("/image")
async def get_image(
    job_id: str = Query(...),
    wait: bool = Query(True)
):
    """Get generated image URL"""
    
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    if job.status == JobStatus.COMPLETED:
        return {"image_url": job.image_url}
    
    elif job.status == JobStatus.FAILED:
        raise HTTPException(status_code=500, detail=job.error_message)
    
    elif job.status in [JobStatus.PENDING, JobStatus.GENERATING]:
        if wait:
            timeout = 60
            start_wait = time.time()
            while time.time() - start_wait < timeout:
                if job.status == JobStatus.COMPLETED:
                    return {"image_url": job.image_url}
                elif job.status == JobStatus.FAILED:
                    raise HTTPException(status_code=500, detail=job.error_message)
                await asyncio.sleep(1)
            
            return {
                "status": "processing",
                "message": "Still generating",
                "job_id": job_id,
                "progress": job.progress
            }
        else:
            return {
                "status": "processing",
                "job_id": job_id,
                "progress": job.progress
            }
    
    raise HTTPException(status_code=500, detail="Unknown state")

if __name__ == "__main__":
    print("=" * 70)
    print("Luno Studio Image Generator API")
    print("=" * 70)
    print("\n🚀 API Endpoints:")
    print("   GET /generate?prompt=your prompt&ref1=image.png")
    print("   GET /status?job_id=xxx")
    print("   GET /image?job_id=xxx")
    print("\n📚 API Docs: http://localhost:8000/docs")
    print("=" * 70)
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        log_level="info"
    )
