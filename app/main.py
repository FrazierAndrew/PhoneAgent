import os
import uuid
import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, FileResponse
from pydantic_settings import BaseSettings
from twilio.twiml.voice_response import VoiceResponse, Gather, Play, Say
import httpx

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MEDIA_DIR = Path(__file__).parent.parent / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv()


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


@app.get("/healthz")
async def health() -> dict:
    return {"ok": True}


@app.post("/voice/incoming", response_class=PlainTextResponse)
async def voice_incoming(request: Request, secret: str = Query(default="")):
    if secret != settings.twilio_voice_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Generate first prompt with Australian accent
    first_prompt = "How's it going im natasha, I need to collect some medical info from you, are you available to answer some questions???"
    audio_path = await synthesize_tts(first_prompt)
    
    vr = VoiceResponse()
    
    # Play the Australian-accented greeting
    if audio_path:
        media_url = f"{settings.base_url}/media/{audio_path.name}"
        vr.play(media_url)
    else:
        vr.say(first_prompt)  # Fallback to Twilio TTS
    
    # Then gather speech input
    gather = Gather(
        input="speech", 
        action=f"{settings.base_url}/voice/handle_gather?secret={secret}", 
        method="POST", 
        timeout=10,
        speech_timeout="auto",
        enhanced=True
    )
    vr.append(gather)
    
    # Fallback if no response
    vr.say("I'm having trouble hearing you. Goodbye.")
    return PlainTextResponse(str(vr))



@app.post("/voice/handle_gather", response_class=PlainTextResponse)
async def voice_handle_gather(request: Request, secret: str = Query(default="")):
    if secret != settings.twilio_voice_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    form = await request.form()
    user_utterance = form.get("SpeechResult") or form.get("TranscriptionText") or ""
    
    # Debug logging
    logger.info(f"Received speech input: '{user_utterance}'")
    logger.info(f"Form data: {dict(form)}")
    
    # Basic state via Twilio cookies is possible; we keep it stateless and short.

    # Get LLM response
    reply_text = await generate_reply(user_utterance)

    # TTS via ElevenLabs
    audio_path = await synthesize_tts(reply_text)
    logger.info("Made it here")
    # Build TwiML to play audio and prompt next turn
    vr = VoiceResponse()
    if audio_path:
        media_url = f"{settings.base_url}/media/{audio_path.name}"
        vr.play(media_url)
    else:
        vr.say(reply_text or "Thanks for calling.")

    # Loop for a few turns
    gather = Gather(
        input="speech", 
        action=f"{settings.base_url}/voice/handle_gather?secret={secret}", 
        method="POST", 
        timeout=10,
        speech_timeout="auto",
        enhanced=True
    )
    gather.say("You can speak again, or say goodbye to end the call.")
    vr.append(gather)
    
    # Fallback if no response
    vr.say("Thank you for calling. Goodbye.")
    return PlainTextResponse(str(vr))


@app.get("/media/{filename}")
async def serve_media(filename: str):
    file_path = MEDIA_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(file_path), media_type="audio/mpeg")


async def generate_reply(user_text: str) -> str:
    # Minimal guardrail
    prompt = (
        "You are a concise, friendly medical office intake assistant. "
        "A caller said: '" + (user_text or "") + "'. "
        "Respond in one or two short sentences."
    )
    api_key = settings.openai_api_key
    if not api_key:
        # Fallback without LLM
        return "Thanks. I heard you. How else can I help?"

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    try:
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
    if not api_key:
        return None

    voice_id = "DLsHlh26Ugcm6ELvS0qi"  # ElevenLabs "Charlie" - Australian accent
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

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(resp.content)
    return out_path


@app.post("/voice/fallback", response_class=PlainTextResponse)
async def voice_fallback(request: Request, secret: str = Query(default="")):
    if secret != settings.twilio_voice_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    vr = VoiceResponse()
    vr.say("Sorry, the service is temporarily unavailable. Please try again later.")
    return PlainTextResponse(str(vr))
