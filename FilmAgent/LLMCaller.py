import os
import openai
from openai import OpenAI


# ============================================================
# ★ API Key 配置（DeepSeek 平台 https://platform.deepseek.com 注册申请）
# DeepSeek 的接口是 OpenAI 兼容格式，所以用 openai 库 + base_url 指过去即可。
base_url = "https://api.deepseek.com"


def _load_api_key():
    # 密钥存在本地文件 api_key.txt 里（该文件已被 .gitignore 忽略，不会上传到 git，避免泄露）
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    # 读不到就用占位符（会导致认证失败，提醒你还没配置密钥）
    return "sk-在这里填你的DeepSeek密钥"


api_key = _load_api_key()
# ============================================================

def GPTCall(prompt):
    counter = 0
    result = "api调用失败"
    while counter < 3:
        try:
            client = OpenAI(api_key = api_key, base_url = base_url)
            completion = client.chat.completions.create(
                model = "deepseek-chat",
                # model = "deepseek-reasoner",  # 推理模型，本项目不适合（慢且贵）
                messages=[
                    {"role": "user", "content": prompt},
                ]
            )
            result = completion.choices[0].message.content
            print(f"%%%%%%%%%%%%%%%%%%%%%%%%\n{result}\n%%%%%%%%%%%%%%%%%%%%%%%")
            break

        except Exception as e:
            print(e)
            counter += 1

    return result


def GPTTTS(text, role):
    # 注意：DeepSeek 不提供语音合成（TTS）服务。
    # 本项目 GenerateAudio.py 默认用 ChatTTS（Flag = "ChatTTS"），不会调用本函数。
    # 如果确实要用 OpenAI 的 TTS，请把上面的 api_key / base_url 换成 OpenAI 的，
    # 否则本函数会报错。
    client = OpenAI(api_key = api_key, base_url = base_url)
    response = client.audio.speech.create(
        model = "tts-1",
        voice = role,
        input = text,
        response_format = "mp3"
    )

    return response
