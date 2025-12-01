# subsScraperMvp.py (FINAL - Safe with randomization & time-of-day logic)
from pathlib import Path
import json
import time
import random
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
import requests

from dotenv import load_dotenv
import os

load_dotenv()




SESSION_FILE = Path("session.json")
JOBS_FILE = Path("previous_jobs.json")
BROWSER_DATA_DIR = Path("browser_data")
UI_URL = "https://slcschools.eschoolsolutions.com/ui/"
API_URL = "https://slcschools.eschoolsolutions.com/api/job/available"

# Config
BASE_POLL_INTERVAL_SECONDS = 3 * 60 
NIGHT_POLL_INTERVAL_SECONDS = 30 * 60  
KEEPALIVE_INTERVAL_SECONDS = 15 * 60 
MAX_CONSECUTIVE_ERRORS = 3
PUSHBULLET_API_KEY  = os.getenv("PUSHBULLET_API_KEY")

# Work hours (6 AM - 10 PM)
WORK_START_HOUR = 6
WORK_END_HOUR = 22


def get_poll_interval():
    """Return poll interval based on time of day, with randomization."""
    now = datetime.now()
    
    # Nighttime (10 PM - 6 AM): slower polling
    if now.hour >= WORK_END_HOUR or now.hour < WORK_START_HOUR:
        # 25-35 minutes overnight (randomized)
        return random.randint(25 * 60, 35 * 60)
    else:
        # 2.5-3.5 minutes during work hours (randomized)
        return random.randint(int(2.5 * 60), int(3.5 * 60))


def get_request_body():
    """Generate request body with current dates."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=7, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=120)
    
    return {
        "filterOption": {
            "jobStart": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "jobEnd": end.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            "jobLocationType": ["NONE"],
            "durationType": ["NONE"],
            "jobInstruction": ["NONE"],
            "locationIdList": [0],
            "locationGroupIdList": [0],
            "classificationIdList": [0],
            "teacher": None,
            "requested": ["NONE"],
        },
        "paginationOption": {"firstResult": 0, "maxResult": 25},
        "loadJobs": True,
        "restrictCoolDown": False,
    }


def log(message):
    """Print with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def push_notification(title, body, url=None):
    """Send Pushbullet notification."""
    try:
        data = {
            "type": "link" if url else "note",
            "title": title,
            "body": body
        }
        if url:
            data["url"] = url
        
        headers = {
            "Access-Token": PUSHBULLET_API_KEY,
            "Content-Type": "application/json"
        }
        
        r = requests.post("https://api.pushbullet.com/v2/pushes",
                         json=data, headers=headers)
        
        if r.status_code != 200:
            log(f"⚠️ Pushbullet failed: {r.text}")
        else:
            log("📱 Pushbullet sent!")
    except Exception as e:
        log(f"⚠️ Pushbullet error: {e}")


def automated_login():
    """
    Automatically re-login by navigating to Microsoft login URL.
    Works because persistent browser has 'remember me' cookies.
    """
    log("🔄 Attempting automated re-login (like Firefox)...")
    
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=False,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(UI_URL, wait_until="networkidle", timeout=30000)
            
            # Wait for page to fully load
            page.wait_for_timeout(3000)
            
            # Check if already logged in
            if "jobs" in page.url or "substitute" in page.url:
                log("✅ Already logged in!")
                context.storage_state(path=str(SESSION_FILE))
                context.close()
                return True
            
            # Navigate directly to Microsoft login URL (works reliably)
            log("Navigating to Microsoft login...")
            try:
                page.goto("https://slcschools.eschoolsolutions.com/sso/oidc/login/2/?source=web", 
                         wait_until="networkidle", timeout=30000)
                log("✅ Navigated to login URL")
            except Exception as e:
                log(f"❌ Navigation failed: {e}")
                context.close()
                return False
            
            # Wait for login to complete (redirect to jobs page)
            log("Waiting for login to complete...")
            try:
                page.wait_for_url("**/jobs/**", timeout=30000)
                log("✅ Automated login successful!")
                page.wait_for_timeout(2000)
                context.storage_state(path=str(SESSION_FILE))
                context.close()
                return True
            except:
                # Check current URL
                current_url = page.url
                log(f"Timeout waiting for jobs page. Current URL: {current_url}")
                
                # Maybe we're already there
                if "jobs" in current_url or "substitute" in current_url:
                    log("✅ Actually on jobs page!")
                    context.storage_state(path=str(SESSION_FILE))
                    context.close()
                    return True
                else:
                    log("⚠️ Not on jobs page after 30s")
                    context.close()
                    return False
                
    except Exception as e:
        log(f"❌ Automated login error: {e}")
        return False


def manual_login():
    """Manual login with user interaction (first-time setup)."""
    log("Opening browser for manual login...")
    
    push_notification(
        "🔑 Manual Login Needed",
        "Please login in the browser window that just opened.",
        UI_URL
    )
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(UI_URL, wait_until="networkidle")
        
        print("\n" + "="*60)
        print("BROWSER OPENED - Please log in:")
        print("1. Click 'Sign in with Microsoft'")
        print("2. Complete authentication (if prompted)")
        print("3. Wait until you see the jobs page")
        print("4. Come back here and press ENTER")
        print("="*60 + "\n")
        
        input("Press ENTER when logged in... ")
        
        context.storage_state(path=str(SESSION_FILE))
        log(f"✅ Session saved")
        context.close()
        
        time.sleep(5)
        
        push_notification(
            "✅ Login Complete",
            "Browser profile saved. Future logins will be automatic!"
        )


def keepalive_session():
    """Reset idle timer with real browser window."""
    log("🔄 Keepalive - resetting idle timer...")
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=False,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(UI_URL, wait_until="networkidle")
            
            # Simulate activity
            page.mouse.move(100, 100)
            page.mouse.click(100, 100)
            page.wait_for_timeout(2000)
            
            context.storage_state(path=str(SESSION_FILE))
            context.close()
            log("✅ Keepalive successful")
    except Exception as e:
        log(f"⚠️ Keepalive failed: {e}")


def fetch_jobs_with_session():
    """Fetch jobs using headless browser."""
    if not SESSION_FILE.exists():
        raise RuntimeError("No session found")
    
    request_body = get_request_body()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(SESSION_FILE))
        page = context.new_page()
        
        page.goto(UI_URL, wait_until="networkidle")
        
        api_result = page.evaluate("""
            async ({ apiUrl, requestBody }) => {
                try {
                    const userRaw = window.localStorage.getItem('user');
                    if (!userRaw) return { status: 0, error: 'No user' };
                    
                    const userObj = JSON.parse(userRaw);
                    let authHeader = userObj.token;
                    if (!authHeader.startsWith('Bearer ')) {
                        authHeader = 'Bearer ' + authHeader;
                    }
                    
                    const response = await fetch(apiUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': authHeader,
                            'lang': 'en'
                        },
                        body: JSON.stringify(requestBody)
                    });
                    
                    const status = response.status;
                    const text = await response.text();
                    let data;
                    try { data = JSON.parse(text); } catch (e) { data = text; }
                    
                    return { status, data };
                } catch (error) {
                    return { status: 0, error: error.message };
                }
            }
        """, {"apiUrl": API_URL, "requestBody": request_body})
        
        browser.close()
        
        status = api_result.get('status', 0)
        data = api_result.get('data')
        error = api_result.get('error')
        
        if error:
            log(f"❌ JS error: {error}")
            return None, 0
        
        return data, status


def load_previous_jobs():
    if JOBS_FILE.exists():
        with open(JOBS_FILE) as f:
            return set(json.load(f))
    return set()


def save_current_jobs(job_ids):
    with open(JOBS_FILE, 'w') as f:
        json.dump(list(job_ids), f)


def main_loop():
    """Main autonomous polling loop."""
    # First-time setup if needed
    if not SESSION_FILE.exists() or not BROWSER_DATA_DIR.exists():
        log("First-time setup - manual login required")
        manual_login()
    
    previous_jobs = load_previous_jobs()
    consecutive_errors = 0
    last_keepalive = time.time()
    last_poll = 0  # Poll immediately on startup
    
    log("🚀 Starting autonomous job monitoring...")
    log(f"   - Polling: 2.5-3.5 min (work hours), 25-35 min (night)")
    log(f"   - Work hours: {WORK_START_HOUR}:00 - {WORK_END_HOUR}:00")
    log(f"   - Keepalive every {KEEPALIVE_INTERVAL_SECONDS//60} min")
    log(f"   - Auto re-login enabled (like Firefox)")
    
    while True:
        try:
            current_time = time.time()
            
            # Get current poll interval based on time of day
            poll_interval = get_poll_interval()
            
            # Keepalive check
            if current_time - last_keepalive >= KEEPALIVE_INTERVAL_SECONDS:
                keepalive_session()
                last_keepalive = current_time
            
            # Poll check
            if current_time - last_poll >= poll_interval:
                now = datetime.now()
                if now.hour >= WORK_END_HOUR or now.hour < WORK_START_HOUR:
                    log("📡 Fetching jobs (night mode - slower polling)...")
                else:
                    log("📡 Fetching jobs...")
                    
                data, status = fetch_jobs_with_session()
                last_poll = current_time
                
                # Rate limited - STOP
                if status == 429:
                    log("🚨 RATE LIMITED - STOPPING")
                    push_notification(
                        "🚨 SCRAPER STOPPED - Rate Limited",
                        "Got 429. Script stopped to avoid ban.",
                        UI_URL
                    )
                    break
                
                # Session expired - AUTO RE-LOGIN
                if status in (0, 401, 403):
                    log(f"⚠️ Session expired ({status}) - trying auto re-login")
                    
                    if automated_login():
                        log("✅ Auto re-login successful!")
                        consecutive_errors = 0
                        last_keepalive = current_time
                        last_poll = 0  # Poll immediately after re-login
                        continue
                    else:
                        log("⚠️ Auto re-login failed, need manual login")
                        push_notification(
                            "🔑 Manual Login Needed",
                            "Auto re-login failed. Check terminal.",
                            UI_URL
                        )
                        if SESSION_FILE.exists():
                            SESSION_FILE.unlink()
                        manual_login()
                        consecutive_errors = 0
                        last_keepalive = current_time
                        last_poll = 0
                        continue
                
                # Bad request
                if status == 400:
                    consecutive_errors += 1
                    log(f"⚠️ 400 error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        log("🚨 Too many 400s - STOPPING")
                        push_notification(
                            "🚨 SCRAPER STOPPED",
                            f"{MAX_CONSECUTIVE_ERRORS} consecutive 400 errors.",
                            UI_URL
                        )
                        break
                    continue
                
                # Success - check for new jobs
                if status == 200 and isinstance(data, list):
                    consecutive_errors = 0
                    current_job_ids = {job['jobId'] for job in data}
                    new_jobs = current_job_ids - previous_jobs
                    
                    if new_jobs:
                        log(f"🎉 NEW JOBS: {len(new_jobs)}")
                        
                        job_list = []
                        for job in data:
                            if job['jobId'] in new_jobs:
                                log(f"   📋 #{job['jobId']}: {job['locationName']} - {job['classfName']}")
                                log(f"      Start: {job['jobStart']} | Duration: {job['durationType']}")
                                job_list.append(f"{job['locationName']} - {job['classfName']}")
                        
                        push_notification(
                            f"🎉 {len(new_jobs)} New Job(s)!",
                            "\n".join(job_list[:5]),
                            "https://slcschools.eschoolsolutions.com/ui/#/substitute/jobs/available"
                        )
                    else:
                        log(f"✅ No new jobs ({len(current_job_ids)} total)")
                    
                    previous_jobs = current_job_ids
                    save_current_jobs(current_job_ids)
                    
                elif status == 200:
                    consecutive_errors = 0
                    log("✅ No jobs available")
                    previous_jobs = set()
                    save_current_jobs([])
                    
                else:
                    consecutive_errors += 1
                    log(f"⚠️ Unexpected {status} ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        log("🚨 Too many errors - STOPPING")
                        push_notification(
                            "🚨 SCRAPER STOPPED",
                            f"{MAX_CONSECUTIVE_ERRORS} consecutive errors.",
                            UI_URL
                        )
                        break
            
            time.sleep(30)
            
        except KeyboardInterrupt:
            log("🛑 Stopped by user")
            push_notification("⏹️ Scraper Stopped", "Manually stopped.")
            break
            
        except Exception as e:
            consecutive_errors += 1
            log(f"❌ Error: {e}")
            
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                log("🚨 CRASHED")
                push_notification(
                    "🚨 SCRAPER CRASHED",
                    f"Crashed after {MAX_CONSECUTIVE_ERRORS} errors: {str(e)[:100]}",
                    UI_URL
                )
                break
            else:
                log(f"   Retry in 30s ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")
                time.sleep(30)


if __name__ == "__main__":
    main_loop()
