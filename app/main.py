import os
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, FileResponse
from pydantic_settings import BaseSettings
from twilio.twiml.voice_response import VoiceResponse, Gather
import httpx

MEDIA_DIR = Path(__file__).parent.parent / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv()

conversation_state = {}

QUESTIONS = [
    {"key": "name", "prompt": "Hi Mr Beazely, this is yosefina. I love Thailand and wanna learn how to love you. Whats your first name"},
    {"key": "date_of_birth", "prompt": "Thank you {name}! What's your date of birth?"},
    {"key": "phone", "prompt": "Great! What's your phone number?"},
    {"key": "email", "prompt": "And what's your email address?"},
    {"key": "reason", "prompt": "Finally, what's the reason for your call today?"},
]


class Settings(BaseSettings):
    twilio_account_sid: Optional[str] = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_auth_token: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_voice_webhook_secret: str = os.getenv("TWILIO_VOICE_WEBHOOK_SECRET", "dev")
    elevenlabs_api_key: Optional[str] = os.getenv("ELEVENLABS_API_KEY")
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000")
    max_turns: int = int(os.getenv("MAX_TURNS", "3"))


settings = Settings()
app = FastAPI()


def get_conversation_state(call_sid: str) -> dict:
    if call_sid not in conversation_state:
        conversation_state[call_sid] = {
            "current_question": 0,
            "responses": {},
            "completed": False,
            "created_at": datetime.utcnow().isoformat()
        }
    return conversation_state[call_sid]


def save_response(call_sid: str, question_key: str, response: str):
    state = get_conversation_state(call_sid)
    state["responses"][question_key] = response
    state["current_question"] += 1
    
    if state["current_question"] >= len(QUESTIONS):
        state["completed"] = True
    
    state["updated_at"] = datetime.utcnow().isoformat()



def get_next_question(call_sid: str) -> tuple[str, bool]:
    state = get_conversation_state(call_sid)
    
    if state["completed"]:
        responses = state["responses"]
        summary_parts = []
        for question in QUESTIONS:
            key = question["key"]
            value = responses.get(key, "N/A")
            readable_key = key.replace("_", " ").title()
            summary_parts.append(f"{readable_key}: {value}")
        
        summary = f"Perfect! I have all your information: {', '.join(summary_parts)}. Thank you!"
        return summary, True
    
    question_idx = state["current_question"]
    if question_idx < len(QUESTIONS):
        question = QUESTIONS[question_idx]
        prompt = question["prompt"]
        
        if "{name}" in prompt and "name" in state["responses"]:
            prompt = prompt.format(name=state["responses"]["name"])
            
        return prompt, False
    
    return "Thank you for calling!", True


@app.get("/healthz")
async def health() -> dict:
    return {"ok": True}


@app.get("/conversation-data")
async def get_conversation_data():
    return {
        "total_conversations": len(conversation_state),
        "conversations": conversation_state
    }


@app.get("/conversation-data/{call_sid}")
async def get_caller_data(call_sid: str):
    if call_sid in conversation_state:
        return conversation_state[call_sid]
    else:
        raise HTTPException(status_code=404, detail="Caller not found")


@app.post("/voice/incoming", response_class=PlainTextResponse)
async def voice_incoming(request: Request, secret: str = Query(default="")):
    if secret != settings.twilio_voice_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    form = await request.form()
    call_sid = form.get("CallSid", "unknown")
    
    first_question, is_complete = get_next_question(call_sid)
    audio_path = await synthesize_tts(first_question)
    
    vr = VoiceResponse()
    
    if audio_path:
        media_url = f"{settings.base_url}/media/{audio_path.name}"
        vr.play(media_url)
    else:
        vr.say(first_question)
    
    gather_url = f"{settings.base_url}/voice/handle_gather?secret={secret}"
    gather = Gather(
        input="speech", 
        action=gather_url, 
        method="POST", 
        timeout=15,
        speech_timeout="auto",
        enhanced=True
    )
    vr.append(gather)
    
    vr.say("I'm having trouble hearing you. Please try again or call back later.")
    
    return PlainTextResponse(str(vr))


@app.post("/voice/handle_gather", response_class=PlainTextResponse)
async def voice_handle_gather(request: Request, secret: str = Query(default="")):
    if secret != settings.twilio_voice_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    form = await request.form()
    user_utterance = form.get("SpeechResult") or form.get("TranscriptionText") or ""
    call_sid = form.get("CallSid", "unknown")
    
    state = get_conversation_state(call_sid)
    current_question_idx = state["current_question"]
    
    if current_question_idx < len(QUESTIONS):
        question_key = QUESTIONS[current_question_idx]["key"]
        save_response(call_sid, question_key, user_utterance)
    
    next_prompt, is_complete = get_next_question(call_sid)
    audio_path = await synthesize_tts(next_prompt)
    
    vr = VoiceResponse()
    
    if audio_path:
        media_url = f"{settings.base_url}/media/{audio_path.name}"
        vr.play(media_url)
    else:
        vr.say(next_prompt)
    
    if not is_complete:
        gather = Gather(
            input="speech", 
            action=f"{settings.base_url}/voice/handle_gather?secret={secret}", 
            method="POST", 
            timeout=15,
            speech_timeout="auto",
            enhanced=True
        )
        vr.append(gather)
        
        vr.say("I didn't catch that. Let me ask again.")
        
        current_question, _ = get_next_question(call_sid)
        fallback_audio = await synthesize_tts(current_question)
        if fallback_audio:
            fallback_url = f"{settings.base_url}/media/{fallback_audio.name}"
            vr.play(fallback_url)
        else:
            vr.say(current_question)
        
        
        final_gather = Gather(
            input="speech", 
            action=f"{settings.base_url}/voice/handle_gather?secret={secret}", 
            method="POST", 
            timeout=10,
            speech_timeout="auto",
            enhanced=True
        )
        vr.append(final_gather)
    
    vr.say("Thank you for calling. Have a great day!")
    return PlainTextResponse(str(vr))


@app.get("/media/{filename}")
async def serve_media(filename: str):
    file_path = MEDIA_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(file_path), media_type="audio/mpeg")


async def generate_reply(user_text: str) -> str:
    prompt = (
        "You are a concise, friendly medical office intake assistant. "
        "A caller said: '" + (user_text or "") + "'. "
        "Respond in one or two short sentences."
    )
    api_key = settings.openai_api_key
    if not api_key or api_key == "your_openai_api_key_here":
        return "Thanks. I heard you. How else can I help?"

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "You are helpful."}, {"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=80,
        )
        return resp.choices[0].message.content or "Understood."
    except Exception:
        return "Understood."


async def synthesize_tts(text: str) -> Optional[Path]:
    if not text:
        return None
        
    api_key = settings.elevenlabs_api_key
    if not api_key or api_key == "your_elevenlabs_api_key_here":
        return None

    voice_id = "WLjZnm4PkNmYtNCyiCq8"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    filename = f"tts_{uuid.uuid4().hex}.mp3"
    out_path = MEDIA_DIR / filename

    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.7},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            
            with open(out_path, "wb") as f:
                f.write(resp.content)
            
        return out_path
    except Exception:
        return None


@app.post("/voice/fallback", response_class=PlainTextResponse)
async def voice_fallback(request: Request, secret: str = Query(default="")):
    if secret != settings.twilio_voice_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    vr = VoiceResponse()
    vr.say("Sorry, the service is temporarily unavailable. Please try again later.")
    return PlainTextResponse(str(vr))