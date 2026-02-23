import json
import asyncio
import time
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

PORT = 8110

app = FastAPI()

# --- 手动编写测试用例区域 ---
MOCK_DATA = {
    "reasoning": "首先，我需要分析用户的问题。这是一个关于 $E=mc^2$ 的物理学计算请求。\n接下来，我将调用计算工具来验证数值的准确性。\n最后，我将以友好的方式告知用户结果。",
    "content": "这是一份关于质能方程的详细回复。根据计算，您的参数输入是正确的。请查看下方的工具输出。 ",
    "tool_call": {
        "id": "call_tester_001",
        "name": "calculate_physics",
        "arguments": '{"formula": "E=mc^2", "m": 1.0, "c": 299792458}'
    }
}

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict[str, str]]
    stream: Optional[bool] = True

def create_chunk(field: str, value: Any, finish_reason: Optional[str] = None):
    """构造 OpenAI 兼容的消息分片"""
    return {
        "id": "chatcmpl-mock-123",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock-tester-v1",
        "choices": [{
            "index": 0,
            "delta": {field: value} if value is not None else {},
            "finish_reason": finish_reason
        }]
    }

async def stream_generator():
    chunk_size = 5  # 模拟每片发送 5 个字符

    # 1. 模拟思维链 (reasoning_content)
    reasoning = MOCK_DATA["reasoning"]
    for i in range(0, len(reasoning), chunk_size):
        chunk = reasoning[i:i + chunk_size]
        yield f"data: {json.dumps(create_chunk('reasoning_content', chunk))}\n\n"
        await asyncio.sleep(0.1)

    # 2. 模拟正文内容 (content)
    content = MOCK_DATA["content"]
    for i in range(0, len(content), chunk_size):
        chunk = content[i:i + chunk_size]
        yield f"data: {json.dumps(create_chunk('content', chunk))}\n\n"
        await asyncio.sleep(0.1)

    # 3. 模拟工具调用 (tool_calls)
    tool = MOCK_DATA["tool_call"]
    
    # 3a. 发送工具 ID 和 函数名
    tool_init_delta = {
        "tool_calls": [{
            "index": 0,
            "id": tool["id"],
            "type": "function",
            "function": {"name": tool["name"], "arguments": ""}
        }]
    }
    yield f"data: {json.dumps(create_chunk(None, None) | {'choices': [{'index': 0, 'delta': tool_init_delta, 'finish_reason': None}]})}\n\n"

    # 3b. 模拟参数流式输出
    args = tool["arguments"]
    for i in range(0, len(args), chunk_size):
        arg_chunk = args[i:i + chunk_size]
        arg_delta = {
            "tool_calls": [{
                "index": 0,
                "function": {"arguments": arg_chunk}
            }]
        }
        # 注意：合并 delta 时需要保持结构
        payload = create_chunk(None, None)
        payload["choices"][0]["delta"] = arg_delta
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(0.05)

    # 4. 结束
    yield f"data: {json.dumps(create_chunk(None, None, finish_reason='tool_calls'))}\n\n"
    yield "data: [DONE]\n\n"

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    return StreamingResponse(stream_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)