"""语音服务（#37）：ASR 语音转文字 + TTS 文字合成语音。

- ASR：dashscope qwen-omni-turbo 多模态（本地音频 → 逐字转写），实测准确
- TTS：dashscope CosyVoice（cosyvoice-v1），同步合成完整 mp3 bytes
- 供应商 key 复用 dashscope（与 EMBEDDING_API_KEY 同源）
- TTS 结果带 LRU 内存缓存：同一文本不重复合成
"""

import os
import tempfile
from functools import lru_cache

import dashscope
from dashscope import MultiModalConversation
from dashscope.audio.tts_v2 import SpeechSynthesizer

from ..config import settings

# dashscope SDK 用同一个阿里云 key（与 embedding 同供应商）
_dashscope_key = settings.embedding_api_key

_ASR_PROMPT = "把这段语音逐字转写成文字，只输出转写结果，不要添加任何解释。"
_TTS_MODEL = "cosyvoice-v1"
_TTS_VOICE = "longxiaochun"
_ASR_MODEL = "qwen-omni-turbo"


def transcribe(audio_bytes: bytes, ext: str = "mp3") -> str:
    """语音 → 文字（#37 语音输入）。失败抛 RuntimeError，由路由转 503。"""
    dashscope.api_key = _dashscope_key
    # qwen-omni 走本地文件：写临时文件交给 SDK（自动 base64）
    fd, path = tempfile.mkstemp(suffix=f".{ext}")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)
        resp = MultiModalConversation.call(
            model=_ASR_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [{"audio": path}, {"text": _ASR_PROMPT}],
                }
            ],
            stream=False,
        )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if resp.status_code != 200:
        raise RuntimeError(f"ASR failed: {resp.code} {resp.message}")
    content = resp.output.choices[0].message.content
    text = content[0].get("text", "") if isinstance(content, list) else str(content)
    return text.strip()


@lru_cache(maxsize=64)
def _synthesize_cached(text: str) -> bytes:
    """TTS 合成（LRU 缓存：同一文本不重复合成，省额度省延迟）。"""
    dashscope.api_key = _dashscope_key
    synth = SpeechSynthesizer(model=_TTS_MODEL, voice=_TTS_VOICE)
    audio = synth.call(text)
    if not audio:
        raise RuntimeError("TTS returned empty audio")
    return audio


def synthesize(text: str) -> bytes:
    """文字 → mp3 语音（#37 语音输出）。失败抛 RuntimeError，由路由转 503。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    return _synthesize_cached(text)
