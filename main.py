# main.py
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
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
from datetime import datetime, timedelta
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
        self.progress = 0  # 0-100 percentage

# In-memory job storage (for production, use Redis or database)
jobs: Dict[str, Job] = {}
# Clean up old jobs after 1 hour
JOB_EXPIRY_HOURS = 1

def cleanup_old_jobs():
    """Remove jobs older than JOB_EXPIRY_HOURS"""
    now = datetime.now()
    expired_jobs = [
        job_id for job_id, job in jobs.items()
        if (now - job.created_at).total_seconds() > JOB_EXPIRY_HOURS * 3600
    ]
    for job_id in expired_jobs:
        del jobs[job_id]
    if expired_jobs:
        print(f"[Cleanup] Removed {len(expired_jobs)} expired jobs")

async def process_job(job: Job):
    """Background task to process image generation"""
    try:
        job.status = JobStatus.GENERATING
        job.started_at = datetime.now()
        job.progress = 20
        print(f"[Job {job.job_id}] Started processing")
        
        # Generate the image
        image_url = await generate_and_return_image(
            job.prompt, 
            job.reference_images,
            job.job_id  # Pass job_id for progress updates
        )
        
        if image_url:
            job.status = JobStatus.COMPLETED
            job.image_url = image_url
            job.completed_at = datetime.now()
            job.progress = 100
            print(f"[Job {job.job_id}] Completed successfully")
        else:
            job.status = JobStatus.FAILED
            job.error_message = "Image generation failed"
            job.completed_at = datetime.now()
            print(f"[Job {job.job_id}] Failed")
            
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        job.completed_at = datetime.now()
        print(f"[Job {job.job_id}] Error: {e}")

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

async def generate_and_return_image(prompt: str, reference_images: List[str], job_id: str = None):
    """Main function to generate image and return URL"""
    try:
        if job_id and job_id in jobs:
            jobs[job_id].progress = 10
            
        print("=" * 70)
        print("Luno Studio Image Generation")
        print("=" * 70)
        
        # Step 1: Generate temporary email
        print("\n[Step 1] Generating temporary email...")
        if job_id and job_id in jobs:
            jobs[job_id].progress = 20
            
        emailnator, email = get_temp_email()
        
        password = secrets.token_urlsafe(12)
        code_challenge, code_verifier = generate_code_challenge()
        print(f"[+] Password: {password}")
        
        # Step 2: Sign up
        print("\n[Step 2] Creating account...")
        if job_id and job_id in jobs:
            jobs[job_id].progress = 30
            
        signup_result = signup(email, password, code_challenge)
        
        if not signup_result or 'id' not in signup_result:
            raise Exception("Signup failed")
        
        user_id = signup_result['id']
        print(f"[+] User ID: {user_id}")
        
        # Step 3: Get verification code
        print("\n[Step 3] Getting verification code...")
        if job_id and job_id in jobs:
            jobs[job_id].progress = 40
            
        try:
            verification_code = wait_for_verification_code(emailnator, email)
        except Exception as e:
            raise Exception(f"Failed to get verification code: {e}")
        
        # Step 4: Verify email
        print("\n[Step 4] Verifying email...")
        if job_id and job_id in jobs:
            jobs[job_id].progress = 50
            
        verify_result = verify_email(email, verification_code)
        
        if not verify_result or 'access_token' not in verify_result:
            raise Exception("Verification failed")
        
        print(f"[+] Email verified!")
        
        # Create the cookie value from the verify result
        cookie_value = create_cookie_value(verify_result)
        print(f"[+] Cookie created")
        
        # Step 5: Create project
        print("\n[Step 5] Creating project...")
        if job_id and job_id in jobs:
            jobs[job_id].progress = 60
            
        timestamp = int(time.time() * 1000)
        project_id = f"proj-{timestamp}-{secrets.token_urlsafe(5).replace('-', '')}"
        
        project_result = create_project(cookie_value, project_id, timestamp)
        
        if not project_result:
            raise Exception("Project creation failed")
        
        print(f"[+] Project ID: {project_id}")
        
        # Step 6: Generate image
        print("\n[Step 6] Generating AI image...")
        if job_id and job_id in jobs:
            jobs[job_id].progress = 80
            
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
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("=" * 70)
    print("Luno Studio Image Generator API with Job Tracking")
    print("=" * 70)
    print("\n💡 New Features:")
    print("   - Async job processing with job IDs")
    print("   - Status endpoint to track generation progress")
    print("   - Image endpoint that waits for completion")
    print("\n✅ API is ready to accept requests")
    
    # Start cleanup task
    async def cleanup_task():
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            cleanup_old_jobs()
    
    asyncio.create_task(cleanup_task())
    
    yield
    # Shutdown
    print("Shutting down...")

app = FastAPI(
    title="Luno Studio Image Generator API with Job Tracking",
    description="Generate AI images with async job processing. Get a job ID and track progress.",
    version="2.0.0",
    lifespan=lifespan
)

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
    estimated_time_remaining: Optional[int] = None

class PingResponse(BaseModel):
    status: str
    message: str
    timestamp: str
    server_time: str
    request_count: int
    uptime_seconds: Optional[float] = None
    active_jobs: int

# Store start time for uptime calculation
start_time = datetime.now()
request_count = 0
last_ping_time = datetime.now()

@app.get("/")
async def root():
    return {
        "service": "Luno Studio Image Generator API with Job Tracking",
        "version": "2.0.0",
        "endpoints": {
            "/generate": "Start a new image generation job (returns job_id)",
            "/status": "Check job status (use ?job_id=your-job-id)",
            "/image": "Get the generated image (waits for completion)",
            "/ping": "Keep-alive endpoint",
            "/health": "Health check"
        },
        "usage": {
            "start_job": "GET /generate?prompt=your prompt&ref1=image.png",
            "check_status": "GET /status?job_id=your-job-id",
            "get_image": "GET /image?job_id=your-job-id (can be used directly in <img> tag)"
        },
        "example": {
            "step1": "curl 'https://nb-api-fs8p.onrender.com/generate?prompt=a beautiful cat'",
            "step2": "curl 'https://nb-api-fs8p.onrender.com/status?job_id=xxx-xxx'",
            "step3": "<img src='https://nb-api-fs8p.onrender.com/image?job_id=xxx-xxx' />"
        }
    }

@app.get("/ping", response_model=PingResponse)
async def ping_endpoint():
    """Keep-alive endpoint that returns 'pong' with job stats"""
    global request_count, last_ping_time
    request_count += 1
    current_time = datetime.now()
    last_ping_time = current_time
    
    uptime = (current_time - start_time).total_seconds()
    active_jobs = len([j for j in jobs.values() if j.status in [JobStatus.PENDING, JobStatus.GENERATING]])
    
    print(f"[PING] Request #{request_count} | Active jobs: {active_jobs}")
    
    return PingResponse(
        status="pong",
        message="Server is alive and running",
        timestamp=current_time.isoformat(),
        server_time=current_time.strftime("%Y-%m-%d %H:%M:%S"),
        request_count=request_count,
        uptime_seconds=uptime,
        active_jobs=active_jobs
    )

@app.get("/health")
async def health_check():
    """Health check endpoint with job statistics"""
    uptime = (datetime.now() - start_time).total_seconds()
    active_jobs = len([j for j in jobs.values() if j.status in [JobStatus.PENDING, JobStatus.GENERATING]])
    completed_jobs = len([j for j in jobs.values() if j.status == JobStatus.COMPLETED])
    failed_jobs = len([j for j in jobs.values() if j.status == JobStatus.FAILED])
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": uptime,
        "uptime_human": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m {int(uptime % 60)}s",
        "request_count": request_count,
        "jobs": {
            "active": active_jobs,
            "completed": completed_jobs,
            "failed": failed_jobs,
            "total": len(jobs)
        }
    }

@app.get("/generate")
async def generate_image_endpoint(
    background_tasks: BackgroundTasks,
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
    wait: bool = Query(False, description="If true, wait for completion before returning")
):
    """
    Start a new image generation job.
    
    Returns a job_id immediately. Use /status?job_id=xxx to check progress,
    or /image?job_id=xxx to get the final image.
    """
    
    # Collect reference images
    reference_images = []
    for i in range(1, 10):
        ref = locals().get(f'ref{i}')
        if ref:
            if re.search(r'\.(png|jpg|jpeg)$', ref, re.I):
                reference_images.append(ref)
            else:
                print(f"[!] Warning: Invalid reference image format for ref{i}: {ref}")
    
    # Create job
    job_id = str(uuid.uuid4())
    job = Job(job_id, prompt, reference_images)
    jobs[job_id] = job
    
    print(f"\n[API] Created job {job_id}")
    print(f"  Prompt: {prompt}")
    print(f"  Reference images: {len(reference_images)}")
    
    # Start background processing
    background_tasks.add_task(process_job, job)
    
    if wait:
        # Wait for completion (max 120 seconds)
        timeout = 120
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            if job.status == JobStatus.COMPLETED:
                return RedirectResponse(url=job.image_url, status_code=302)
            elif job.status == JobStatus.FAILED:
                raise HTTPException(status_code=500, detail=job.error_message)
            await asyncio.sleep(1)
        
        # Timeout - return job info
        return GenerateResponse(
            success=True,
            job_id=job_id,
            message="Job is still processing. Use /status endpoint to check progress.",
            status_url=f"/status?job_id={job_id}"
        )
    else:
        # Return job ID immediately
        return GenerateResponse(
            success=True,
            job_id=job_id,
            message="Job created successfully. Use the status_url to check progress.",
            status_url=f"/status?job_id={job_id}"
        )

@app.get("/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str = Query(..., description="The job ID to check")
):
    """Check the status of a generation job"""
    
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    # Calculate estimated time remaining (rough estimate)
    estimated_time_remaining = None
    if job.status == JobStatus.GENERATING and job.started_at:
        elapsed = (datetime.now() - job.started_at).total_seconds()
        # Average generation takes about 30 seconds
        if elapsed < 30:
            estimated_time_remaining = int(30 - elapsed)
    
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        image_url=job.image_url if job.status == JobStatus.COMPLETED else None,
        error_message=job.error_message,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        estimated_time_remaining=estimated_time_remaining
    )

@app.get("/image")
async def get_image(
    job_id: str = Query(..., description="The job ID to get the image from"),
    wait: bool = Query(True, description="If true, wait for completion before returning")
):
    """
    Get the generated image. Can be used directly in <img> tags.
    
    If the job is still processing and wait=true, it will wait up to 60 seconds.
    """
    
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    if job.status == JobStatus.COMPLETED:
        # Redirect to the generated image
        return RedirectResponse(url=job.image_url, status_code=302)
    
    elif job.status == JobStatus.FAILED:
        raise HTTPException(status_code=500, detail=job.error_message)
    
    elif job.status in [JobStatus.PENDING, JobStatus.GENERATING]:
        if wait:
            # Wait for completion (max 60 seconds)
            timeout = 60
            start_wait = time.time()
            while time.time() - start_wait < timeout:
                if job.status == JobStatus.COMPLETED:
                    return RedirectResponse(url=job.image_url, status_code=302)
                elif job.status == JobStatus.FAILED:
                    raise HTTPException(status_code=500, detail=job.error_message)
                await asyncio.sleep(1)
            
            # Timeout - return placeholder with job info
            return JSONResponse(
                status_code=202,
                content={
                    "status": "processing",
                    "message": "Image is still generating. Try again in a few seconds.",
                    "job_id": job_id,
                    "progress": job.progress,
                    "status_url": f"/status?job_id={job_id}"
                }
            )
        else:
            # Return placeholder
            return JSONResponse(
                status_code=202,
                content={
                    "status": "processing",
                    "message": "Image is still generating",
                    "job_id": job_id,
                    "progress": job.progress
                }
            )
    
    raise HTTPException(status_code=500, detail="Unknown job state")

@app.get("/example")
async def get_example_html():
    """Return an example HTML page demonstrating the API"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Luno Image Generator Demo</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            input, button {
                padding: 10px;
                margin: 5px;
                font-size: 16px;
            }
            input {
                width: 70%;
            }
            button {
                background: #007bff;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
            }
            button:hover {
                background: #0056b3;
            }
            #image-container {
                margin-top: 20px;
                text-align: center;
            }
            img {
                max-width: 100%;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            #status {
                margin-top: 10px;
                padding: 10px;
                background: #e9ecef;
                border-radius: 5px;
                font-family: monospace;
            }
            .progress-bar {
                width: 100%;
                height: 20px;
                background: #e0e0e0;
                border-radius: 10px;
                overflow: hidden;
                margin: 10px 0;
            }
            .progress-fill {
                height: 100%;
                background: #007bff;
                transition: width 0.3s;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎨 AI Image Generator</h1>
            <p>Enter a prompt and optional reference image URLs to generate an AI image</p>
            
            <div>
                <input type="text" id="prompt" placeholder="Enter your prompt..." style="width: 100%;">
                <input type="text" id="ref1" placeholder="Reference image 1 URL (optional)">
                <input type="text" id="ref2" placeholder="Reference image 2 URL (optional)">
                <input type="text" id="ref3" placeholder="Reference image 3 URL (optional)">
                <button onclick="generateImage()">Generate Image</button>
            </div>
            
            <div id="status"></div>
            <div class="progress-bar" id="progress-bar" style="display: none;">
                <div class="progress-fill" id="progress-fill" style="width: 0%"></div>
            </div>
            <div id="image-container"></div>
        </div>
        
        <script>
            let currentJobId = null;
            let statusInterval = null;
            
            async function generateImage() {
                const prompt = document.getElementById('prompt').value;
                if (!prompt) {
                    alert('Please enter a prompt');
                    return;
                }
                
                // Build URL
                let url = `/generate?prompt=${encodeURIComponent(prompt)}`;
                const refs = ['ref1', 'ref2', 'ref3'];
                for (let ref of refs) {
                    const value = document.getElementById(ref).value;
                    if (value) {
                        url += `&${ref}=${encodeURIComponent(value)}`;
                    }
                }
                
                // Clear previous results
                document.getElementById('image-container').innerHTML = '';
                document.getElementById('status').innerHTML = 'Starting generation...';
                document.getElementById('progress-bar').style.display = 'block';
                
                try {
                    // Start job
                    const response = await fetch(url);
                    const data = await response.json();
                    
                    if (data.success && data.job_id) {
                        currentJobId = data.job_id;
                        document.getElementById('status').innerHTML = `Job created: ${currentJobId}<br>Status: Processing...`;
                        startPolling(currentJobId);
                    } else {
                        document.getElementById('status').innerHTML = 'Error: Failed to create job';
                    }
                } catch (error) {
                    document.getElementById('status').innerHTML = `Error: ${error.message}`;
                }
            }
            
            async function startPolling(jobId) {
                if (statusInterval) clearInterval(statusInterval);
                
                statusInterval = setInterval(async () => {
                    try {
                        const response = await fetch(`/status?job_id=${jobId}`);
                        const data = await response.json();
                        
                        // Update progress
                        const fill = document.getElementById('progress-fill');
                        fill.style.width = `${data.progress}%`;
                        
                        document.getElementById('status').innerHTML = `
                            Status: ${data.status}<br>
                            Progress: ${data.progress}%<br>
                            ${data.estimated_time_remaining ? `ETA: ~${data.estimated_time_remaining}s` : ''}
                        `;
                        
                        if (data.status === 'completed') {
                            clearInterval(statusInterval);
                            document.getElementById('progress-bar').style.display = 'none';
                            document.getElementById('status').innerHTML = '✅ Generation complete!';
                            
                            // Display the image
                            const img = document.createElement('img');
                            img.src = `/image?job_id=${jobId}`;
                            img.alt = 'Generated Image';
                            document.getElementById('image-container').appendChild(img);
                        } else if (data.status === 'failed') {
                            clearInterval(statusInterval);
                            document.getElementById('status').innerHTML = `❌ Failed: ${data.error_message}`;
                            document.getElementById('progress-bar').style.display = 'none';
                        }
                    } catch (error) {
                        console.error('Polling error:', error);
                    }
                }, 2000);
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

from fastapi.responses import HTMLResponse

# ============================================================
# MAIN EXECUTION
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("Luno Studio Image Generator API with Job Tracking")
    print("=" * 70)
    print("\n🚀 Starting FastAPI server...")
    print("\n📋 New Job System Endpoints:")
    print("   GET /generate     - Start a new job (returns job_id)")
    print("   GET /status       - Check job status")
    print("   GET /image        - Get the generated image (use in <img> tags)")
    print("   GET /example      - Interactive HTML demo page")
    print("\n💡 Usage Examples:")
    print("   1. Start a job:")
    print("      curl 'https://nb-api-fs8p.onrender.com/generate?prompt=a beautiful cat'")
    print("\n   2. Check status:")
    print("      curl 'https://nb-api-fs8p.onrender.com/status?job_id=your-job-id'")
    print("\n   3. Use in HTML:")
    print("      <img src='https://nb-api-fs8p.onrender.com/image?job_id=your-job-id' />")
    print("\n📚 Interactive API docs: http://localhost:8000/docs")
    print("🎨 Try the demo: http://localhost:8000/example")
    print("=" * 70)
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        log_level="info",
        timeout_keep_alive=65
    )
