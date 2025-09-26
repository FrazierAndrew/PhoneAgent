# Voice Agent Demo (FastAPI + Twilio + ElevenLabs)

This is a bare-bones Python FastAPI app that exposes a Twilio webhook so you can call a phone number and converse with an AI agent. For now, the agent does not remember anything; it simply echoes/transforms caller speech using an LLM and speaks back using TTS.

We will iterate later to collect structured data (patient info, insurance, address validation, scheduling). This version just proves the end-to-end calling and agent speaking loop.

## Stack
- FastAPI for web server
- Twilio Programmable Voice for telephony
- Deepgram (optional) or Twilio STT via `<Gather input="speech">`
- ElevenLabs for TTS
- OpenAI (or compatible) for LLM text responses

## Prerequisites
- Python 3.10+
- Twilio account and a purchased phone number (`https://www.twilio.com/console`)
- ElevenLabs API key (`https://elevenlabs.io`)
- OpenAI API key (or compatible)
- ngrok (or Cloudflare Tunnel) to expose localhost

## Environment Variables
Create a `.env` file in the project root:

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_VOICE_WEBHOOK_SECRET=12345
ELEVENLABS_API_KEY=your_elevenlabs_api_key
OPENAI_API_KEY=your_openai_key
BASE_URL=https://your-public-ngrok-url
PORT=8000
```

- `BASE_URL` must be your public URL (from ngrok), no trailing slash.
- `TWILIO_VOICE_WEBHOOK_SECRET` is used to validate a simple query param to avoid random hits.

## Install
```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Expose with ngrok in another terminal:
```
ngrok http 8000
```

Set your Twilio number Voice webhook to:
- When a call comes in: `POST {BASE_URL}/voice/incoming?secret=TWILIO_VOICE_WEBHOOK_SECRET`
- Fallback URL (optional): `POST {BASE_URL}/voice/fallback?secret=TWILIO_VOICE_WEBHOOK_SECRET`

## What this demo does
- Answers an incoming call
- Prompts the caller to speak
- Uses Twilio speech-to-text via `<Gather input="speech">`
- Sends the transcript to an LLM to generate a short response
- Uses ElevenLabs to synthesize the response to an MP3
- Plays the audio back to the caller
- Repeats for a couple of turns (configurable)

## Notes
- This is intentionally simple (stateless between turns). Later we can collect and validate fields.
- If you prefer Deepgram STT, we can switch to Twilio Media Streams + Deepgram in a later iteration.
- If you prefer Cartesia for TTS, we can swap providers quickly.
