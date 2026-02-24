import json
import re
import time
import uvicorn
import httpx
import logging
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from openai import AsyncOpenAI
import os

PORT = 8111

# 目标配置（从 model_mapping.json 加载）
DEFAULT_TARGET_HOST = None  # 将从配置文件加载
DEFAULT_TARGET_BASE_URL = None  # 将从配置文件加载

# 拦截非法工具调用配置
ENABLE_TOOL_CALL_INTERCEPTION = True  # 是否启用拦截功能
# 检测的非法标记列表（仅检查特定的工具调用格式）
ILLEGAL_TOOL_CALL_MARKERS = [
    "<|tool_calls_section_begin|>",  
    "<|tool_call_begin|>"
]
MAX_BUFFER_SIZE = 128  # 用于跨 chunk 检测的缓冲区最大大小

# 不可或缺的 native tools_call 配置
REQUIRE_NATIVE_TOOL_CALL = False  # 当启用时，请求的回答必须包含 tool call，否则返回异常

# 日志配置（从配置文件加载）
ENABLE_REQUEST_LOGGING = True
REQUEST_LOG_DIR = "request_logs"
ENABLE_RESPONSE_LOGGING = True
RESPONSE_LOG_DIR = "response_logs"


# 自定义异常：用于标记拦截行为
class InterceptionError(Exception):
    pass


def save_request_log(body: any, path: str):
    """
    后台任务：保存请求日志到JSON文件
    - body: 请求体内容（可以是 dict 或 bytes）
    - path: 请求路径
    """
    if not ENABLE_REQUEST_LOGGING:
        return
    
    try:
        # 确保日志目录存在
        log_dir = os.path.join(os.path.dirname(__file__), REQUEST_LOG_DIR)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # 生成时间戳文件名（包含微秒以确保唯一性）
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        microseconds = str(time.time()).split('.')[-1][:6]  # 获取微秒部分
        filename = f"{timestamp}_{microseconds}_{path.replace('/', '_').replace(':', '_')}.json"
        filepath = os.path.join(log_dir, filename)
        
        # 准备要记录的日志数据
        log_data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "path": path,
            "request_body": body
        }
        
        # 将请求体写入文件
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        print(f"[Request Log] Saved to {filepath}")
    except Exception as e:
        print(f"[Request Log Error] Failed to save request log: {e}")


def save_response_log(response_data: any, path: str, is_stream: bool = False):
    """
    后台任务：保存响应日志到JSON文件
    - response_data: 响应内容（可以是 dict 或 list）
    - path: 请求路径
    - is_stream: 是否为流式响应
    """
    if not ENABLE_RESPONSE_LOGGING:
        return
    
    try:
        # 确保日志目录存在
        log_dir = os.path.join(os.path.dirname(__file__), RESPONSE_LOG_DIR)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # 生成时间戳文件名（包含微秒以确保唯一性）
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        microseconds = str(time.time()).split('.')[-1][:6]  # 获取微秒部分
        stream_suffix = "_stream" if is_stream else ""
        filename = f"{timestamp}_{microseconds}_{path.replace('/', '_').replace(':', '_')}{stream_suffix}.json"
        filepath = os.path.join(log_dir, filename)
        
        # 准备要记录的日志数据
        log_data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "path": path,
            "is_stream": is_stream,
            "response_body": response_data
        }
        
        # 将响应写入文件
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        print(f"[Response Log] Saved to {filepath}")
    except Exception as e:
        print(f"[Response Log Error] Failed to save response log: {e}")


def setup_logging():
    """配置日志过滤器，抑制 InterceptionError 的 traceback 输出"""
    class InterceptionFilter(logging.Filter):
        def filter(self, record):
            # 如果日志包含异常信息且异常类型是 InterceptionError，则过滤掉
            if record.exc_info and issubclass(record.exc_info[0], InterceptionError):
                return False
            return True
    
    # 给 uvicorn 的 error 日志添加过滤器
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_error_logger.addFilter(InterceptionFilter())

app = FastAPI()


# 注册异常处理器
@app.exception_handler(InterceptionError)
async def interception_exception_handler(request: Request, exc: InterceptionError):
    """拦截异常处理器，不打印 traceback"""
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc)}
    )


# 用于流式响应的累积缓冲区（处理跨 chunk 检测）
class StreamBuffer:
    def __init__(self, max_size=MAX_BUFFER_SIZE):
        self._buffer = ""
        self._max_size = max_size
    
    def append(self, content: str) -> str:
        """追加内容并返回完整缓冲区"""
        if not content:
            return self._buffer
        self._buffer += content
        # 限制缓冲区大小
        if len(self._buffer) > self._max_size:
            self._buffer = self._buffer[-self._max_size:]
        return self._buffer
    
    def check_markers(self, markers: list) -> tuple:
        """
        检查缓冲区中是否包含任意非法标记
        返回: (是否发现, 发现的标记)
        """
        for marker in markers:
            if marker in self._buffer:
                return True, marker
        return False, None
    
    def check_content(self, content: str, markers: list) -> tuple:
        """
        检查内容中是否包含任意非法标记
        返回: (是否发现, 发现的标记)
        """
        for marker in markers:
            if marker in content:
                return True, marker
        return False, None
    
    def get_buffer_tail(self, length: int = 50) -> str:
        """获取缓冲区最后 N 个字符用于调试"""
        return self._buffer[-length:] if len(self._buffer) > length else self._buffer


# 代码块过滤器：用于检测和处理 Markdown 代码块
class CodeBlockFilter:
    """
    跟踪文本中的代码块状态，过滤掉代码块内的内容
    支持：```多行代码块``` 和 `行内代码`
    
    原理：
    - 连续3个反引号 ``` 触发多行代码块状态切换
    - 单个反引号 ` 触发行内代码状态切换（仅在不在多行代码块中时有效）
    - 所有处于代码块内的内容都会被过滤掉，不返回用于检测
    - 反引号字符本身也不会被包含在过滤后的内容中
    """
    def __init__(self):
        self._in_multiline = False  # 是否在 ``` 多行代码块中
        self._in_inline = False     # 是否在 ` 行内代码中
        self._backtick_buf = ""     # 反引号缓冲区，用于处理连续的反引号
    
    def _flush_backticks(self) -> str:
        """
        刷新反引号缓冲区，根据缓冲区内反引号数量决定如何处理
        返回：需要输出到过滤后内容的字符（如果有）
        """
        if not self._backtick_buf:
            return ""
        
        count = len(self._backtick_buf)
        self._backtick_buf = ""
        
        # 如果在多行代码块内
        if self._in_multiline:
            if count >= 3:
                # 关闭多行代码块（如果正好是3个）
                if count == 3:
                    self._in_multiline = False
                # 多余的反引号需要处理
                remaining = count - 3
                if remaining == 1 and not self._in_inline:
                    # 剩余1个，打开行内代码
                    self._in_inline = True
                    return ""  # 反引号是分隔符，不输出
                elif remaining >= 3:
                    # 剩余3个或以上，切换多行代码块状态奇数次
                    toggles = remaining // 3
                    self._in_multiline = (self._in_multiline if toggles % 2 == 0 else not self._in_multiline)
                    leftover = remaining % 3
                    if leftover == 1 and not self._in_multiline and not self._in_inline:
                        self._in_inline = True
                    return ""
                return ""
            else:
                # 少于3个反引号，不关闭多行代码块
                return ""
        
        # 如果不在多行代码块内
        else:
            if count >= 3:
                # 触发多行代码块状态切换
                toggles = count // 3
                self._in_multiline = (toggles % 2 == 1)  # 奇数次切换则进入多行代码块
                remaining = count % 3
                
                # 处理剩余的反引号
                if remaining == 1:
                    if self._in_multiline:
                        # 刚打开多行代码块，又遇到单反引号 - 忽略或错误处理
                        return ""
                    else:
                        # 打开行内代码
                        self._in_inline = True
                        return ""
                return ""
            elif count == 1:
                # 单个反引号，切换行内代码状态
                self._in_inline = not self._in_inline
                return ""
            elif count == 2:
                # 两个反引号：连续开关行内代码（回到原状态）
                # 或者视为空行内代码 ``，状态不改变
                return ""
        
        return ""
    
    def filter_content(self, content: str) -> str:
        """
        过滤掉代码块内的内容，返回非代码块的内容
        只有不在任何代码块内的文本才会被返回用于检查
        """
        result = []
        
        for char in content:
            if char == '`':
                # 将反引号加入缓冲区
                self._backtick_buf += char
            else:
                # 遇到非反引号字符，先刷新缓冲区
                self._flush_backticks()
                
                # 如果当前不在任何代码块内，则输出该字符
                if not self._in_multiline and not self._in_inline:
                    result.append(char)
                # 如果在代码块内，不输出该字符（被过滤）
        
        # 流式处理中，保留反引号缓冲区中的内容到下一个chunk
        # 因为反引号序列可能被拆分到不同chunk中
        
        return "".join(result)
    
    def finalize(self) -> str:
        """
        结束过滤，处理剩余的缓冲区内容
        通常在流结束时调用，但作为代理可以依赖状态重置
        """
        # 处理剩余的缓冲区内容
        if self._backtick_buf:
            # 如果还有未处理的反引号，清除它们
            # 这可能表示输入不完整的代码块，但为了安全起见，清除它们
            self._flush_backticks()
        return ""
    
    def is_in_code_block(self) -> bool:
        """检查当前是否处于代码块中（多行或行内）"""
        return self._in_multiline or self._in_inline
    
    def get_state(self) -> dict:
        """获取当前状态（用于调试）"""
        return {
            "in_multiline": self._in_multiline,
            "in_inline": self._in_inline,
            "backtick_buf": self._backtick_buf
        }


# 导入状态码相关
from fastapi import HTTPException

# 加载模型映射配置
def load_config():
    """
    加载配置文件，返回配置元组
    返回: (model_mapping, default_target_host, logging_config)
    logging_config: dict with keys: enable_request_logging, enable_response_logging,
                    request_log_dir, response_log_dir
    """
    config_path = os.path.join(os.path.dirname(__file__), "model_mapping.json")
    
    # 默认日志配置
    default_logging = {
        "enable_request_logging": True,
        "enable_response_logging": True,
        "request_log_dir": "request_logs",
        "response_log_dir": "response_logs"
    }
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # 支持新格式（包含 default_target_host）和旧格式（直接是模型映射）
        if isinstance(config, dict):
            if "model_mapping" in config:
                # 新格式
                model_mapping = config.get("model_mapping", {})
                default_host = config.get("default_target_host", "api.openai.com")
                logging_config = config.get("logging", default_logging)
            else:
                # 旧格式（直接是模型映射）
                model_mapping = config
                default_host = "api.openai.com"
                logging_config = default_logging
            
            # 合并默认日志配置（确保所有字段都存在）
            for key, value in default_logging.items():
                if key not in logging_config:
                    logging_config[key] = value
            
            return model_mapping, default_host, logging_config
        else:
            return {}, "api.openai.com", default_logging
    except FileNotFoundError:
        print(f"[Warning] model_mapping.json not found at {config_path}")
        return {}, "api.openai.com", default_logging
    except json.JSONDecodeError as e:
        print(f"[Warning] Failed to parse model_mapping.json: {e}")
        return {}, "api.openai.com", default_logging

# 加载配置并设置全局变量
MODEL_MAPPING, DEFAULT_TARGET_HOST, LOGGING_CONFIG = load_config()
DEFAULT_TARGET_BASE_URL = f"https://{DEFAULT_TARGET_HOST}"

# 设置日志配置全局变量
ENABLE_REQUEST_LOGGING = LOGGING_CONFIG.get("enable_request_logging", True)
REQUEST_LOG_DIR = LOGGING_CONFIG.get("request_log_dir", "request_logs")
ENABLE_RESPONSE_LOGGING = LOGGING_CONFIG.get("enable_response_logging", True)
RESPONSE_LOG_DIR = LOGGING_CONFIG.get("response_log_dir", "response_logs")

# 创建客户端缓存字典
client_cache = {}

def get_or_create_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """根据 base_url 和 api_key 获取或创建客户端"""
    cache_key = f"{base_url}:{api_key[:10]}..."
    if cache_key not in client_cache:
        client_cache[cache_key] = AsyncOpenAI(base_url=f"{base_url}/v1", api_key=api_key)
    return client_cache[cache_key]

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all_proxy(request: Request, path: str, background_tasks: BackgroundTasks):
    if "chat/completions" in path and request.method == "POST":
        return await handle_chat_completions(request, path, background_tasks)
    return await handle_transparent_proxy(request, path, f"{DEFAULT_TARGET_BASE_URL}/{path}", background_tasks)

async def handle_chat_completions(request: Request, path: str, background_tasks: BackgroundTasks):
    # 获取原始请求
    body = await request.json()
    
    # 后台记录请求日志
    background_tasks.add_task(save_request_log, body, path)
    
    # 提取原始模型名
    original_model = body.get("model", "")
    
    # 检查是否有映射配置
    mapping = MODEL_MAPPING.get(original_model)
    
    if mapping:
        # 有映射：修改请求体中的模型名和使用映射的URL/Key
        body["model"] = mapping["model"]
        target_url = mapping["url"]
        target_key = mapping["key"]
        target_model = mapping["model"]
        is_mapped = True
    else:
        # 无映射：透传请求，使用请求自带的API Key（如果提供）或默认值
        target_url = DEFAULT_TARGET_BASE_URL
        auth_header = request.headers.get("Authorization", "")
        target_key = auth_header.replace("Bearer ", "") if auth_header else "placeholder"
        target_model = original_model
        is_mapped = False
    
    # 提取用户消息内容（最后一条 user 消息的 content）
    messages = body.get("messages", [])
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            if isinstance(user_message, list):
                # 处理多模态消息格式
                user_message = " ".join([str(item.get("text", "")) for item in user_message if isinstance(item, dict)])
            break
    
    # 打印格式化请求日志
    print(f"\n[Request] Model: {original_model} -> [{target_url}] -> {target_model}")
    print(f"          Content: {user_message[:32]}...")
    
    # 获取对应的客户端
    client = get_or_create_client(target_url, target_key)

    # 发起请求
    response = await client.chat.completions.create(
        **body,
        extra_headers={"Authorization": f"Bearer {target_key}"}
    )

    async def stream_generator():
        # 初始化缓冲区用于跨 chunk 检测非法标记
        reasoning_buf = StreamBuffer()
        content_buf = StreamBuffer()
        
        # 初始化代码块过滤器（用于思维链和正文）
        reasoning_filter = CodeBlockFilter()
        content_filter = CodeBlockFilter()
        
        # 追踪是否看到过 tool call（用于 REQUIRE_NATIVE_TOOL_CALL 检查）
        has_seen_tool_call = False
        # condense 标签豁免检测状态
        has_seen_condense_open = False
        has_seen_condense_close = False
        
        # 收集所有原始响应用于保存日志
        raw_chunks = []
        
        # condense 标签检测函数
        def check_condense_tags(text: str) -> None:
            nonlocal has_seen_condense_open, has_seen_condense_close
            if not has_seen_condense_open and "<condense>" in text:
                has_seen_condense_open = True
            if not has_seen_condense_close and "</condense>" in text:
                has_seen_condense_close = True
        
        def check_interception(content: str, buffer: StreamBuffer) -> tuple:
            """
            双重检查：当前内容或累积缓冲区中是否包含任意非法标记
            返回: (是否拦截, 发现的标记)
            """
            if not ENABLE_TOOL_CALL_INTERCEPTION:
                return False, None
            # 检查当前内容
            found, marker = buffer.check_content(content, ILLEGAL_TOOL_CALL_MARKERS)
            if found:
                return True, marker
            # 检查累积缓冲区
            found, marker = buffer.check_markers(ILLEGAL_TOOL_CALL_MARKERS)
            if found:
                return True, marker
            return False, None
        
        # 如果是非流式请求处理
        if not hasattr(response, "__aiter__"):
            response_dict = response.model_dump()
            full_res = json.dumps(response_dict)
            print(f"\n[Response]: {full_res[:200]}...")
            
            # 拦截检查：非流式响应（仅检查 reasoning_content 和 content，排除代码块和 tool_calls）
            if ENABLE_TOOL_CALL_INTERCEPTION:
                intercept_found = False
                intercept_marker = None
                
                # 获取 message 对象
                message = response.choices[0].message if response.choices and len(response.choices) > 0 else None
                
                if message:
                    # 检查思维链内容 (reasoning_content) - 黄色部分
                    if hasattr(message, 'reasoning_content') and message.reasoning_content:
                        reasoning_filter = CodeBlockFilter()
                        filtered_reasoning = reasoning_filter.filter_content(message.reasoning_content)
                        found, marker = content_buf.check_content(filtered_reasoning, ILLEGAL_TOOL_CALL_MARKERS)
                        if found:
                            intercept_found = True
                            intercept_marker = marker
                    
                    # 检查正文内容 (content) - 白色部分
                    if not intercept_found and message.content:
                        content_filter = CodeBlockFilter()
                        filtered_content = content_filter.filter_content(message.content)
                        found, marker = content_buf.check_content(filtered_content, ILLEGAL_TOOL_CALL_MARKERS)
                        if found:
                            intercept_found = True
                            intercept_marker = marker
                
                if intercept_found:
                    print(f"\033[31m[INTERCEPTED] Illegal tool call marker '{intercept_marker}' detected in non-stream response!\033[0m")
                    raise InterceptionError("Illegal tool call format detected")
            
            # 检查是否必须有 tool call（支持 condense 标签豁免，仅在白色正文中检测）
            if REQUIRE_NATIVE_TOOL_CALL:
                has_tool_call = False
                if response.choices and len(response.choices) > 0:
                    message = response.choices[0].message
                    if message.tool_calls and len(message.tool_calls) > 0:
                        has_tool_call = True
                    # 检查是否包含 condense 标签对作为豁免（仅在 content 中检测，不在 reasoning_content）
                    if not has_tool_call and message.content:
                        if "<condense>" in message.content and "</condense>" in message.content:
                            has_tool_call = True  # 视为已进行工具调用（豁免）
                
                if not has_tool_call:
                    print(f"\033[31m[INTERCEPTED] No tool call detected in non-stream response but REQUIRE_NATIVE_TOOL_CALL is enabled!\033[0m")
                    raise InterceptionError("Native tool call is required but not found in response")
            
            # 保存响应日志
            background_tasks.add_task(save_response_log, response_dict, path, is_stream=False)
            
            yield f"data: {full_res}\n\n"
            yield "data: [DONE]\n\n"
            return

        async for chunk in response:
            # 保存原始 chunk 到列表用于日志记录
            chunk_dict = chunk.model_dump()
            raw_chunks.append(chunk_dict)
            
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                
                # 1. 监控：思维链内容 (Thinking/Reasoning)
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    print(f"\033[33m{reasoning}\033[0m", end="", flush=True)
                    
                    # 过滤代码块内的内容
                    filtered_reasoning = reasoning_filter.filter_content(reasoning)
                    
                    # 只更新过滤后的内容到缓冲区
                    if filtered_reasoning:
                        reasoning_buf.append(filtered_reasoning)
                        
                        # 双重检查（仅检查非代码块内容）
                        should_intercept, marker = check_interception(filtered_reasoning, reasoning_buf)
                        if should_intercept:
                            print(f"\033[31m\n[INTERCEPTED] Illegal tool call marker '{marker}' detected in reasoning content!\033[0m")
                            print(f"\033[31m[DEBUG] Reasoning buffer tail: ...{reasoning_buf.get_buffer_tail(100)}\033[0m")
                            raise InterceptionError(f"Illegal tool call format detected in reasoning: {marker}")
                
                # 2. 监控：普通回复内容 (Content)
                if delta.content:
                    print(delta.content, end="", flush=True)
                    
                    # 过滤代码块内的内容
                    filtered_content = content_filter.filter_content(delta.content)
                    
                    # 只更新过滤后的内容到缓冲区
                    if filtered_content:
                        content_buf.append(filtered_content)
                        
                        # 检测 condense 标签豁免
                        check_condense_tags(filtered_content)
                        
                        # 双重检查（仅检查非代码块内容）
                        should_intercept, marker = check_interception(filtered_content, content_buf)
                        if should_intercept:
                            print(f"\033[31m\n[INTERCEPTED] Illegal tool call marker '{marker}' detected in stream content!\033[0m")
                            print(f"\033[31m[DEBUG] Content buffer tail: ...{content_buf.get_buffer_tail(100)}\033[0m")
                            raise InterceptionError(f"Illegal tool call format detected in content: {marker}")
                
                # 3. 监控：工具调用 (Tool Calls) - 注意：蓝色部分不进行拦截检查
                if delta.tool_calls:
                    # 标记已看到 tool call
                    has_seen_tool_call = True
                    
                    for tool_call in delta.tool_calls:
                        # 工具名称只在第一次出现时打印
                        if tool_call.function and tool_call.function.name:
                            tool_name = tool_call.function.name
                            print(f"\n\033[35m[Tool Call: {tool_name}]\033[0m", flush=True)
                        
                        # 工具参数（可能分多个 chunk 发送）- 仅打印，不拦截
                        if tool_call.function and tool_call.function.arguments:
                            arg_content = tool_call.function.arguments
                            # 使用青色打印工具调用的参数
                            print(f"\033[36m{arg_content}\033[0m", end="", flush=True)
                            # 注意：不对工具调用参数进行拦截检查（蓝色部分）

            # 保持原始格式透传
            yield f"data: {json.dumps(chunk_dict)}\n\n"
        
        # 流式响应结束后的检查（支持 condense 标签豁免）
        if REQUIRE_NATIVE_TOOL_CALL and not has_seen_tool_call:
            # 检查是否包含 condense 标签对作为豁免
            if not (has_seen_condense_open and has_seen_condense_close):
                print(f"\033[31m[INTERCEPTED] No tool call detected in stream response but REQUIRE_NATIVE_TOOL_CALL is enabled!\033[0m")
                raise InterceptionError("Native tool call is required but not found in stream response")
        
        # 保存流式响应日志（原始内容）
        background_tasks.add_task(save_response_log, raw_chunks, path, is_stream=True)
        
        print("\n[Stream Finished]")
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")

async def handle_transparent_proxy(request: Request, path: str, url: str, background_tasks: BackgroundTasks):
    # 获取原始请求体
    body = await request.body()
    
    # 尝试解析为JSON，如果失败则保持原始字节
    try:
        body_content = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        body_content = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body
    
    # 后台记录请求日志
    background_tasks.add_task(save_request_log, body_content, path)
    
    async with httpx.AsyncClient() as h_client:
        headers = dict(request.headers)
        headers.pop("host", None)
        rp_req = h_client.build_request(
            request.method, url, headers=headers,
            params=request.query_params, content=body
        )
        r = await h_client.send(rp_req, stream=True)
        return StreamingResponse(
            r.aiter_bytes(),
            status_code=r.status_code,
            headers=dict(r.headers),
            background=BackgroundTasks(r.aclose)
        )

if __name__ == "__main__":
    setup_logging()  # 启用日志过滤器，抑制拦截异常的 traceback
    print(f"Proxying 127.0.0.1:{PORT} -> {DEFAULT_TARGET_BASE_URL}")
    uvicorn.run(app, host="127.0.0.1", port=PORT)
