"""
QA Agent V6.0 — server.py
Complete rewrite with:
  - Vision-based perception (screenshot + DOM)
  - Task Planning phase
  - Loop Detection & auto-break
  - Smart Element Fallback chain
  - Extended 22-action library
  - Inbox polling with retries
  - Context-aware chat compression
"""

import asyncio
import sys

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import json, os, base64, re, uuid, datetime
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from playwright.async_api import async_playwright
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

# ── App ───────────────────────────────────────────────────────────
app = FastAPI()

for d in ["sessions", "browser_data", "videos", "extracted_data"]:
    os.makedirs(d, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
async def on_startup():
    import webbrowser
    def _open():
        try:
            webbrowser.open("http://localhost:8000")
        except:
            pass
    asyncio.get_event_loop().call_later(0.5, _open)

@app.get("/")
async def serve_ui():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# ── Vault ─────────────────────────────────────────────────────────
VAULT_FILE = "vault.json"

def load_vault():
    if os.path.exists(VAULT_FILE):
        try:
            with open(VAULT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_vault(data):
    with open(VAULT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ── Sessions ──────────────────────────────────────────────────────
class SessionData(BaseModel):
    name: str
    html: str

@app.get("/api/vault")
async def api_vault():
    return load_vault()

@app.get("/api/sessions")
async def api_sessions_list():
    sessions = []
    for fn in os.listdir("sessions"):
        if fn.endswith(".json"):
            try:
                with open(os.path.join("sessions", fn), "r", encoding="utf-8") as f:
                    d = json.load(f)
                    sessions.append({"id": fn[:-5], "name": d.get("name","?"), "date": d.get("date","")})
            except: pass
    return sorted(sessions, key=lambda x: x["date"], reverse=True)

@app.get("/api/sessions/{sid}")
async def api_session_get(sid: str):
    p = os.path.join("sessions", f"{sid}.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"error": "not found"}

@app.post("/api/sessions")
async def api_session_save(data: SessionData):
    sid = str(uuid.uuid4())
    with open(os.path.join("sessions", f"{sid}.json"), "w", encoding="utf-8") as f:
        json.dump({"id": sid, "name": data.name, "html": data.html,
                   "date": datetime.datetime.now().isoformat()}, f)
    return {"status": "ok", "id": sid}

@app.get("/api/data")
async def api_data_list():
    """Return all extracted data from the extracted_data folder."""
    all_data = []
    if os.path.exists("extracted_data"):
        for fn in sorted(os.listdir("extracted_data"), reverse=True):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join("extracted_data", fn), "r", encoding="utf-8") as f:
                        item = json.load(f)
                        if isinstance(item, list):
                            all_data.extend(item)
                        else:
                            all_data.append(item)
                except: pass
    return all_data

@app.get("/api/models")
async def api_models():
    """Return available LLM configs so the frontend can build the dropdown dynamically."""
    labels = [
        ("0", "⚡ Gemini 3.8 Flash"),
        ("1", "🩶 Gemini 3.1 Flash-Lite"),
        ("2", "🟢 Groq GPT-OSS-20B"),
        ("3", "🟢 Groq Llama 3.3 70B"),
        ("4", "🌐 OpenRouter Auto"),
    ]
    # Add Ollama entry if it was detected at startup
    ollama_cfg = next((c for c in API_CONFIGS if c.get("provider") == "ollama"), None)
    if ollama_cfg:
        labels.append((str(API_CONFIGS.index(ollama_cfg)), ollama_cfg.get("label", "🧠 Ollama")))
    return [{"value": v, "label": l} for v, l in labels]

# ── LLM Pool ─────────────────────────────────────────────────────
# Index map (matches frontend dropdown):
#   0 = Gemini 3.8 Flash      ✅ VERIFIED
#   1 = Gemini 3.1 Flash-Lite ✅ VERIFIED
#   2 = Groq GPT-OSS-20B      ✅ VERIFIED
#   3 = Groq Llama 3.3 70B    ✅ VERIFIED
#   4 = OpenRouter Auto        ✅ VERIFIED
API_CONFIGS = []

gemini_key = os.environ.get("GEMINI_API_KEY")
if gemini_key:
    # [0] Gemini 3.8 Flash — fast, capable, verified
    API_CONFIGS.append({
        "provider": "google",
        "api_key": gemini_key,
        "model": "gemini-3.8-flash"
    })
    # [1] Gemini 3.1 Flash-Lite — lighter, cheaper, verified July 24 2026
    API_CONFIGS.append({
        "provider": "google",
        "api_key": gemini_key,
        "model": "gemini-3.1-flash-lite"
    })

# [2] & [3] Groq — VERIFIED WORKING July 2026
#   openai/gpt-oss-20b ✅ | llama-3.3-70b-versatile ✅
#   DEPRECATED: meta-llama/llama-4-scout-17b ❌ (removed July 17)
for k in ["GROQ_API_KEY_1", "GROQ_API_KEY_2"]:
    val = os.environ.get(k)
    if val:
        model = "openai/gpt-oss-20b" if k == "GROQ_API_KEY_1" else "llama-3.3-70b-versatile"
        API_CONFIGS.append({
            "provider": "openai",
            "api_key": val,
            "base_url": "https://api.groq.com/openai/v1",
            "model": model
        })

# [4] OpenRouter Auto — always works, routes to best available free model
or_key = os.environ.get("OPENROUTER_API_KEY")
if or_key:
    API_CONFIGS.append({
        "provider": "openai",
        "api_key": or_key,
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openrouter/auto",
        "extra_headers": {
            "HTTP-Referer": "https://rio-agent.local",
            "X-Title": "Rio Agent"
        }
    })

# [5] Ollama — local OR remote inference, no API key needed
# Set OLLAMA_HOST in .env to use a remote machine (e.g. 192.168.100.4)
# Defaults to localhost if not set
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "localhost").strip().rstrip("/")
# Remove protocol if user included it
if OLLAMA_HOST.startswith("http"):
    OLLAMA_HOST = OLLAMA_HOST.split("//", 1)[-1]
OLLAMA_BASE_URL = f"http://{OLLAMA_HOST}:11434"

def _detect_ollama_model():
    """Ping Ollama (local or remote) and return the first available model name."""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/tags",
            headers={"User-Agent": "rio-agent"}
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
            models = data.get("models", [])
            if models:
                return models[0]["name"]  # e.g. "gemma4:e2b"
    except Exception as e:
        print(f"[Ollama] Could not reach {OLLAMA_BASE_URL}: {e}")
    return None

_ollama_model = _detect_ollama_model()
if _ollama_model:
    API_CONFIGS.append({
        "provider": "ollama",
        "api_key": "ollama",
        "base_url": f"{OLLAMA_BASE_URL}/v1",
        "model": _ollama_model,
        "label": f"🧠 Ollama {OLLAMA_HOST} ({_ollama_model})"
    })
    print(f"[Ollama] ✅ Connected to {OLLAMA_BASE_URL} — model: {_ollama_model}")
else:
    print(f"[Ollama] ❌ Not reachable at {OLLAMA_BASE_URL} — skipping")

current_key_idx = 0
consecutive_errors = 0

def get_llm(idx=None):
    global current_key_idx
    cfg = API_CONFIGS[idx if idx is not None else current_key_idx] if API_CONFIGS else {}
    if cfg.get("provider") == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=cfg.get("model", "gemini-3.8-flash"),
            google_api_key=cfg.get("api_key", ""),
            temperature=0.1
        )
    # Ollama and OpenAI-compatible providers (Groq, OpenRouter, Ollama)
    kwargs = dict(
        base_url=cfg.get("base_url", "http://localhost:11434/v1"),
        api_key=cfg.get("api_key", "ollama"),
        model=cfg.get("model", "gemma4:e2b"),
        temperature=0.1,
        max_tokens=2048,
    )
    # Pass extra headers for OpenRouter (required for routing)
    if cfg.get("extra_headers"):
        kwargs["default_headers"] = cfg["extra_headers"]
    return ChatOpenAI(**kwargs)

# ── Helper for LangChain LLM responses ───────────────────────────
def unwrap_llm_text(content) -> str:
    """Safely extracts raw text from LangChain response.content (handles str, list of dicts, TextBlock objects)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))
                elif "content" in item:
                    parts.append(str(item["content"]))
            elif hasattr(item, "text"):
                parts.append(str(item.text))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(content).strip()

# ── WebSocket helpers ─────────────────────────────────────────────
async def send(ws: WebSocket, content: str, image: str = None, requires_input: bool = False):
    try:
        payload = {"type": "bot", "content": content}
        if image:           payload["image"] = image
        if requires_input:  payload["requires_input"] = True
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass

async def recv(ws: WebSocket) -> dict:
    raw = await ws.receive_text()
    return json.loads(raw)

# ── DOM Extractor ─────────────────────────────────────────────────
DOM_JS = """() => {
    const seen = new Set();
    const tags = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="tab"],[role="menuitem"],[role="combobox"],[role="textbox"],[contenteditable="true"],[data-testid],label,[tabindex]';
    const els = Array.from(document.querySelectorAll(tags));
    const result = [];
    let idx = 0;
    els.forEach(el => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const visible = rect.width > 0 && rect.height > 0
            && style.visibility !== 'hidden'
            && style.display !== 'none'
            && parseFloat(style.opacity) > 0
            && rect.top < window.innerHeight + 300
            && rect.bottom > -300;
        if (!visible) return;

        let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('data-testid') || '').substring(0, 80).trim();
        const testid = el.getAttribute('data-testid') || '';
        const role = el.getAttribute('role') || el.type || '';
        const key = el.tagName + '|' + (el.name||'') + '|' + (el.id||'') + '|' + (el.placeholder||'') + '|' + testid + '|' + role + '|' + text + '|' + Math.round(rect.top) + '|' + Math.round(rect.left);

        if (seen.has(key)) return;
        seen.add(key);

        el.setAttribute('data-agent-id', idx);
        let options = null;
        if (el.tagName.toLowerCase() === 'select') {
            options = Array.from(el.options || []).map(o => (o.text || o.value).trim()).slice(0, 15);
        }
        result.push({
            id: String(idx),
            tag: el.tagName.toLowerCase(),
            text: text,
            type: role,
            name: el.name || el.id || el.getAttribute('aria-label') || testid || '',
            value: el.value || '',
            ...(options ? { options: options } : {})
        });
        idx++;
    });
    const pageText = (document.body ? document.body.innerText : '').substring(0, 3000);
    return { elements: result, page_text: pageText };
}"""

# ── System Prompt ─────────────────────────────────────────────────
SYSTEM_PROMPT = """⚠️ OUTPUT FORMAT RULE — READ FIRST:
You MUST respond with ONLY a valid JSON array. No text before or after. No markdown fences. No explanation.
CORRECT: [{"action": "goto", "url": "https://example.com"}]
WRONG: "Sure! I'll navigate to..." or ```json [...]```

You are Rio, an elite autonomous web agent. You control a real browser to complete tasks. When interacting or updating your memory, always remember that your name is Rio.

═══ YOUR CAPABILITIES ═══
GOAL: {task}
MEMORY: {memory}
VAULT ACCOUNTS: {vault_keys}
TASK PLAN: {plan}

═══ CRITICAL RULES ═══
1. NEVER repeat the same action twice in a row.
2. Always update memory when you complete a sub-task.
3. Always save accounts to vault BEFORE calling done.
4. If an element is not responding, try a fallback: scroll, wait, then use js_click.
5. Read the page text carefully — error messages and confirmations are there.
6. If you're stuck in a loop (same action 3 times), use ask_user.
7. After every 10 steps, take a screenshot to verify progress visually.
8. PREFER clear_and_type over type for input fields — it clears first.
9. When creating an account, always use generate_email first.
10. FOR DROPDOWNS / SELECT BOXES: DO NOT use 'click' on dropdowns! Native <select> elements CANNOT be opened with 'click'. ALWAYS use 'select_option' directly (e.g. {{"action": "select_option", "element_id": "2", "value": "20"}} or "Egypt").
11. To click options in custom dropdowns or text on screen, use 'click_text' (e.g. {{"action": "click_text", "text": "Egypt"}}).
12. NEVER OUTPUT PLAIN TEXT OR CONVERSATIONAL EXPLANATIONS! If you want to explain reasoning, ALWAYS use {{"action": "update_memory", "text": "..."}}. Plain text breaks formatting.
13. FOR RICH TEXT EDITORS (like X/Twitter "What's happening?"): Use 'type' or 'clear_and_type' directly on the element_id.
14. X / TWITTER 280-CHARACTER LIMIT: Free X (Twitter) accounts have a STRICT limit of 280 characters! Keep all tweets VERY SHORT (under 180 characters). If you see red text, negative counter (e.g. -31), or 'Upgrade to Premium', YOU MUST use 'clear_and_type' to type a shorter tweet under 180 characters!
15. VERIFY PUBLISHING BEFORE 'DONE' (ESPECIALLY FACEBOOK/TWITTER): You MUST click the 'Post' / 'Tweet' / 'التالي' (Next) / 'نشر' (Publish) button. For Facebook, you often have to click 'التالي' (Next) then 'نشر' (Post). You are NOT DONE until you verify that the post was sent (the compose box disappears and you see the post on the timeline). NEVER call 'done' while the compose modal, 'التالي' (Next), or 'Post' button is still on screen!
16. DO NOT CALL 'DONE' PREMATURELY! You must execute all steps in your TASK PLAN before calling 'done'. Calling 'done' after only 1 or 2 steps without achieving the actual user objective is strictly forbidden. If the task is to publish a post, you MUST confirm it is published on the timeline before calling done.
20. FACEBOOK POST-PUBLISH POPUPS: After clicking publish, Facebook often shows popups like 'تسهيل التواصل معك' (Add WhatsApp button) or 'Boost Post'. You MUST use `click_text` to click 'ليس الآن' (Not now) or the 'x' close button to dismiss these popups. You cannot call 'done' while these popups are active!
21. FACEBOOK COMPOSE BOX TYPING: The Facebook post compose text area is a React contenteditable div. NEVER use 'type' action with an element_id for it — it will silently fail. ALWAYS use {{"action":"fb_type","text":"your post content here"}} to type into Facebook's compose modal. This action directly finds the box without needing an element_id.
22. USE click_xy FOR CANVAS/SVG/IFRAME: If an element has no data-agent-id or is inside an iframe/canvas, use {{"action":"click_xy","x":X,"y":Y}} with the approximate screen coordinates from the screenshot.
23. USE compare_screenshot TO VERIFY IMPORTANT RESULTS: After publishing a post, submitting a form, or completing a critical step, use compare_screenshot to visually verify the result instead of assuming done.
17. NEVER NAVIGATE TO X.COM OR TWITTER UNLESS THE USER EXPLICITLY MENTIONED 'X.COM' OR 'TWITTER'! Having an account saved in Vault DOES NOT mean you should use it for general tasks. For job/general tasks, use google.com, khamsat.com, mostaql.com, or remotive.com.
18. FOR JOB/FREELANCE TASKS: You are NEVER done just by looking at a job listing or searching! You MUST actually try to APPLY for a specific job, REGISTER an account, or WRITE A PROPOSAL. Do not call 'done' until you have attempted to submit a proposal, apply, or create a full account.
19. FOR FACEBOOK / META SIGNUP FORMS — date of birth and gender fields are CUSTOM dropdowns, NOT native <select>. ALWAYS use 'fb_select' action for them:
    - Date fields: {{"action":"fb_select","label":"اليوم","value":"1"}} / {{"action":"fb_select","label":"الشهر","value":"يناير"}} / {{"action":"fb_select","label":"السنة","value":"1995"}}
    - Gender: {{"action":"fb_select","label":"الجنس","value":"ذكر"}} or click the dropdown then use click_text with the exact option text shown.
    - If fb_select doesn't work: click the dropdown trigger element, wait 0.5s, then use click_text to pick the value from the open list.
24. MULTI-TAB AWARENESS: When a link opens in a new tab, you'll see a notification. Use switch_tab to navigate to it. Always use list_tabs if you're unsure which tab you're on.

═══ AVAILABLE ACTIONS ═══
Respond ONLY with a valid JSON array of action objects.

# NAVIGATION
{{"action":"goto","url":"https://..."}}
{{"action":"new_tab","url":"https://..."}}
{{"action":"switch_tab","index":1}}   ← SWITCH TO ANOTHER OPEN TAB
{{"action":"list_tabs"}}   ← SEE ALL OPEN TABS
{{"action":"close_tab","index":1}}   ← CLOSE A TAB
{{"action":"go_back"}}
{{"action":"go_forward"}}
{{"action":"reload"}}

# INTERACTION
{{"action":"click","element_id":"123"}}
{{"action":"double_click","element_id":"123"}}
{{"action":"right_click","element_id":"123"}}
{{"action":"js_click","element_id":"123"}}
{{"action":"hover","element_id":"123"}}
{{"action":"type","element_id":"123","text":"hello"}}
{{"action":"clear_and_type","element_id":"123","text":"hello"}}
{{"action":"press_key","key":"Enter"}}
{{"action":"select_option","element_id":"123","value":"option_value"}}
{{"action":"click_text","text":"ذكر"}}
{{"action":"fb_select","label":"الجنس","value":"ذكر"}}   ← FOR FACEBOOK DATE/GENDER CUSTOM DROPDOWNS
{{"action":"fb_type","text":"post content here"}}   ← FOR FACEBOOK POST COMPOSE BOX ONLY
{{"action":"upload_file","element_id":"123","path":"./file.txt"}}
{{"action":"drag_drop","from_id":"1","to_id":"2"}}

# CONTROL
{{"action":"ask_user","question":"What should I do next?"}}
{{"action":"human_pause","message":"Please solve the CAPTCHA then click Continue"}}   ← FOR CAPTCHA / 2FA / MANUAL STEPS
{{"action":"fill_form","fields":{{"الاسم":"Rio","البريد الإلكتروني":"test@example.com"}}}}   ← FILL MULTIPLE FIELDS AT ONCE
{{"action":"done","message":"Task completed successfully."}}
{{"action":"replan","reason":"The page layout is different than expected."}}

# PAGE CONTROL
{{"action":"click_xy","x":340,"y":220}}   ← CLICK BY SCREEN COORDINATES (for canvas/SVG/iframe)
{{"action":"wait_for_element","selector":"#success-msg","timeout":10}}   ← WAIT FOR ELEMENT TO APPEAR
{{"action":"extract_table","selector":"table.results","key":"job_listings"}}   ← EXTRACT TABLE AS JSON
{{"action":"compare_screenshot","description":"Did the post appear on the timeline?"}}   ← VISION VERIFICATION
{{"action":"scroll","direction":"down"}}
{{"action":"scroll","direction":"up"}}
{{"action":"scroll_to_element","element_id":"123"}}
{{"action":"wait","seconds":3}}
{{"action":"screenshot"}}

# DATA & MEMORY
{{"action":"extract_text","selector":"h1"}}
{{"action":"find_text_on_page","text":"Verification Code"}}
{{"action":"save_data","key":"result","data":"value"}}
{{"action":"update_memory","text":"..."}}
{{"action":"save_account","platform":"x.com","username":"...","password":"..."}}
{{"action":"get_account","platform":"x.com"}}

# EMAIL
{{"action":"generate_email"}}
{{"action":"check_inbox","email":"user@domain.com"}}
{{"action":"wait_for_email","email":"user@domain.com","timeout_seconds":60}}

# CONTROL
{{"action":"ask_user","question":"What should I do next?"}}
{{"action":"done","message":"Task completed successfully."}}
{{"action":"replan","reason":"The page layout is different than expected."}}

# ── SYSTEM PROMPT EXAMPLES ────────────────────────
═══ EXAMPLE ═══
[
  {{"action":"update_memory","text":"Starting task: navigate to target URL"}},
  {{"action":"goto","url":"https://google.com"}}
]"""

# ── Planning Phase ────────────────────────────────────────────────
async def generate_plan(task: str, vault_keys: list, ws: WebSocket) -> str:
    await send(ws, "🧠 **Planning Phase:** Analyzing task and creating execution plan...")
    vault_str = f"{vault_keys} (Use ONLY if task explicitly asks to log into them. DO NOT navigate to x.com or Vault sites for job/general tasks!)"
    prompt = [
        {"role": "system", "content": "You are a strategic web automation planner."},
        {"role": "user", "content": f"""Create a concise numbered execution plan for this task:
TASK: {task}
SAVED VAULT ACCOUNTS: {vault_str}

CRITICAL RULES FOR PLANNER:
1. If the task is about finding online jobs, earning money, or freelancing: DO NOT GO TO SOCIAL MEDIA (x.com/twitter)! Go to real job sites like google.com, khamsat.com, mostaql.com, or remotive.com.
2. Only navigate to x.com if the user's task EXPLICITLY contains the words 'x.com' or 'twitter' or 'tweet'.
3. For job/freelance tasks: The plan MUST include steps to actually APPLY for a job, WRITE a proposal, or REGISTER an account. Finding a job listing is NOT enough to complete the goal.
4. For social media posts (Facebook, Twitter, etc): The plan MUST include steps to WRITE the post, CLICK 'Post' or 'Next'/'التالي' then 'Publish'/'نشر', and finally VERIFY the post is live on the timeline.

Return ONLY a numbered list of high-level steps (max 10 steps). Be specific about which websites, what data to use, and what to verify. Example:
1. Navigate to job site (e.g. khamsat.com or remotive.com)
2. Search for entry level jobs
3. Select a specific job and click Apply / Add Offer
4. Register an account using generate_email if required
5. Write and submit the job proposal"""}
    ]
    try:
        llm = get_llm()
        response = await llm.ainvoke(prompt)
        plan = unwrap_llm_text(response.content)
        await send(ws, f"📋 **Execution Plan:**\n```\n{plan}\n```")
        return plan
    except Exception as e:
        plan = "1. Analyze page\n2. Execute task step by step\n3. Verify completion"
        await send(ws, f"⚠️ Planning skipped (will improvise): {str(e)[:100]}")
        return plan

# ── Screenshot helper ─────────────────────────────────────────────
async def take_screenshot(page, quality: int = 55) -> str | None:
    try:
        data = await page.screenshot(type="jpeg", quality=quality, full_page=False)
        return "data:image/jpeg;base64," + base64.b64encode(data).decode()
    except Exception:
        return None

# ── Sniper Screenshot (with element boxes) ────────────────────────
async def take_sniper_screenshot(page, ws: WebSocket, step: int) -> str | None:
    try:
        await page.evaluate("""() => {
            document.querySelectorAll('.__ab').forEach(b => b.remove());
            document.querySelectorAll('[data-agent-id]').forEach(el => {
                const r = el.getBoundingClientRect();
                if (!r.width || !r.height) return;
                const b = document.createElement('div');
                b.className = '__ab';
                b.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;border:1.5px solid #7c6af7;background:rgba(124,106,247,0.08);z-index:2147483646;pointer-events:none;box-sizing:border-box;`;
                const lbl = document.createElement('span');
                lbl.innerText = el.getAttribute('data-agent-id');
                lbl.style.cssText = 'background:#7c6af7;color:#fff;font-size:10px;font-weight:700;padding:1px 3px;border-radius:0 0 3px 0;position:absolute;top:0;left:0;line-height:1.2;';
                b.appendChild(lbl);
                document.body.appendChild(b);
            });
        }""")
        data = await page.screenshot(type="jpeg", quality=60)
        await page.evaluate("() => document.querySelectorAll('.__ab').forEach(b => b.remove())")
        b64 = "data:image/jpeg;base64," + base64.b64encode(data).decode()
        await send(ws, f"📸 **Sniper View** (Step {step})", image=b64)
        return b64
    except Exception:
        return None

# ── Smart Popup Auto-Dismisser ────────────────────────────────────
async def auto_dismiss_popups(page) -> bool:
    """Automatically dismiss common cookie/notification/promo popups
    WITHOUT waiting for the LLM. Returns True if any popup was dismissed."""
    try:
        dismissed = await page.evaluate("""() => {
            const DISMISS_KEYWORDS = [
                'ليس الآن', 'not now', 'رفض الكل', 'reject all', 'reject all cookies',
                'block', 'حظر', 'skip', 'تخطي', 'dismiss', 'لاحقاً',
                'no thanks', 'لا شكراً', 'close', 'إغلاق', 'got it', 'فهمت'
            ];
            // Only target dialogs that look like popups (no compose textarea inside)
            const dialogs = Array.from(document.querySelectorAll(
                '[role="dialog"], [role="alertdialog"]'
            ));
            for (let dialog of dialogs) {
                // Skip compose / create-post dialogs
                if (dialog.querySelector('textarea, [contenteditable="true"], [role="textbox"]')) continue;
                const dialogText = (dialog.innerText || '').toLowerCase();
                if (dialogText.includes('إنشاء منشور') || dialogText.includes('create post') ||
                    dialogText.includes('إعدادات المنشور') || dialogText.includes('نشر')) {
                    // This is the FB post dialog — don't auto-dismiss
                    continue;
                }
                // Find dismiss buttons
                const btns = Array.from(dialog.querySelectorAll('[role="button"], button, a'));
                for (let btn of btns) {
                    if (!btn.offsetParent) continue;
                    const txt = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase().trim();
                    if (DISMISS_KEYWORDS.some(k => txt === k || txt.startsWith(k))) {
                        btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                        btn.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true}));
                        btn.click();
                        return `auto-dismissed: "${btn.innerText}"`;
                    }
                }
            }
            return false;
        }""")
        if dismissed and dismissed is not False:
            return True
    except:
        pass
    return False

# ── Element interaction with fallback chain ────────────────────────
async def smart_click(page, el_id: str) -> bool:
    sel = f"[data-agent-id='{el_id}']"
    # Flash the element
    try:
        await page.evaluate(f"() => {{ const e = document.querySelector(\"{sel}\"); if(e) e.style.outline='2.5px solid #7c6af7'; }}")
    except: pass
    await asyncio.sleep(0.3)
    # Try 1: Playwright normal click
    try:
        await page.locator(sel).click(force=True, timeout=4000)
        await page.evaluate(f"() => {{ const e = document.querySelector(\"{sel}\"); if(e) e.style.outline=''; }}")
        return True
    except Exception: pass
    # Try 2: JS dispatch click
    try:
        result = await page.evaluate(f"""() => {{
            const e = document.querySelector("{sel}");
            if (!e) return false;
            e.dispatchEvent(new MouseEvent('mousedown', {{bubbles:true,cancelable:true,view:window}}) );
            e.dispatchEvent(new MouseEvent('mouseup', {{bubbles:true,cancelable:true,view:window}}) );
            e.dispatchEvent(new MouseEvent('click', {{bubbles:true,cancelable:true,view:window}}) );
            return true;
        }}""")
        if result: return True
    except Exception: pass
    # Try 3: scroll to element then click
    try:
        await page.evaluate(f"() => {{ const e = document.querySelector(\"{sel}\"); if(e) e.scrollIntoView({{block:'center'}}); }}")
        await asyncio.sleep(0.5)
        await page.locator(sel).click(force=True, timeout=3000)
        return True
    except Exception: pass
    # Try 4: click by bounding rect center
    try:
        rect = await page.evaluate(f"""() => {{
            const e = document.querySelector("{sel}");
            if (!e) return null;
            const r = e.getBoundingClientRect();
            return {{x: r.left + r.width/2, y: r.top + r.height/2}};
        }}""")
        if rect:
            await page.mouse.click(rect['x'], rect['y'])
            return True
    except Exception: pass
    return False

async def smart_fill(page, el_id: str, text: str, clear_first: bool = False) -> bool:
    sel = f"[data-agent-id='{el_id}']"
    try:
        await page.evaluate(f"() => {{ const e = document.querySelector(\"{sel}\"); if(e) e.style.outline='2.5px solid #f59e0b'; }}")
    except: pass

    # Detect if element is contenteditable (Facebook post box, Twitter, etc.)
    try:
        is_contenteditable = await page.evaluate(f"""() => {{
            const e = document.querySelector("{sel}");
            if (!e) return false;
            return e.isContentEditable || e.getAttribute('contenteditable') === 'true' || e.getAttribute('role') === 'textbox';
        }}""")
    except:
        is_contenteditable = False

    # ── Strategy A: contenteditable / rich text (Facebook, Twitter) ──
    if is_contenteditable:
        try:
            # Click with real mouse coords to focus
            rect = await page.evaluate(f"""() => {{
                const e = document.querySelector("{sel}");
                if (!e) return null;
                e.scrollIntoView({{block:'center'}});
                const r = e.getBoundingClientRect();
                return {{x: r.left + r.width/2, y: r.top + r.height/2}};
            }}""")
            if rect:
                await page.mouse.click(rect['x'], rect['y'])
            else:
                await page.locator(sel).click(force=True, timeout=3000)
            await asyncio.sleep(0.4)
            # Clear existing content
            if clear_first:
                await page.keyboard.press("Control+A")
                await asyncio.sleep(0.1)
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.1)
            # Type with real keyboard - works with React/FB
            await page.keyboard.type(text, delay=30)
            await asyncio.sleep(0.3)
            # Verify text was entered
            actual = await page.evaluate(f"""() => {{
                const e = document.querySelector("{sel}");
                return e ? (e.innerText || e.textContent || '').trim() : '';
            }}""")
            await page.evaluate(f"() => {{ const e = document.querySelector(\"{sel}\"); if(e) e.style.outline=''; }}")
            if actual and len(actual) > 0:
                return True
        except Exception: pass

    # ── Strategy B: Playwright locator.fill() for regular inputs ──
    try:
        if clear_first:
            try:
                await page.locator(sel).triple_click(timeout=2000)
                await asyncio.sleep(0.1)
            except: pass
        await page.locator(sel).fill(text, timeout=3000)
        await page.evaluate(f"() => {{ const e = document.querySelector(\"{sel}\"); if(e) e.style.outline=''; }}")
        return True
    except Exception: pass

    # ── Strategy C: Click + keyboard.type() ──
    try:
        rect = await page.evaluate(f"""() => {{
            const e = document.querySelector("{sel}");
            if (!e) return null;
            const r = e.getBoundingClientRect();
            return {{x: r.left + r.width/2, y: r.top + r.height/2}};
        }}""")
        if rect:
            await page.mouse.click(rect['x'], rect['y'])
        else:
            await page.locator(sel).click(force=True, timeout=3000)
        await asyncio.sleep(0.2)
        if clear_first:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        await page.keyboard.type(text, delay=20)
        await page.evaluate(f"() => {{ const e = document.querySelector(\"{sel}\"); if(e) e.style.outline=''; }}")
        return True
    except Exception: pass

    # ── Strategy D: JS direct innerText set (last resort) ──
    try:
        safe_text = json.dumps(text)
        await page.evaluate(f"""() => {{
            const e = document.querySelector("{sel}");
            if (!e) return;
            e.focus();
            if (e.isContentEditable || e.getAttribute('contenteditable') === 'true' || e.getAttribute('role') === 'textbox') {{
                // Use execCommand for contenteditable — triggers React state
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, {safe_text});
            }} else {{
                const nv = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value') ||
                           Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
                if (nv && nv.set) nv.set.call(e, {safe_text});
                e.dispatchEvent(new Event('input', {{bubbles:true}}));
                e.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        }}""")
        return True
    except: pass
    return False

# ═══════════════════════════════════════════════════════════════════
# MAIN AGENT LOOP
# ═══════════════════════════════════════════════════════════════════
async def run_agent(ws: WebSocket, task: str, api_mode: str, headless: bool, record_video: bool, agent_speed: int):
    global current_key_idx, consecutive_errors

    if api_mode != "auto":
        try: current_key_idx = int(api_mode)
        except: pass

    await send(ws, "🚀 **Agent V6 Starting** — Launching stealth browser...")

    vault = load_vault()
    vault_keys = list(vault.keys())

    # ── Phase 1: Planning ─────────────────────────────────────────
    plan = await generate_plan(task, vault_keys, ws)
    memory = f"PLAN CREATED. Starting execution. Task: {task}"

    # ── History with system prompt ────────────────────────────────
    def build_system():
        # Use manual replace instead of .format() to avoid KeyError from JSON braces in the prompt
        return (
            SYSTEM_PROMPT
            .replace("{task}", task)
            .replace("{memory}", memory)
            .replace("{vault_keys}", str(vault_keys))
            .replace("{plan}", plan)
        )

    chat_history = [{"role": "system", "content": build_system()}]

    # ── Loop tracking ─────────────────────────────────────────────
    action_counter   = 0
    step_counter     = 1
    last_actions     = deque(maxlen=6)   # for loop detection
    recorded_steps   = []
    extracted_data   = {}
    consecutive_errors = 0
    last_screenshot_hash = None

    try:
        async with async_playwright() as p:
            user_data_dir = os.path.join(os.getcwd(), "browser_data")
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir,
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--start-maximized'
                ],
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
                record_video_dir="videos" if record_video else None
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            
            # ── Multi-Tab: track all open pages ──
            all_pages = [page]  # list of all open tabs
            
            async def on_page_created(new_page):
                all_pages.append(new_page)
                try:
                    await new_page.wait_for_load_state('domcontentloaded', timeout=5000)
                except:
                    pass
                try:
                    title = await new_page.title()
                except:
                    title = ""
                url = new_page.url
                tab_idx = len(all_pages) - 1
                await send(ws, f"📱 **New tab #{tab_idx} opened:** {title or 'Loading...'} ({url[:50]}) — use switch_tab to navigate to it")
                # Inject into chat so agent knows
                chat_history.append({"role": "user", "content": f"SYSTEM: New browser tab #{tab_idx} just opened: title='{title}', url='{url}'. If this tab is needed for the task, use {{\"action\":\"switch_tab\",\"index\":{tab_idx}}} to switch to it."})
            
            ctx.on('page', on_page_created)

            # Stealth
            await page.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                window.chrome = {runtime:{}};
                Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
            """)
            # Reset page to blank so previous session (e.g. x.com) doesn't bias the new task
            try:
                await page.goto("about:blank")
            except: pass

            # ── Main agentic loop ─────────────────────────────────
            while True:
                try:
                    # Auto-dismiss popups before reading DOM (prevents popup text polluting state)
                    popup_dismissed = await auto_dismiss_popups(page)
                    if popup_dismissed:
                        await asyncio.sleep(0.5)  # let page settle
                    
                    # Extract DOM state
                    state        = await page.evaluate(DOM_JS)
                    elements     = state["elements"]
                    page_text    = state["page_text"]
                    current_url  = page.url

                    # Auto-screenshot every 8 steps for vision context
                    vision_b64 = None
                    if action_counter % 8 == 0 and action_counter > 0:
                        vision_b64 = await take_screenshot(page, quality=40)

                    # Build state message
                    state_msg = (
                        f"STEP {step_counter} | URL: {current_url}\n"
                        f"MEMORY: {memory}\n"
                        f"EXTRACTED DATA SO FAR: {json.dumps(extracted_data) if extracted_data else 'none'}\n\n"
                        f"VISIBLE TEXT (first 3000 chars):\n{page_text}\n\n"
                        f"INTERACTIVE ELEMENTS ({len(elements)} visible, showing first 60):\n"
                        f"{json.dumps(elements[:60], indent=2)}\n\n"
                        f"What is your next action? Return a JSON array."
                    )

                    # Vision message (if screenshot taken)
                    if vision_b64:
                        user_msg = [
                            {"type": "text", "text": state_msg},
                            {"type": "image_url", "image_url": {"url": vision_b64, "detail": "low"}}
                        ]
                    else:
                        user_msg = state_msg

                    chat_history.append({"role": "user", "content": user_msg})

                    # ── LLM call ──────────────────────────────────
                    try:
                        llm    = get_llm()
                        resp   = await llm.ainvoke(chat_history)
                        reply  = unwrap_llm_text(resp.content)
                        consecutive_errors = 0

                        # Compress history to save tokens (keep system + last 8 exchanges)
                        chat_history[-1] = {"role": "user", "content": f"[STEP {step_counter}] You were at: {current_url}"}
                        chat_history.append({"role": "assistant", "content": reply[:500]})
                        if len(chat_history) > 18:
                            chat_history = [chat_history[0]] + chat_history[-16:]

                    except Exception as e:
                        err = str(e).lower()
                        if any(x in err for x in ["429", "rate limit", "quota", "503"]):
                            consecutive_errors += 1
                            if api_mode == "auto" and len(API_CONFIGS) > 1:
                                current_key_idx = (current_key_idx + 1) % len(API_CONFIGS)
                                new_cfg = API_CONFIGS[current_key_idx]
                                provider = new_cfg.get("model", "unknown")
                                await send(ws, f"🔄 **Switched API** → `{provider}` (rate limit hit)")
                                chat_history.pop()
                                consecutive_errors = 0
                                continue
                            if consecutive_errors >= 3:
                                await send(ws, "⚠️ All APIs rate-limited. Please wait or switch API.", requires_input=True)
                                try:
                                    d = await recv(ws)
                                    if d.get("api_mode") not in [None, "auto", api_mode]:
                                        api_mode = d["api_mode"]
                                        try: current_key_idx = int(api_mode)
                                        except: pass
                                except: break
                                consecutive_errors = 0
                                chat_history.pop()
                                continue
                            await send(ws, f"⏳ Rate limit (attempt {consecutive_errors}/3) — waiting 30s...")
                            await asyncio.sleep(30)
                            chat_history.pop()
                            continue
                        else:
                            await send(ws, f"❌ **LLM Error:** {str(e)[:200]}")
                            chat_history.pop()
                            break

                    # ── Parse JSON (robust) ───────────────────────────
                    def extract_json(text: str):
                        """Try multiple strategies to extract a valid JSON array from LLM reply."""
                        # Strategy 1: strip ```json ... ``` or ``` ... ``` markdown fences
                        fenced = re.search(r'```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```', text, re.DOTALL)
                        if fenced:
                            candidate = fenced.group(1).strip()
                            try:
                                result = json.loads(candidate)
                                return [result] if isinstance(result, dict) else result
                            except: pass

                        # Strategy 2: find outermost [ ... ] array
                        arr = re.search(r'(\[[\s\S]*\])', text)
                        if arr:
                            try:
                                result = json.loads(arr.group(1))
                                return [result] if isinstance(result, dict) else result
                            except: pass

                        # Strategy 3: find outermost { ... } object
                        obj = re.search(r'(\{[\s\S]*\})', text)
                        if obj:
                            try:
                                result = json.loads(obj.group(1))
                                return [result] if isinstance(result, dict) else [result]
                            except: pass

                        # Strategy 4: extract multiple { } objects separately
                        objects = re.findall(r'\{[^{}]*\}', text)
                        parsed = []
                        for o in objects:
                            try:
                                parsed.append(json.loads(o))
                            except: pass
                        if parsed:
                            return parsed

                        return None

                    actions = extract_json(reply)

                    if actions is None:
                        # Send a strict reminder to the LLM with the raw (truncated) reply shown
                        await send(ws, f"⚠️ LLM returned plain text instead of JSON. Sending format reminder...")
                        # Inject a reminder into history so next call returns JSON
                        chat_history.append({
                            "role": "user",
                            "content": (
                                "CRITICAL: Your last response was NOT valid JSON.\n"
                                f"You said: {reply[:300]}\n\n"
                                "You MUST respond with ONLY a JSON array. No explanation, no markdown, no text before or after.\n"
                                "Example: [{\"action\": \"screenshot\"}]\n"
                                "What is your next action? Respond with JSON array only."
                            )
                        })
                        continue

                    # ── Loop detection & auto-recovery ──────────────
                    def make_action_sig(acts):
                        """Create a precise signature including action type + key params."""
                        sigs = []
                        for a in acts:
                            t = a.get("action", "")
                            if   "element_id" in a: p = str(a["element_id"])
                            elif "text"       in a: p = str(a["text"])[:30]
                            elif "url"        in a: p = str(a["url"])[:40]
                            elif "value"      in a: p = str(a["value"])[:20]
                            elif "label"      in a: p = str(a["label"])[:20]
                            else:                   p = ""
                            sigs.append(f"{t}:{p}")
                        return "|".join(sigs)

                    action_sig = make_action_sig(actions)
                    last_actions.append(action_sig)
                    if len(last_actions) >= 4 and len(set(list(last_actions)[-3:])) == 1:
                        await send(ws, f"🔄 **Loop Detected!** Same action repeated 3x: `{action_sig[:80]}`. Forcing strategy change...")
                        anti_loop_msg = (
                            f"CRITICAL LOOP: You repeated `{action_sig[:100]}` 3 times with no progress.\n"
                            f"Current URL: {current_url}\n"
                            f"You MUST switch strategy NOW. Options:\n"
                            f"- Try click_text with the exact visible text of the button\n"
                            f"- Try js_click on a DIFFERENT element ID\n"
                            f"- Use scroll to reveal hidden elements\n"
                            f"- Take a screenshot to reassess the situation\n"
                            f"- If clicking 'نشر'/'Post' keeps failing, try page.keyboard.press('Enter')\n"
                            f"Do NOT repeat the same action again!"
                        )
                        memory = f"LOOP DETECTED: {action_sig[:60]} — must change strategy"
                        chat_history[0]["content"] = build_system()
                        chat_history.append({"role": "user", "content": anti_loop_msg})
                        last_actions.clear()
                        continue

                    # ── Hard cap ──────────────────────────────────
                    if action_counter > 100:
                        await send(ws, "⚠️ **100 step limit reached.** Stopping to prevent runaway. Task may need to be broken into smaller pieces.", requires_input=True)
                        try:
                            user_d = await recv(ws)
                            chat_history.append({"role":"user","content": f"New instruction: {user_d.get('content','')}"})
                            action_counter = 0
                            continue
                        except: break

                    # ══════════════════════════════════════════════
                    # ACTION EXECUTOR
                    # ══════════════════════════════════════════════
                    should_break_inner = False

                    for action_data in actions:
                        if should_break_inner:
                            break
                        action = action_data.get("action", "")
                        step_label = f"[Step {step_counter}]"

                        # ── NAVIGATION ────────────────────────────
                        if action == "goto":
                            url = action_data.get("url", "")
                            await send(ws, f"{step_label} 🌐 Navigating → `{url}`")
                            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            await asyncio.sleep(1)
                            recorded_steps.append(f"page.goto('{url}')")

                        elif action == "new_tab":
                            url = action_data.get("url", "https://google.com")
                            await send(ws, f"{step_label} 🗂️ Opening new tab → `{url}`")
                            page = await ctx.new_page()
                            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                            recorded_steps.append(f"page = ctx.new_page(); page.goto('{url}')")

                        elif action == "go_back":
                            await page.go_back(wait_until="domcontentloaded", timeout=10000)
                            await send(ws, f"{step_label} ⬅️ Went back")

                        elif action == "go_forward":
                            await page.go_forward(wait_until="domcontentloaded", timeout=10000)
                            await send(ws, f"{step_label} ➡️ Went forward")

                        elif action == "reload":
                            await page.reload(wait_until="domcontentloaded", timeout=15000)
                            await send(ws, f"{step_label} 🔃 Reloaded page")

                        # ── INTERACTION ────────────────────────────
                        elif action == "click":
                            el_id = str(action_data.get("element_id", ""))
                            el_text = next((e["text"] for e in elements if e["id"] == el_id), "?")
                            await send(ws, f"{step_label} 🖱️ Clicking `{el_text}` (id:{el_id})")
                            ok = await smart_click(page, el_id)
                            if not ok:
                                await send(ws, f"⚠️ Click failed on id:{el_id} — trying scroll+retry")
                                await page.evaluate("window.scrollBy(0,300)")
                                await asyncio.sleep(0.8)
                                await smart_click(page, el_id)
                            recorded_steps.append(f"page.locator(\"[data-agent-id='{el_id}']\").click()")

                        elif action == "double_click":
                            el_id = str(action_data.get("element_id", ""))
                            await send(ws, f"{step_label} 🖱️🖱️ Double-clicking id:{el_id}")
                            sel = f"[data-agent-id='{el_id}']"
                            try:
                                await page.locator(sel).dblclick(timeout=4000)
                            except:
                                await page.evaluate(f"() => {{ const e=document.querySelector('{sel}'); if(e) {{ e.dispatchEvent(new MouseEvent('dblclick',{{bubbles:true}})); }} }}")

                        elif action == "right_click":
                            el_id = str(action_data.get("element_id", ""))
                            await send(ws, f"{step_label} 🖱️ Right-clicking id:{el_id}")
                            sel = f"[data-agent-id='{el_id}']"
                            try:
                                await page.locator(sel).click(button="right", timeout=4000)
                            except:
                                pass

                        elif action == "js_click":
                            el_id = str(action_data.get("element_id", ""))
                            await send(ws, f"{step_label} ⚡ JS force-click id:{el_id}")
                            await page.evaluate(f"""() => {{
                                const e = document.querySelector("[data-agent-id='{el_id}']");
                                if (e) {{
                                    e.dispatchEvent(new MouseEvent('mousedown', {{bubbles:true}}));
                                    e.dispatchEvent(new MouseEvent('mouseup', {{bubbles:true}}));
                                    e.click();
                                }}
                            }}""")

                        elif action == "hover":
                            el_id = str(action_data.get("element_id", ""))
                            await send(ws, f"{step_label} 🖱️ Hovering id:{el_id}")
                            sel = f"[data-agent-id='{el_id}']"
                            try:
                                await page.locator(sel).hover(timeout=4000)
                            except: pass

                        elif action == "type":
                            el_id = str(action_data.get("element_id", ""))
                            text  = action_data.get("text", "")
                            await send(ws, f"{step_label} ⌨️ Typing `{text[:30]}...` into id:{el_id}")
                            await smart_fill(page, el_id, text, clear_first=False)
                            recorded_steps.append(f"page.locator(\"[data-agent-id='{el_id}']\").fill('{text}')")

                        elif action == "clear_and_type":
                            el_id = str(action_data.get("element_id", ""))
                            text  = action_data.get("text", "")
                            await send(ws, f"{step_label} 🗑️✍️ Clearing & typing `{text[:30]}` into id:{el_id}")
                            await smart_fill(page, el_id, text, clear_first=True)

                        elif action == "press_key":
                            key = action_data.get("key", "Enter")
                            await send(ws, f"{step_label} ⌨️ Pressing `{key}`")
                            await page.keyboard.press(key)
                            recorded_steps.append(f"page.keyboard.press('{key}')")

                        elif action == "fb_type":
                            # Special action for typing into Facebook compose box
                            # Directly finds the contenteditable div in the modal — bypasses element ID issues
                            # Usage: {"action":"fb_type","text":"Hello world"}
                            text = action_data.get("text", "")
                            await send(ws, f"{step_label} ⌨️📘 FB-Type: `{text[:50]}...`")
                            try:
                                # Find the contenteditable div inside the compose dialog
                                compose_coords = await page.evaluate("""() => {
                                    // Try to find the compose textarea in the modal
                                    const selectors = [
                                        '[role="dialog"] [contenteditable="true"]',
                                        '[role="dialog"] [role="textbox"]',
                                        '[contenteditable="true"][data-lexical-editor]',
                                        '[contenteditable="true"]'
                                    ];
                                    for (let sel of selectors) {
                                        const els = Array.from(document.querySelectorAll(sel));
                                        for (let el of els) {
                                            const rect = el.getBoundingClientRect();
                                            if (rect.width > 50 && rect.height > 20 && el.offsetParent) {
                                                el.scrollIntoView({block:'center'});
                                                return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
                                            }
                                        }
                                    }
                                    return null;
                                }""")
                                if compose_coords:
                                    # Click to focus the compose area
                                    await page.mouse.click(compose_coords['x'], compose_coords['y'])
                                    await asyncio.sleep(0.4)
                                    # Clear any existing content
                                    await page.keyboard.press("Control+A")
                                    await asyncio.sleep(0.1)
                                    await page.keyboard.press("Backspace")
                                    await asyncio.sleep(0.1)
                                    # Type with real keyboard
                                    await page.keyboard.type(text, delay=25)
                                    await asyncio.sleep(0.5)
                                    # Verify text was entered
                                    typed_text = await page.evaluate("""() => {
                                        const el = document.querySelector('[role="dialog"] [contenteditable="true"], [contenteditable="true"][data-lexical-editor]');
                                        return el ? (el.innerText || el.textContent || '').trim() : '';
                                    }""")
                                    if typed_text and len(typed_text) > 0:
                                        await send(ws, f"✅ fb_type OK — typed {len(typed_text)} chars")
                                    else:
                                        # Fallback: execCommand
                                        await page.evaluate(f"""() => {{
                                            const el = document.querySelector('[role="dialog"] [contenteditable="true"]');
                                            if (el) {{ el.focus(); document.execCommand('insertText', false, {json.dumps(text)}); }}
                                        }}""")
                                        await send(ws, f"⚠️ fb_type used execCommand fallback")
                                else:
                                    await send(ws, f"⚠️ fb_type: compose box not found in modal")
                            except Exception as ex:
                                await send(ws, f"⚠️ fb_type error: {str(ex)[:100]}")

                        elif action == "switch_tab":
                            idx = int(action_data.get("index", action_data.get("tab_index", 0)))
                            if 0 <= idx < len(all_pages):
                                page = all_pages[idx]
                                try:
                                    await page.bring_to_front()
                                    await page.wait_for_load_state('domcontentloaded', timeout=5000)
                                except:
                                    pass
                                try:
                                    title = await page.title()
                                except:
                                    title = ""
                                current_url = page.url
                                await send(ws, f"{step_label} 📱 Switched to tab #{idx}: {title} ({current_url[:60]})")
                            else:
                                await send(ws, f"{step_label} ⚠️ Tab #{idx} doesn't exist. Available tabs: {list(range(len(all_pages)))}")

                        elif action == "close_tab":
                            idx = int(action_data.get("index", -1))
                            if idx > 0 and idx < len(all_pages):  # never close tab 0
                                await all_pages[idx].close()
                                all_pages.pop(idx)
                                page = all_pages[-1]  # switch to last remaining tab
                                await send(ws, f"{step_label} ❌ Closed tab #{idx}, now on tab #{len(all_pages)-1}")
                            else:
                                await send(ws, f"{step_label} ⚠️ Cannot close tab #{idx}")

                        elif action == "list_tabs":
                            tabs_info = []
                            for i, p in enumerate(all_pages):
                                try:
                                    t = await p.title()
                                    u = p.url
                                except:
                                    t, u = "closed", ""
                                tabs_info.append(f"Tab #{i}: {t[:40]} | {u[:50]}")
                            await send(ws, f"{step_label} 📱 Open tabs:\n" + "\n".join(tabs_info))
                            chat_history.append({"role": "user", "content": "Current open tabs:\n" + "\n".join(tabs_info)})

                        elif action == "click_xy":
                            x = action_data.get("x", 0)
                            y = action_data.get("y", 0)
                            await send(ws, f"{step_label} 🎯 Clicking at ({x}, {y})")
                            await page.mouse.click(float(x), float(y))
                            await asyncio.sleep(0.3)
                            recorded_steps.append(f"page.mouse.click({x}, {y})")

                        elif action == "wait_for_element":
                            selector = action_data.get("selector", "")
                            timeout = int(action_data.get("timeout", 10)) * 1000  # convert to ms
                            await send(ws, f"{step_label} ⏳ Waiting for `{selector}` (up to {timeout//1000}s)")
                            try:
                                await page.wait_for_selector(selector, timeout=timeout)
                                await send(ws, f"  ✅ Element appeared: `{selector}`")
                            except Exception:
                                await send(ws, f"  ⚠️ Timeout: `{selector}` did not appear")

                        elif action == "extract_table":
                            selector = action_data.get("selector", "table")
                            key = action_data.get("key", "table_data")
                            await send(ws, f"{step_label} 📋 Extracting table: `{selector}`")
                            table_data = await page.evaluate(f"""
                                () => {{
                                    const tbl = document.querySelector(\"{selector}\");
                                    if (!tbl) return null;
                                    const rows = Array.from(tbl.querySelectorAll('tr'));
                                    const headers = Array.from(rows[0]?.querySelectorAll('th,td') || []).map(c => c.innerText.trim());
                                    const data = rows.slice(1).map(row => {{
                                        const cells = Array.from(row.querySelectorAll('td,th')).map(c => c.innerText.trim());
                                        const obj = {{}};
                                        headers.forEach((h, i) => {{ if (h) obj[h] = cells[i] || ''; }});
                                        return obj;
                                    }}).filter(r => Object.values(r).some(v => v));
                                    return {{ headers, rows: data, count: data.length }};
                                }}
                            """)
                            if table_data:
                                extracted_data[key] = table_data
                                os.makedirs("extracted_data", exist_ok=True)
                                fname = f"extracted_data/{key}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                                with open(fname, "w", encoding="utf-8") as f:
                                    json.dump(table_data, f, ensure_ascii=False, indent=2)
                                await send(ws, f"  ✅ Extracted {table_data.get('count', 0)} rows → saved as `{key}`")
                            else:
                                await send(ws, f"  ⚠️ No table found at `{selector}`")

                        elif action == "compare_screenshot":
                            description = action_data.get("description", "What do you see?")
                            await send(ws, f"{step_label} 🔍 Vision check: `{description}`")
                            ss = await take_screenshot(page, quality=65)
                            if ss:
                                try:
                                    vision_llm = get_llm()
                                    vision_response = await vision_llm.ainvoke([
                                        {"role": "user", "content": [
                                            {"type": "image_url", "image_url": {"url": ss}},
                                            {"type": "text", "text": f"Look at this screenshot and answer: {description}\\nRespond with: YES or NO, then one short sentence explaining."}
                                        ]}
                                    ])
                                    vision_text = unwrap_llm_text(vision_response.content)
                                    await send(ws, f"  👁️ Vision: {vision_text[:200]}", image=ss)
                                    chat_history.append({"role": "user", "content": f"Vision check result for '{description}': {vision_text}"})
                                except Exception as ve:
                                    await send(ws, f"  ⚠️ Vision check failed: {str(ve)[:100]}")
                            else:
                                await send(ws, "  ⚠️ Could not take screenshot for vision check")

                        elif action == "select_option":
                            el_id = str(action_data.get("element_id", ""))
                            val_str = str(action_data.get("value", ""))
                            await send(ws, f"{step_label} 📋 Selecting `{val_str}` from id:{el_id}")
                            sel = f"[data-agent-id='{el_id}']"
                            success = False
                            try:
                                await page.locator(sel).select_option(value=val_str, timeout=3000)
                                success = True
                            except:
                                try:
                                    await page.locator(sel).select_option(label=val_str, timeout=3000)
                                    success = True
                                except: pass

                            if not success:
                                # Smart JS fallback searching option texts/values matching target (e.g. "20", "Egypt", "+20")
                                try:
                                    res = await page.evaluate(f"""(vStr) => {{
                                        const el = document.querySelector("{sel}");
                                        if (!el) return false;
                                        const target = vStr.toLowerCase().trim().replace('+', '');
                                        const options = Array.from(el.options || el.querySelectorAll('option'));
                                        for (let opt of options) {{
                                            const v = (opt.value || '').toLowerCase().replace('+', '');
                                            const t = (opt.text || opt.innerText || '').toLowerCase().replace('+', '');
                                            if (v === target || t.includes(target) || target.includes(v) || (target === '20' && (t.includes('egypt') || t.includes('20')))) {{
                                                el.value = opt.value;
                                                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                                                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                                                return true;
                                            }}
                                        }}
                                        return false;
                                    }}""", val_str)
                                    if res:
                                        success = True
                                except: pass

                            if not success:
                                # If it's a custom dropdown button, click it to open menu
                                await smart_click(page, el_id)
                                await asyncio.sleep(0.5)

                        elif action == "click_text":
                            text_target = str(action_data.get("text", ""))
                            await send(ws, f"{step_label} 🎯 Clicking text matching `{text_target}`")
                            await asyncio.sleep(0.5)  # wait for animations
                            try:
                                coords = await page.evaluate(f"""(targetText) => {{
                                    const search = targetText.toLowerCase().trim();
                                    
                                    function checkVisible(el) {{
                                        const rect = el.getBoundingClientRect();
                                        if (rect.width === 0 || rect.height === 0) return false;
                                        const style = window.getComputedStyle(el);
                                        if (style.opacity === '0' || style.visibility === 'hidden' || style.display === 'none') return false;
                                        return true;
                                    }}

                                    // Prioritize role=option / listbox items (Facebook dropdowns)
                                    const priority = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], [role="listitem"], li, option'));
                                    for (let el of priority) {{
                                        if (!checkVisible(el)) continue;
                                        const txt = (el.innerText || el.textContent || el.value || '').toLowerCase().trim();
                                        if (txt === search || txt.includes(search)) {{
                                            el.scrollIntoView({{block:'center'}});
                                            const r = el.getBoundingClientRect();
                                            return {{x: r.left + r.width/2, y: r.top + r.height/2}};
                                        }}
                                    }}
                                    
                                    // Fallback: any visible element
                                    const elements = Array.from(document.querySelectorAll('a, button, span, div, p, [role="button"]'));
                                    // Go in reverse to get innermost elements first
                                    for (let i = elements.length - 1; i >= 0; i--) {{
                                        const el = elements[i];
                                        if (!checkVisible(el)) continue;
                                        const txt = (el.innerText || '').toLowerCase().trim();
                                        if (txt === search || (txt.length < 80 && txt.includes(search))) {{
                                            el.scrollIntoView({{block:'center'}});
                                            const r = el.getBoundingClientRect();
                                            return {{x: r.left + r.width/2, y: r.top + r.height/2}};
                                        }}
                                    }}
                                    return null;
                                }}""", text_target)
                                
                                if coords and isinstance(coords, dict):
                                    await page.mouse.click(coords["x"], coords["y"])
                                else:
                                    # Playwright fallback with visible filter
                                    await page.locator(f"text={text_target}").locator("visible=true").first.click(force=True, timeout=4000)
                            except Exception as ex:
                                await send(ws, f"⚠️ click_text failed for `{text_target}`: {str(ex)[:80]}")

                        elif action == "fb_select":
                            # Special action for Facebook-style custom dropdowns (date, gender, country)
                            # Usage: {"action":"fb_select","label":"اليوم","value":"15"}
                            # or:   {"action":"fb_select","label":"الجنس","value":"ذكر"}
                            field_label = str(action_data.get("label", ""))
                            value       = str(action_data.get("value", ""))
                            await send(ws, f"{step_label} 🧩 FB-Select: `{field_label}` → `{value}`")
                            try:
                                done_fb = await page.evaluate("""(args) => {
                                    const label = args.label;
                                    const value = args.value;
                                    // Strategy 1: find <select> by nearby label text, set value
                                    const allSelects = Array.from(document.querySelectorAll('select'));
                                    for (let sel of allSelects) {
                                        // Check nearest label/placeholder
                                        const placeholder = sel.getAttribute('aria-label') || '';
                                        const parentText  = (sel.parentElement?.innerText || '').toLowerCase();
                                        if (parentText.includes(label.toLowerCase()) || placeholder.toLowerCase().includes(label.toLowerCase())) {
                                            const opts = Array.from(sel.options);
                                            const target = value.toLowerCase().trim();
                                            for (let opt of opts) {
                                                const v = (opt.value || '').toLowerCase();
                                                const t = (opt.text || '').toLowerCase();
                                                if (v === target || t === target || t.includes(target) || v === target) {
                                                    sel.value = opt.value;
                                                    sel.dispatchEvent(new Event('change', {bubbles:true}));
                                                    sel.dispatchEvent(new Event('input', {bubbles:true}));
                                                    return 'select:' + opt.text;
                                                }
                                            }
                                            // Try by index for numbers
                                            const num = parseInt(target);
                                            if (!isNaN(num)) {
                                                for (let opt of opts) {
                                                    if (opt.value == num || opt.text.trim() == String(num)) {
                                                        sel.value = opt.value;
                                                        sel.dispatchEvent(new Event('change', {bubbles:true}));
                                                        return 'select_num:' + opt.text;
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    // Strategy 2: click custom dropdown trigger, then click matching option
                                    const triggers = Array.from(document.querySelectorAll('[role="button"], button, [aria-haspopup="listbox"]'));
                                    for (let btn of triggers) {
                                        const txt = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase();
                                        if (txt.includes(label.toLowerCase()) || label.toLowerCase().includes(txt.replace(/\\s/g,''))) {
                                            btn.click();
                                            return 'opened:' + btn.innerText;
                                        }
                                    }
                                    return false;
                                }""", {"label": field_label, "value": value})

                                await asyncio.sleep(0.4)

                                if not done_fb or done_fb is False:
                                    # Fallback: open by clicking, then click_text
                                    await send(ws, f"⚠️ fb_select: couldn't find `{field_label}`, trying click_text `{value}`")
                                else:
                                    await send(ws, f"✅ fb_select result: {done_fb}")

                                # If a listbox opened, pick the value from it
                                if done_fb and done_fb.startswith("opened:"):
                                    await asyncio.sleep(0.5)
                                    clicked = await page.evaluate("""(args) => {
                                        const value = args.value;
                                        const items = Array.from(document.querySelectorAll('[role="option"], [role="listitem"], li, .bp4-menu-item'));
                                        const target = value.toLowerCase().trim();
                                        for (let item of items) {
                                            const txt = (item.innerText || item.textContent || '').toLowerCase().trim();
                                            if (txt === target || txt.includes(target)) {
                                                item.scrollIntoView({block:'center'});
                                                item.click();
                                                item.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                                                return true;
                                            }
                                        }
                                        return false;
                                    }""", {"value": value})
                                    if clicked:
                                        await send(ws, f"✅ fb_select picked `{value}` from listbox")
                            except Exception as ex:
                                await send(ws, f"⚠️ fb_select error: {str(ex)[:100]}")


                        elif action == "drag_drop":
                            from_id = str(action_data.get("from_id", ""))
                            to_id   = str(action_data.get("to_id", ""))
                            await send(ws, f"{step_label} 🤏 Drag from id:{from_id} → id:{to_id}")
                            try:
                                src = page.locator(f"[data-agent-id='{from_id}']")
                                tgt = page.locator(f"[data-agent-id='{to_id}']")
                                await src.drag_to(tgt, timeout=6000)
                            except: pass

                        elif action == "upload_file":
                            el_id = str(action_data.get("element_id", ""))
                            path  = action_data.get("path", "")
                            await send(ws, f"{step_label} 📁 Uploading file `{path}` to id:{el_id}")
                            try:
                                async with page.expect_file_chooser() as fc_info:
                                    await smart_click(page, el_id)
                                fc = await fc_info.value
                                await fc.set_files(path)
                            except: pass

                        # ── PAGE CONTROL ────────────────────────────
                        elif action == "scroll":
                            direction = action_data.get("direction", "down")
                            amount    = action_data.get("amount", 600)
                            dist      = amount if direction == "down" else -amount
                            await send(ws, f"{step_label} 📜 Scrolling {direction}")
                            await page.evaluate(f"window.scrollBy(0, {dist})")
                            await asyncio.sleep(0.4)

                        elif action == "scroll_to_element":
                            el_id = str(action_data.get("element_id", ""))
                            await send(ws, f"{step_label} 🎯 Scrolling to id:{el_id}")
                            await page.evaluate(f"() => {{ const e=document.querySelector(\"[data-agent-id='{el_id}']\"); if(e) e.scrollIntoView({{block:'center',behavior:'smooth'}}); }}")
                            await asyncio.sleep(0.6)

                        elif action == "wait":
                            sec = int(action_data.get("seconds", 2))
                            await send(ws, f"{step_label} ⏱️ Waiting {sec}s...")
                            await asyncio.sleep(sec)

                        elif action == "screenshot":
                            await send(ws, f"{step_label} 📸 Capturing browser state...")
                            ss = await take_sniper_screenshot(page, ws, step_counter)
                            if ss:
                                # Simple hash: compare first 500 chars of base64 as a quick fingerprint
                                ss_hash = ss[22:522]  # skip data:image/jpeg;base64, prefix
                                if ss_hash == last_screenshot_hash:
                                    await send(ws, "  ⏭️ Screenshot unchanged — skipping (page hasn't changed)")
                                else:
                                    last_screenshot_hash = ss_hash
                                    await send(ws, "  📸 Screenshot captured", image=ss)

                        # ── DATA & MEMORY ────────────────────────────
                        elif action == "extract_text":
                            selector = action_data.get("selector", "body")
                            key      = action_data.get("key", "text")
                            try:
                                txt = await page.locator(selector).first.inner_text(timeout=3000)
                                txt = txt.strip()[:1000]
                                extracted_data[key] = txt
                                chat_history.append({"role":"user","content": f"System: Extracted '{key}': {txt}"})
                                await send(ws, f"{step_label} 📊 Extracted `{key}`: `{txt[:100]}`")
                            except Exception as ex:
                                await send(ws, f"⚠️ extract_text failed: {str(ex)[:100]}")

                        elif action == "find_text_on_page":
                            search_text = action_data.get("text", "")
                            found = search_text.lower() in page_text.lower()
                            idx_pos = page_text.lower().find(search_text.lower())
                            snippet = page_text[max(0,idx_pos-30):idx_pos+80] if found else ""
                            msg = f"System: Text '{search_text}' {'FOUND: ' + snippet if found else 'NOT FOUND on page'}."
                            chat_history.append({"role":"user","content": msg})
                            await send(ws, f"{step_label} 🔍 {'✅ Found' if found else '❌ Not found'}: `{search_text}`")

                        elif action == "save_data":
                            key  = action_data.get("key", "result")
                            data = action_data.get("data", "")
                            extracted_data[key] = data
                            # Save to file
                            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                            fname = os.path.join("extracted_data", f"{key}_{ts}.json")
                            with open(fname, "w", encoding="utf-8") as f:
                                json.dump({key: data, "task": task, "url": current_url, "time": ts}, f, indent=2)
                            await send(ws, f"💾 **Data Saved:**\n```\n{json.dumps({key:data}, indent=2)[:500]}\n```")

                        elif action == "update_memory":
                            memory = action_data.get("text", memory)
                            chat_history[0]["content"] = build_system()
                            await send(ws, f"🧠 **Memory →** {memory}")

                        elif action == "save_account":
                            platform = action_data.get("platform", "unknown")
                            username = action_data.get("username", "")
                            password = action_data.get("password", "")
                            vault[platform] = {"username": username, "password": password}
                            vault_keys = list(vault.keys())
                            save_vault(vault)
                            chat_history[0]["content"] = build_system()
                            chat_history.append({"role":"user","content": f"System: Account for {platform} saved. Username: {username}"})
                            await send(ws, f"🔐 **Vault Updated** — `{platform}` → `{username}`")

                        elif action == "get_account":
                            platform = action_data.get("platform", "")
                            if platform in vault:
                                creds = vault[platform]
                                chat_history.append({"role":"user","content": f"System: Vault credentials for {platform}: {json.dumps(creds)}"})
                                await send(ws, f"🔓 **Vault →** Found `{platform}`: `{creds['username']}`")
                            else:
                                # fuzzy match
                                close = [k for k in vault if platform.lower() in k.lower() or k.lower() in platform.lower()]
                                if close:
                                    creds = vault[close[0]]
                                    chat_history.append({"role":"user","content": f"System: Fuzzy match: credentials for '{close[0]}': {json.dumps(creds)}"})
                                    await send(ws, f"🔓 **Vault →** Fuzzy match found `{close[0]}`")
                                else:
                                    chat_history.append({"role":"user","content": f"System: No account for '{platform}' in vault. Vault has: {vault_keys}"})
                                    await send(ws, f"⚠️ No account for `{platform}` in vault. Need to create one.")

                        # ── EMAIL ────────────────────────────────────
                        elif action == "generate_email":
                            try:
                                resp = await ctx.request.get("https://www.1secmail.com/api/v1/?action=genRandomMailbox&count=1")
                                emails = await resp.json()
                                new_email = emails[0] if emails else "fallback@1secmail.com"
                            except:
                                import random, string
                                rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
                                new_email = f"{rand}@1secmail.com"
                            extracted_data["generated_email"] = new_email
                            chat_history.append({"role":"user","content": f"System: Temp email generated: {new_email}"})
                            await send(ws, f"📧 **Temp Email Created:** `{new_email}`")

                        elif action == "check_inbox":
                            email = action_data.get("email", extracted_data.get("generated_email", ""))
                            if "@" not in email:
                                await send(ws, "⚠️ No valid email to check.")
                            else:
                                login, domain = email.split("@")
                                url_msgs = f"https://www.1secmail.com/api/v1/?action=getMessages&login={login}&domain={domain}"
                                msgs = []
                                try:
                                    r = await ctx.request.get(url_msgs)
                                    msgs = await r.json()
                                except: pass
                                if msgs:
                                    mid = msgs[0]["id"]
                                    url_msg = f"https://www.1secmail.com/api/v1/?action=readMessage&login={login}&domain={domain}&id={mid}"
                                    try:
                                        r2   = await ctx.request.get(url_msg)
                                        body = await r2.json()
                                        text = body.get("textBody", body.get("body",""))[:1000]
                                        chat_history.append({"role":"user","content": f"System: Email from {body.get('from')} | Subject: {body.get('subject')} | Body: {text}"})
                                        extracted_data["last_email_body"] = text
                                        await send(ws, f"📬 **Email Received!**\n**From:** {body.get('from')}\n**Subject:** {body.get('subject')}\n```\n{text[:300]}\n```")
                                    except: pass
                                else:
                                    chat_history.append({"role":"user","content": f"System: Inbox for {email} is empty."})
                                    await send(ws, f"📭 Inbox empty for `{email}`")

                        elif action == "wait_for_email":
                            email   = action_data.get("email", extracted_data.get("generated_email",""))
                            timeout = int(action_data.get("timeout_seconds", 60))
                            if "@" not in email:
                                await send(ws, "⚠️ No email to poll.")
                            else:
                                login, domain = email.split("@")
                                url_msgs = f"https://www.1secmail.com/api/v1/?action=getMessages&login={login}&domain={domain}"
                                found_msg = None
                                for attempt in range(1, timeout // 5 + 1):
                                    await send(ws, f"⏳ Checking inbox... (attempt {attempt}/{timeout//5})")
                                    try:
                                        r    = await ctx.request.get(url_msgs)
                                        msgs = await r.json()
                                        if msgs:
                                            mid  = msgs[0]["id"]
                                            url2 = f"https://www.1secmail.com/api/v1/?action=readMessage&login={login}&domain={domain}&id={mid}"
                                            r2   = await ctx.request.get(url2)
                                            body = await r2.json()
                                            found_msg = body
                                            break
                                    except: pass
                                    await asyncio.sleep(5)

                                if found_msg:
                                    text = found_msg.get("textBody", found_msg.get("body",""))[:1000]
                                    chat_history.append({"role":"user","content": f"System: Email arrived! From: {found_msg.get('from')} | Subject: {found_msg.get('subject')} | Body: {text}"})
                                    extracted_data["last_email_body"] = text
                                    await send(ws, f"📬 **Email arrived after {attempt*5}s!**\n**Subject:** {found_msg.get('subject')}\n```\n{text[:300]}\n```")
                                else:
                                    chat_history.append({"role":"user","content": f"System: No email arrived in {timeout}s. The inbox is still empty."})
                                    await send(ws, f"📭 No email received in {timeout}s for `{email}`")

                        # ── CONTROL ──────────────────────────────────
                        elif action == "ask_user":
                            question = action_data.get("question", "What should I do next?")
                            await send(ws, f"❓ **Agent needs input:**\n\n{question}", requires_input=True)
                            try:
                                user_d = await recv(ws)
                                user_reply = user_d.get("content", "")
                                chat_history.append({
                                    "role": "user",
                                    "content": f"USER INSTRUCTION: {user_reply}\n\nIMPORTANT: Execute this instruction immediately by outputting ONLY a JSON array of actions."
                                })
                                memory = f"User instruction received: {user_reply}"
                                chat_history[0]["content"] = build_system()
                                should_break_inner = True
                            except: break

                        elif action == "replan":
                            reason = action_data.get("reason", "Unexpected situation")
                            await send(ws, f"🔄 **Replanning** — Reason: {reason}")
                            new_plan = await generate_plan(f"{task}\n\nCurrent situation: {memory}", vault_keys, ws)
                            plan = new_plan
                            chat_history[0]["content"] = build_system()
                            should_break_inner = True

                        elif action == "human_pause":
                            # Pause automation and wait for human to take over (e.g. CAPTCHA, 2FA, manual click)
                            message = action_data.get("message", "يرجى التدخل اليدوي ثم اضغط Continue")
                            await send(ws, f"🙋 **Human Pause Required:**\n\n{message}\n\n*Press Continue when ready...*", requires_input=True)
                            try:
                                user_d = await recv(ws)
                                user_note = user_d.get("content", "ok")
                                chat_history.append({
                                    "role": "user",
                                    "content": f"System: Human completed manual action. User note: '{user_note}'. Page is ready — continue from where you left off."
                                })
                                memory = f"Human intervention done: {user_note}"
                                chat_history[0]["content"] = build_system()
                                await asyncio.sleep(1)
                                should_break_inner = True
                            except: break

                        elif action == "fill_form":
                            # Fill multiple form fields in one action
                            # Usage: {"action":"fill_form","fields":{"name":"Rio","email":"test@test.com"}}
                            fields = action_data.get("fields", {})
                            await send(ws, f"📝 **Filling form** — {len(fields)} field(s)")
                            state_fresh = await page.evaluate(DOM_JS)
                            elements_fresh = state_fresh["elements"]
                            for field_name, field_value in fields.items():
                                # Match field by label text or placeholder
                                matched_id = None
                                name_lower = field_name.lower()
                                for el in elements_fresh:
                                    txt = (el.get("text","") or el.get("placeholder","") or "").lower()
                                    if name_lower in txt or txt in name_lower:
                                        matched_id = el["id"]
                                        break
                                if matched_id:
                                    ok = await smart_fill(page, matched_id, str(field_value), clear_first=True)
                                    status = "✅" if ok else "⚠️"
                                    await send(ws, f"  {status} `{field_name}` → `{str(field_value)[:40]}`")
                                else:
                                    await send(ws, f"  ⚠️ Could not find field: `{field_name}`")
                                await asyncio.sleep(0.3)

                        elif action == "done":
                            msg = action_data.get("message", "Task complete")

                            # Verification check: Check if tweet/post is blocked by character limit or if publish button is still visible
                            try:
                                is_unfinished_post = await page.evaluate("""() => {
                                    const text = document.body ? document.body.innerText : '';
                                    const isOverflow = text.includes('Upgrade to Premium') || /\\-\\d+/.test(text);
                                    const hasTwitterModal = !!document.querySelector('[data-testid="tweetButton"], [data-testid="tweetButtonInline"], [data-testid="tweetTextarea_0"]');
                                    
                                    // Check for Facebook "التالي" or "نشر" buttons or "ليس الآن" in a dialog
                                    const buttons = Array.from(document.querySelectorAll('div[role="button"], button'));
                                    const hasFBPublish = buttons.some(b => {
                                        const txt = (b.innerText || '').toLowerCase().trim();
                                        return (txt === 'نشر' || txt === 'التالي' || txt === 'post' || txt === 'next' || txt === 'ليس الآن' || txt === 'not now') && b.offsetParent !== null;
                                    });
                                    const hasDialog = !!document.querySelector('div[role="dialog"]');
                                    
                                    if (isOverflow && hasTwitterModal) return 'twitter_limit';
                                    if (hasDialog && hasFBPublish) return 'fb_unfinished';
                                    return false;
                                }""")
                                
                                if is_unfinished_post == 'twitter_limit':
                                    await send(ws, "⚠️ **Post Error!** Text exceeds X's 280-character limit (-31 red text). Re-trying with shorter tweet...")
                                    chat_history.append({
                                        "role": "user",
                                        "content": "CRITICAL ERROR: Cannot finish! Your tweet exceeds X's 280-character limit (showing negative counter / red text / Upgrade to Premium error). YOU MUST: 1) use 'clear_and_type' to write a short tweet under 150 characters, 2) click the 'Post' button, 3) verify the modal closes."
                                    })
                                    should_break_inner = True
                                    continue
                                elif is_unfinished_post == 'fb_unfinished':
                                    await send(ws, "⚠️ **Done Rejected!** A 'Publish', 'Next' or a Popup ('نشر' / 'التالي' / 'ليس الآن') button is still visible! Forcing agent to continue...")
                                    chat_history.append({
                                        "role": "user",
                                        "content": "CRITICAL REJECTION: You cannot call 'done' yet! A 'نشر' (Post), 'التالي' (Next), or 'ليس الآن' (Not now) button is still visible in a dialog. If this is a WhatsApp or Boost popup, click 'ليس الآن'. You MUST keep going until the post is completely published to the timeline and all dialogs are gone!"
                                    })
                                    should_break_inner = True
                                    continue
                            except: pass

                            # Check 2: Reject premature done on open-ended goal tasks (e.g. job search, earning money, creating account)
                            is_open_ended = any(w in task.lower() for w in ["شغل", "دولار", "وظايف", "ربح", "نت", "job", "earn", "money", "freelance", "work", "/goal"])
                            if is_open_ended and action_counter < 10:
                                await send(ws, f"⚠️ **Done Rejected!** Long-running goal task requires thorough execution. The agent only took {action_counter} steps. Auto-continuing...")
                                chat_history.append({
                                    "role": "user",
                                    "content": (
                                        f"CRITICAL REJECTION: You CANNOT call 'done' yet! The user asked: '{task}'.\n"
                                        f"This is a long-running goal. You have only taken {action_counter} steps! "
                                        f"You MUST be thorough. If this is a job search, you MUST: 1) Visit actual platforms (e.g. khamsat.com, mostaql.com, remotive.com), "
                                        f"2) Browse multiple job listings, 3) Extract jobs using 'save_data', 4) Register an account using 'generate_email' if needed. "
                                        f"DO NOT call 'done' until you have completed at least 10 meaningful steps and fully achieved the goal!"
                                    )
                                })
                                should_break_inner = True
                                continue

                            # Generate automation script
                            script = ("from playwright.sync_api import sync_playwright\n\n"
                                      "def run():\n"
                                      "    with sync_playwright() as p:\n"
                                      "        b = p.chromium.launch(headless=False)\n"
                                      "        ctx = b.new_context()\n"
                                      "        page = ctx.new_page()\n")
                            for s in recorded_steps:
                                script += f"        {s}\n"
                            script += "        b.close()\n\nif __name__ == '__main__':\n    run()"

                            # Save extracted data summary
                            summary = f"✅ **{msg}**"
                            if extracted_data:
                                summary += f"\n\n📊 **Extracted Data:**\n```json\n{json.dumps(extracted_data, indent=2, ensure_ascii=False)[:800]}\n```"
                            summary += f"\n\n💻 **Automation Script:**\n```python\n{script}\n```"

                            await send(ws, summary, requires_input=True)

                            try:
                                user_d = await recv(ws)
                                next_task = user_d.get("content", "")
                                if next_task:
                                    plan  = await generate_plan(next_task, list(vault.keys()), ws)
                                    memory = f"New task started: {next_task}"
                                    chat_history[0]["content"] = build_system()
                                    action_counter = 0
                            except: pass
                            should_break_inner = True

                        else:
                            await send(ws, f"⚠️ Unknown action: `{action}` — skipping")

                        step_counter     += 1
                        action_counter   += 1

                    # ── Context Window Manager: compress every 15 steps ──
                    if action_counter > 0 and action_counter % 15 == 0 and len(chat_history) > 20:
                        try:
                            # Build a summary of old messages
                            old_messages = chat_history[1:-8]  # keep system + last 8
                            if old_messages:
                                summary_content = "\n".join([
                                    f"[{m['role']}]: {str(m.get('content',''))[:200]}"
                                    for m in old_messages
                                ])
                                compress_prompt = [
                                    {"role": "system", "content": "Summarize the following agent conversation history into a concise bullet list (max 10 bullets). Focus on: what was accomplished, what failed, current state, key data found."},
                                    {"role": "user", "content": summary_content[:4000]}
                                ]
                                compress_llm = get_llm()
                                compress_resp = await compress_llm.ainvoke(compress_prompt)
                                summary = unwrap_llm_text(compress_resp.content)
                                
                                # Replace old messages with summary
                                new_history = [
                                    chat_history[0],  # system prompt
                                    {"role": "user", "content": f"[COMPRESSED HISTORY - Steps 1 to {action_counter-8}]:\n{summary}"},
                                    *chat_history[-8:]  # keep last 8 messages
                                ]
                                chat_history.clear()
                                chat_history.extend(new_history)
                                await send(ws, f"📦 **Context compressed** ({len(old_messages)} msgs → summary) to save tokens")
                        except Exception as ce:
                            pass  # If compression fails, just continue with full history

                    # Auto-checkpoint screenshot every 7 steps
                    if action_counter > 0 and action_counter % 7 == 0:
                        ss = await take_screenshot(page, quality=50)
                        if ss:
                            await send(ws, f"📸 Checkpoint (step {action_counter})", image=ss)

                    await asyncio.sleep(agent_speed)

                except Exception as action_err:
                    err_str = str(action_err)[:400]
                    await send(ws, f"❌ **Action Error:** {err_str}")
                    if "Target page" in err_str or "TargetClosedError" in err_str:
                        await send(ws, "Browser was closed. Stopping.")
                        break
                    # Try to recover: scroll and continue
                    try:
                        await page.evaluate("window.scrollBy(0, 300)")
                    except: pass
                    chat_history.append({"role":"user","content": f"System: Error occurred: {err_str}. Recover and continue."})
                    await asyncio.sleep(2)
                    continue

            await ctx.close()

    except Exception as fatal:
        err = str(fatal)
        if "TargetClosedError" in err or "Target page" in err:
            await send(ws, "⚠️ Browser was closed externally. Please restart the agent.")
        else:
            await send(ws, f"💥 **Fatal Error:** {err[:300]}")

# ── WebSocket endpoint ────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """Persistent session: one connection handles multiple tasks.
    After each task ends (success, error, or user stop), the UI stays
    connected and the user can send another task without refreshing."""
    await websocket.accept()
    try:
        while True:
            # Wait for the next task message from the user
            try:
                raw  = await websocket.receive_text()
                data = json.loads(raw)
            except (WebSocketDisconnect, Exception):
                break

            # Frontend sends {type:'stop'} to abort current task \u2014 handled inside run_agent
            if data.get("type") == "stop":
                # run_agent checks for this; if we get it here the task already ended
                continue

            task = data.get("content", "").strip()
            if not task:
                continue

            # Run the agent for this task
            try:
                await run_agent(
                    ws           = websocket,
                    task         = task,
                    api_mode     = data.get("api_mode", "auto"),
                    headless     = data.get("headless", False),
                    record_video = data.get("record_video", False),
                    agent_speed  = int(data.get("agent_speed", 2))
                )
            except WebSocketDisconnect:
                break
            except Exception as e:
                try:
                    await websocket.send_text(json.dumps({
                        "type": "bot",
                        "content": f"\ud83d\udca5 **Session Error:** {str(e)[:300]}\n\nReady for a new task!"
                    }))
                except:
                    break

            # After task ends \u2014 unlock the UI and await the next command
            try:
                await websocket.send_text(json.dumps({
                    "type": "bot",
                    "content": "\u2705 Task finished. Type a new task to continue \u2014 no refresh needed!",
                    "requires_input": False
                }))
            except:
                break

    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
