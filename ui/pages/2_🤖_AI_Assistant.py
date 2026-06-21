"""🤖 AI Assistant — smart conversational module with text + voice.

Talk in Hindi / English / Hinglish. With a Groq key it understands any phrasing
and answers accounting/business questions via RAG. With a Sarvam key the 🎙️ mic
appears: speak, and it transcribes, thinks, and speaks the reply back.
"""
from __future__ import annotations

import base64
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import _shared as ui  # noqa: E402
import streamlit as st  # noqa: E402

st.set_page_config(page_title="AI Assistant — Dukanbook", page_icon="🤖", layout="centered")
ui.inject_theme()
ui.header("🤖 AI Assistant", "Hindi · English · Hinglish — boliye ya likhiye")

if ui.backend_down_banner():
    st.stop()

status = ui.health()
if status.get("llm"):
    st.caption("🟢 Smart mode on (Groq)" + ("  ·  🎙️ Voice on (Sarvam)" if status.get("voice")
                                            else "  ·  🎙️ Voice off"))
else:
    st.warning("⚠️ Basic mode: koi LLM key nahi mili — AI simple parser se chal raha hai. "
               "Poori intelligence ke liye `.env` mein `GROQ_API_KEY=...` daaliye.")

if "messages" not in st.session_state:
    st.session_state.messages = []


def render(m: dict) -> None:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("audio"):
            st.audio(base64.b64decode(m["audio"]), format="audio/mp3")


for m in st.session_state.messages:
    render(m)

# --- Voice input (only if Sarvam key configured) ---
if status.get("voice"):
    audio = st.audio_input("🎙️ Boliye (Hindi / English / Hinglish)")
    if audio is not None:
        sig = hash(audio.getvalue())
        if st.session_state.get("last_audio_sig") != sig:
            st.session_state.last_audio_sig = sig
            with st.spinner("Sun raha hoon…"):
                try:
                    data = ui.voice_chat(audio.getvalue())
                    st.session_state.messages.append(
                        {"role": "user", "content": "🎙️ " + (data.get("transcript") or "…")})
                    st.session_state.messages.append(
                        {"role": "assistant", "content": data.get("reply", ""),
                         "audio": data.get("audio_b64")})
                except Exception as e:
                    st.session_state.messages.append(
                        {"role": "assistant", "content": f"Voice error: {e}"})
            st.rerun()

# --- Text input ---
prompt = st.chat_input("Likhiye… (e.g. 'Ramesh ko 500 udhaar likho')")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    try:
        reply = ui.chat(prompt).get("reply", "(no reply)")
    except Exception as e:
        reply = f"Backend se baat nahi ho paayi: {e}"
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.rerun()
