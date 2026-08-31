import os
import openai
from openai import OpenAI


# ============================================================
# ★ 在这里填你的 API Key（DeepSeek 平台 https://platform.deepseek.com 注册申请）
# DeepSeek 的接口是 OpenAI 兼容格式，所以用 openai 库 + base_url 指过去即可。
api_key = "sk-把你的DeepSeek密钥填在这里"
base_url = "https://api.deepseek.com"
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
