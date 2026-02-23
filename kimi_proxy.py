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
from typing import Optional, List, Dict, Any, Tuple, Generator
from dataclasses import dataclass, field
from enum import Enum, auto

PORT = 8112

# 目标配置（从 model_mapping.json 加载）
DEFAULT_TARGET_HOST = None  # 将从配置文件加载
DEFAULT_TARGET_BASE_URL = None  # 将从配置文件加载

# 请求日志记录配置
ENABLE_REQUEST_LOGGING = True  # 是否启用请求日志记录
REQUEST_LOG_DIR = "request_logs"  # 保存请求日志的文件夹路径

# 响应日志记录配置
ENABLE_RESPONSE_LOGGING = True  # 是否启用响应日志记录
RESPONSE_LOG_DIR = "response_logs"  # 保存响应日志的文件夹路径

# Kimi 特殊格式标记
KIMI_MARKERS = {
    "section_begin": "<|tool_calls_section_begin|>",
    "section_end": "<|tool_calls_section_end|>",
    "call_begin": "<|tool_call_begin|>",
    "call_end": "<|tool_call_end|>",
    "argument_begin": "<|tool_call_argument_begin|>",
    "argument_end": "<|tool_call_argument_end|>",
}

# 短标记别名（用于兼容性）
# 注意：有些实现使用更短的标记版本
KIMI_MARKERS_ALIASES = {
    "<|argument_begin|>": "<|tool_call_argument_begin|>",
    "<|argument_end|>": "<|tool_call_argument_end|>",
    "<|call_begin|>": "<|tool_call_begin|>",
    "<|call_end|>": "<|tool_call_end|>",
}


# 代码块状态检测器：用于跟踪 Markdown 代码块状态
class CodeBlockDetector:
    """
    跟踪文本中的代码块状态，不修改原始内容
    支持：```多行代码块``` 和 `行内代码`
    
    用于防止解析器误识别代码块内的工具调用标记
    """
    def __init__(self):
        self._in_multiline = False
        self._in_inline = False
        self._backtick_count = 0  # 当前连续的反引号数量
    
    def process_chunk(self, content: str) -> bool:
        """
        处理文本块,更新代码块状态
        返回处理后的状态：当前是否在代码块内
        """
        for char in content:
            if char == '`':
                self._backtick_count += 1
            else:
                # 遇到非反引号字符,处理累积的反引号
                self._process_backticks()
        
        # 注意：不在此处处理末尾的反引号，留给下一个 chunk
        return self.is_in_code_block()
    
    def _process_backticks(self):
        """处理累积的反引号,更新状态"""
        count = self._backtick_count
        if count == 0:
            return
        
        self._backtick_count = 0
        
        if self._in_multiline:
            # 在多行代码块内,需要3个反引号来关闭
            if count >= 3:
                self._in_multiline = False
        else:
            if count >= 3:
                # 开启多行代码块
                self._in_multiline = True
            elif count % 2 == 1:
                # 奇数个反引号切换行内代码状态
                self._in_inline = not self._in_inline
            # 偶数个反引号抵消,状态不变
    
    def is_in_code_block(self) -> bool:
        """检查当前是否处于代码块中（多行或行内）"""
        return self._in_multiline or self._in_inline
    
    def finalize(self) -> bool:
        """
        处理末尾的反引号（当确定没有更多内容时调用）
        返回最终状态
        """
        self._process_backticks()
        return self.is_in_code_block()
    
    def get_state(self) -> dict:
        """获取当前状态（用于调试）"""
        return {
            "in_multiline": self._in_multiline,
            "in_inline": self._in_inline,
            "backtick_count": self._backtick_count
        }


# 解析器状态枚举
class ParserState(Enum):
    TEXT = auto()              # 普通文本状态
    IN_SECTION = auto()        # 在工具调用区域内
    IN_TOOL_CALL = auto()      # 正在解析工具调用头部
    IN_TOOL_ARGS = auto()      # 正在解析工具参数
    IN_TOOL_CODE_BLOCK = auto()  # 在工具调用参数内的代码块中


# 工具调用构建器（流式支持）
class ToolCallBuilder:
    def __init__(self, index: int, tool_id: str = None):
        self.index = index
        self.tool_id = tool_id or f"call_{self.index}_{int(time.time() * 1000)}"
        self.name = ""
        self.arguments = ""
        self.header_sent = False  # 标记是否已发送 name 头
        self.phase = "call"  # "call" or "arguments"
    
    def to_initial_dict(self) -> Dict[str, Any]:
        """生成初始片段（包含 name）"""
        return {
            "index": self.index,
            "id": self.tool_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": ""
            }
        }
    
    def to_argument_chunk(self, new_args: str) -> Dict[str, Any]:
        """生成参数增量片段"""
        return {
            "index": self.index,
            "id": self.tool_id,
            "type": "function",
            "function": {
                "name": "",  # 后续增量不需要 name
                "arguments": new_args
            }
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为 OpenAI tool_calls 字典（完整）"""
        return {
            "index": self.index,
            "id": self.tool_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments
            }
        }


# 计算最大标记长度，用于安全缓冲区预留
MAX_MARKER_LEN = max(len(v) for v in KIMI_MARKERS.values())

# Kimi 工具调用解析器
class KimiToolParser:
    """
    将 Kimi 的私有工具调用格式转换为 OpenAI native tool_calls 格式
    
    支持流式解析，处理跨 chunk 的标记截断问题
    """
    def __init__(self):
        self.state = ParserState.TEXT
        self.buffer = ""
        self.tools: List[ToolCallBuilder] = []
        self.current_tool: Optional[ToolCallBuilder] = None
        self.tool_index = 0
        self.in_tool_args = False
        self._finished = False  # 标记是否已完成所有解析
    
    def reset(self):
        """重置解析器状态"""
        self.state = ParserState.TEXT
        self.buffer = ""
        self.tools = []
        self.current_tool = None
        self.tool_index = 0
        self.in_tool_args = False
        self._finished = False
    
    def _find_marker(self, text: str, marker: str) -> int:
        """在文本中查找标记，返回位置索引，未找到返回-1"""
        return text.find(marker)
    
    def _is_potential_marker_prefix(self, text: str) -> bool:
        """
        检查文本是否可能是某个标记的前缀（用于流式截断保护）
        仅当 text 是某个标记的【开头部分】时返回 True
        """
        if not text:
            return False
        
        # 只要文本匹配任何标记的开头，就保留它以防止截断
        for marker in KIMI_MARKERS.values():
            if marker.startswith(text):
                return True
        
        # 保护代码块标记 ```，防止被流式截断
        if text.startswith("`") or "`".startswith(text):
            return True
        
        return False
    
    def _split_safe_output(self, text: str) -> Tuple[str, str]:
        """
        将文本分割为安全输出部分和保留缓冲区部分
        返回 (safe_output, buffer_keep)
        
        规则：
        1. 如果 text 完全匹配某个标记，返回 ("", text) 保留
        2. 如果 text 是某个标记的前缀，返回 ("", text) 保留
        3. 如果 text 的后缀是某个标记的前缀，提取非标记部分
        4. 否则返回 (text, "") 全部输出
        """
        if not text:
            return "", ""
        
        text_len = len(text)
        
        # 如果 text 完全匹配某个标记，保留（可能是标记的开始）
        for marker in KIMI_MARKERS.values():
            if text == marker:
                return "", text
        
        # 如果文本长度小于等于 MAX_MARKER_LEN
        if text_len <= MAX_MARKER_LEN:
            # 检查是否是某个标记的前缀
            if self._is_potential_marker_prefix(text):
                return "", text
            # 否则安全输出全部
            return text, ""
        
        # 文本长度大于 MAX_MARKER_LEN
        # 保留最后 MAX_MARKER_LEN 个字符
        potential_buffer = text[-MAX_MARKER_LEN:]
        
        if self._is_potential_marker_prefix(potential_buffer):
            return text[:-MAX_MARKER_LEN], potential_buffer
        else:
            return text, ""
    
    def _parse_function_name(self, func_text: str) -> Tuple[str, int]:
        """解析函数名和索引"""
        if ":" in func_text:
            parts = func_text.split(":")
            full_name = parts[0].strip()
            name = full_name[len("functions."):] if full_name.startswith("functions.") else full_name
            try:
                index = int(parts[1].strip()) if len(parts) > 1 else self.tool_index
            except ValueError:
                index = self.tool_index
        else:
            name = func_text.strip()
            index = self.tool_index
        return name, index
    
    def feed(self, text: str, in_code_block: bool = False, is_final: bool = False) -> Generator[Tuple[str, Optional[Dict]], None, None]:
        """
        处理输入文本，产生 (输出文本, 工具调用对象) 元组
        - 输出文本：非工具调用部分的纯文本
        - 工具调用对象：转换后的 OpenAI 格式工具调用字典，或 None
        
        Args:
            text: 输入文本片段
            in_code_block: 是否处于代码块内（代码块内的标记不应被解析）
            is_final: 是否为最后一段文本（此时应清空所有缓冲区）
        """
        if self._finished and not text:
            return
        
        # 合并之前的缓冲区
        if self.buffer:
            text = self.buffer + text
            self.buffer = ""
        
        # 使用 while True 循环，在 text 为空时检查是否有工作要做
        while True:
            # 【关键修改】代码块内的内容直接输出，但仅在 TEXT 状态下生效
            # 一旦进入工具调用状态，由内部状态机接管，忽略外部代码块检测
            if in_code_block and self.state == ParserState.TEXT:
                if is_final:
                    yield text, None
                else:
                    # 即使是代码块，也需要保护末尾可能被截断的字符
                    safe, self.buffer = self._split_safe_output(text)
                    if safe:
                        yield safe, None
                break
            
            # 如果 text 为空，检查是否应该结束
            if not text:
                if is_final:
                    self._finished = True
                break
            
            if self.state == ParserState.TEXT:
                pos = self._find_marker(text, KIMI_MARKERS["section_begin"])
                if pos != -1:
                    # 找到 section_begin
                    if pos > 0:
                        yield text[:pos], None
                    text = text[pos + len(KIMI_MARKERS["section_begin"]):]
                    self.state = ParserState.IN_SECTION
                    # 继续循环处理剩余内容
                else:
                    # 没找到，处理安全输出
                    if is_final:
                        yield text, None
                    else:
                        safe, self.buffer = self._split_safe_output(text)
                        if safe:
                            yield safe, None
                    break
            
            elif self.state == ParserState.IN_SECTION:
                # 首先检查 section_end（可能直接结束工具区域）
                end_pos = self._find_marker(text, KIMI_MARKERS["section_end"])
                pos = self._find_marker(text, KIMI_MARKERS["call_begin"])
                
                # 如果长标记没找到，尝试短标记别名
                if pos == -1:
                    for short_marker, long_marker in KIMI_MARKERS_ALIASES.items():
                        if "call" in short_marker:  # 只处理 call 相关的短标记
                            arg_pos = self._find_marker(text, short_marker)
                            if arg_pos != -1:
                                pos = arg_pos
                                # 替换文本中的短标记为长标记，以便后续处理
                                text = text[:pos] + long_marker + text[pos + len(short_marker):]
                                break
                
                if pos != -1:
                    # 找到 call_begin，先输出之前的任何内容（如果有）
                    if pos > 0:
                        yield text[:pos], None
                    text = text[pos + len(KIMI_MARKERS["call_begin"]):]
                    self.state = ParserState.IN_TOOL_CALL
                    # 继续循环
                elif end_pos != -1:
                    # 找到 section_end，可能是空的工具区域
                    if end_pos > 0:
                        yield text[:end_pos], None
                    text = text[end_pos + len(KIMI_MARKERS["section_end"]):]
                    self.state = ParserState.TEXT
                    # 继续循环
                else:
                    # 等待更多数据
                    if is_final:
                        yield text, None
                    else:
                        # 只保留末尾最多 MAX_MARKER_LEN 个字符
                        safe, self.buffer = self._split_safe_output(text)
                        if safe:
                            yield safe, None
                    break
            
            elif self.state == ParserState.IN_TOOL_CALL:
                # 首先尝试长标记
                arg_pos = self._find_marker(text, KIMI_MARKERS["argument_begin"])
                call_end_pos = self._find_marker(text, KIMI_MARKERS["call_end"])
                
                # 如果长标记没找到，尝试短标记别名
                if arg_pos == -1:
                    for short_marker, long_marker in KIMI_MARKERS_ALIASES.items():
                        if "argument_begin" in short_marker:  # 只处理 argument_begin
                            pos = self._find_marker(text, short_marker)
                            if pos != -1:
                                arg_pos = pos
                                # 替换文本中的短标记为长标记，以便后续处理
                                text = text[:pos] + long_marker + text[pos + len(short_marker):]
                                break
                
                next_pos = -1
                next_type = None
                
                if arg_pos != -1:
                    next_pos = arg_pos
                    next_type = "argument"
                if call_end_pos != -1:
                    if next_pos == -1 or call_end_pos < next_pos:
                        next_pos = call_end_pos
                        next_type = "call_end"
                
                if next_pos != -1:
                    # 找到标记，解析函数名
                    func_text = text[:next_pos].strip()
                    remaining_text = text[next_pos:]
                    
                    name, index = self._parse_function_name(func_text)
                    
                    self.current_tool = ToolCallBuilder(index)
                    self.current_tool.name = name
                    self.tool_index = max(self.tool_index, index + 1)
                    
                    # 【流式】立即 yield 出初始片段（包含 name）
                    yield "", self.current_tool.to_initial_dict()
                    
                    if next_type == "argument":
                        text = remaining_text[len(KIMI_MARKERS["argument_begin"]):]
                        self.state = ParserState.IN_TOOL_ARGS
                        self.in_tool_args = True
                    else:
                        text = remaining_text[len(KIMI_MARKERS["call_end"]):]
                        self.state = ParserState.IN_SECTION
                        self.current_tool = None  # 无参数工具调用，重置
                    continue  # 继续下一次循环
                else:
                    # 在 IN_TOOL_CALL 中没找到标记
                    if is_final:
                        yield text, None
                    else:
                        self.buffer = text
                    break
            
            elif self.state == ParserState.IN_TOOL_ARGS:
                # 检查是否进入代码块（```）
                code_block_start = self._find_marker(text, "```")
                
                if code_block_start != -1:
                    # 找到代码块开始，输出代码块之前的内容
                    if code_block_start > 0 and self.current_tool:
                        yield "", self.current_tool.to_argument_chunk(text[:code_block_start])
                    # 保留 ``` 进入缓冲区，切换到代码块状态
                    text = text[code_block_start:]
                    self.state = ParserState.IN_TOOL_CODE_BLOCK
                    continue
                
                # 首先尝试长标记
                pos = self._find_marker(text, KIMI_MARKERS["argument_end"])
                marker_len = len(KIMI_MARKERS["argument_end"])
                
                # 如果长标记没找到，尝试短标记别名
                if pos == -1:
                    for short_marker, long_marker in KIMI_MARKERS_ALIASES.items():
                        if "argument_end" in short_marker:
                            arg_pos = self._find_marker(text, short_marker)
                            if arg_pos != -1:
                                pos = arg_pos
                                marker_len = len(short_marker)
                                break
                
                if pos != -1:
                    # 找到结束标记，输出参数剩余部分（如果有）
                    if self.current_tool:
                        args_text = text[:pos]
                        if args_text:
                            yield "", self.current_tool.to_argument_chunk(args_text)
                    text = text[pos + marker_len:]
                    self.in_tool_args = False
                    self.current_tool = None
                    
                    # 跳过 call_end
                    call_end_pos = self._find_marker(text, KIMI_MARKERS["call_end"])
                    call_end_len = len(KIMI_MARKERS["call_end"])
                    
                    # 如果长标记没找到，尝试短标记
                    if call_end_pos == -1:
                        for short_marker, long_marker in KIMI_MARKERS_ALIASES.items():
                            if "call_end" in short_marker:
                                p = self._find_marker(text, short_marker)
                                if p != -1:
                                    call_end_pos = p
                                    call_end_len = len(short_marker)
                                    break
                    
                    if call_end_pos != -1:
                        text = text[call_end_pos + call_end_len:]
                    
                    # 切换到 IN_SECTION 状态，让外层循环处理下一个 call_begin 或 section_end
                    self.state = ParserState.IN_SECTION
                    continue
                
                # 没有找到结束标记，流式输出参数增量
                if is_final:
                    if self.current_tool:
                        yield "", self.current_tool.to_argument_chunk(text)
                else:
                    if self.current_tool:
                        safe, self.buffer = self._split_safe_output(text)
                        if safe:
                            yield "", self.current_tool.to_argument_chunk(safe)
                        if self.buffer:
                            break
                    else:
                        self.buffer = text
                break
            
            elif self.state == ParserState.IN_TOOL_CODE_BLOCK:
                # 在工具参数代码块内：查找代码块结束标记 ```
                code_block_end = self._find_marker(text, "```")
                
                if code_block_end != -1:
                    # 找到代码块结束，输出代码块内容（包含结束的 ```）
                    code_block_content = text[:code_block_end + 3]  # +3 包含 ```
                    if self.current_tool:
                        yield "", self.current_tool.to_argument_chunk(code_block_content)
                    
                    # 剩余文本回到 IN_TOOL_ARGS 状态处理
                    text = text[code_block_end + 3:]
                    self.state = ParserState.IN_TOOL_ARGS
                    continue
                
                # 当前 chunk 没有结束代码块，但需要处理流式输出
                if is_final:
                    # 最终状态，输出剩余全部内容
                    if self.current_tool:
                        yield "", self.current_tool.to_argument_chunk(text)
                else:
                    # 流式输出，但要保护末尾可能被截断的 ```
                    if len(text) >= 3:
                        # 保留最后 2 个字符作为缓冲区（防止 ``` 被拆分）
                        safe_output = text[:-2]
                        self.buffer = text[-2:]
                        if safe_output and self.current_tool:
                            yield "", self.current_tool.to_argument_chunk(safe_output)
                    else:
                        # 文本太短，全部保留到缓冲区
                        self.buffer = text
                break
            
            # 任何其他状态（理论上不会到达这里）
            else:
                if is_final:
                    yield text, None
                    self._finished = True
                else:
                    self.buffer = text
                break


def save_request_log(body: Any, path: str):
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


def save_response_log(response_data: Any, path: str, is_stream: bool = False):
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
    """配置日志"""
    pass

app = FastAPI()


# 加载模型映射配置
def load_config():
    """加载配置文件，返回 (model_mapping, default_target_host)"""
    config_path = os.path.join(os.path.dirname(__file__), "model_mapping.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # 支持新格式（包含 default_target_host）和旧格式（直接是模型映射）
        if isinstance(config, dict):
            if "model_mapping" in config:
                # 新格式
                model_mapping = config.get("model_mapping", {})
                default_host = config.get("default_target_host", "api.openai.com")
            else:
                # 旧格式（直接是模型映射）
                model_mapping = config
                default_host = "api.openai.com"
            return model_mapping, default_host
        else:
            return {}, "api.openai.com"
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[Warning] model_mapping.json error: {e}")
        return {}, "api.openai.com"

MODEL_MAPPING, DEFAULT_TARGET_HOST = load_config()
DEFAULT_TARGET_BASE_URL = f"https://{DEFAULT_TARGET_HOST}"

client_cache = {}

def get_or_create_client(base_url: str, api_key: str) -> AsyncOpenAI:
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
    body = await request.json()
    background_tasks.add_task(save_request_log, body, path)
    
    original_model = body.get("model", "")
    is_stream = body.get("stream", False)  # 获取请求是否为流式（OpenAI规范的默认值是False）
    
    mapping = MODEL_MAPPING.get(original_model)
    if mapping:
        body["model"] = mapping["model"]
        target_url = mapping["url"]
        target_key = mapping["key"]
    else:
        target_url = DEFAULT_TARGET_BASE_URL
        auth_header = request.headers.get("Authorization", "")
        target_key = auth_header.replace("Bearer ", "") if auth_header else "placeholder"
    
    messages = body.get("messages", [])
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            if isinstance(user_message, list):
                user_message = " ".join([str(item.get("text", "")) for item in user_message if isinstance(item, dict)])
            break
    
    target_model = mapping["model"] if mapping else original_model
    print(f"\n[Request] Model: {original_model} -> [{target_url}] -> {target_model} (stream={is_stream})")
    print(f"          Content: {user_message[:32]}...")
    
    client = get_or_create_client(target_url, target_key)
    # 移除 extra_headers，因为 client 已包含 Authorization
    response = await client.chat.completions.create(**body)
    
    # 非流式响应处理
    if not is_stream:
        # 处理非流式响应，转换可能的 Kimi 标记
        response_dict = response.model_dump()
        
        # 转换消息内容中的 Kimi 标记
        if response_dict.get("choices"):
            for choice in response_dict["choices"]:
                message = choice.get("message", {})
                content = message.get("content", "")
                
                if content:
                    # 处理内容中的 Kimi 标记
                    parser = KimiToolParser()
                    detector = CodeBlockDetector()
                    
                    # 检测代码块状态
                    in_code = detector.process_chunk(content)
                    
                    # 解析内容
                    tool_calls = []
                    output_parts = []
                    
                    for text_part, tool_call in parser.feed(content, in_code_block=in_code, is_final=True):
                        if text_part:
                            output_parts.append(text_part)
                        if tool_call:
                            tool_calls.append(tool_call)
                    
                    # 更新消息
                    if output_parts:
                        message["content"] = "".join(output_parts)
                    else:
                        message["content"] = None
                    
                    if tool_calls:
                        # 移除非流式响应中的 index 字段（仅限流式）
                        for tc in tool_calls:
                            tc.pop("index", None)
                        message["tool_calls"] = tool_calls
                    
                    choice["message"] = message
        
        print(f"\n[Response]: {json.dumps(response_dict)[:200]}...")
        
        # 保存响应日志
        background_tasks.add_task(save_response_log, response_dict, path, is_stream=False)
        
        return JSONResponse(content=response_dict)
    
    # 流式响应处理
    async def stream_generator():
        # 收集所有原始响应用于保存日志
        raw_chunks = []
        
        # 解析器实例（跨 chunk 保持状态）
        reasoning_parser = KimiToolParser()
        content_parser = KimiToolParser()
        
        # 代码块检测器（新的非破坏性实现）
        reasoning_detector = CodeBlockDetector()
        content_detector = CodeBlockDetector()
        
        # 追踪当前 chunk 的元数据 (id, model, created等)
        last_chunk_meta = {}
        
        def build_yield_chunk(base_meta: dict, delta_updates: dict, choice_index: int = 0) -> dict:
            """构建输出 chunk，保留原始元数据"""
            new_chunk = {
                "id": base_meta.get("id"),
                "object": "chat.completion.chunk",
                "created": base_meta.get("created"),
                "model": base_meta.get("model"),
                "system_fingerprint": base_meta.get("system_fingerprint"),
                "choices": [
                    {
                        "index": choice_index,
                        "delta": delta_updates,
                        "finish_reason": base_meta.get("choices", [{}])[0].get("finish_reason") if delta_updates else None
                    }
                ]
            }
            # 移除 None 值
            return {k: v for k, v in new_chunk.items() if v is not None}

        # 跟踪已打印的 Kimi 工具调用 ID，避免重复打印 Header
        printed_kimi_tool_ids = set()
        
        def pretty_print_text(text, text_type="content"):
            """打印普通文本"""
            if not text:
                return
            if text_type == "reasoning":
                print(f"\033[33m{text}\033[0m", end="", flush=True)
            else:
                print(f"\033[0m{text}\033[0m", end="", flush=True)
        
        def pretty_print_kimi_tool_call(tool_call):
            """打印 Kimi 工具调用，确保 Header 只打印一次"""
            if not tool_call:
                return
            tool_id = tool_call.get("id")
            func = tool_call.get("function", {})
            name = func.get("name", "")
            arguments = func.get("arguments", "")
            
            # 如果有函数名且这个工具调用还没打印过 Header
            if name and tool_id not in printed_kimi_tool_ids:
                print(f"\n\033[35m[Kimi Tool Call: {name}]\033[0m", flush=True)
                printed_kimi_tool_ids.add(tool_id)
            
            # 打印参数（青色）
            if arguments:
                print(f"\033[36m{arguments}\033[0m", end="", flush=True)
        
        def pretty_print_native_tool_call(delta):
            """打印原生 OpenAI 工具调用"""
            if not delta.tool_calls:
                return
            for tc in delta.tool_calls:
                if tc.function and tc.function.name:
                    print(f"\n\033[35m[Tool Call: {tc.function.name}]\033[0m", flush=True)
                if tc.function and tc.function.arguments:
                    print(f"\033[36m{tc.function.arguments}\033[0m", end="", flush=True)

        # 流式处理
        async for chunk in response:
            # 获取原始 chunk 字典（保留所有元数据：id, model, created等）
            chunk_dict = chunk.model_dump()
            
            # 保存原始 chunk 到列表用于日志记录
            raw_chunks.append(chunk_dict)
            
            # 更新元数据缓存
            last_chunk_meta = {
                "id": chunk_dict.get("id"),
                "created": chunk_dict.get("created"),
                "model": chunk_dict.get("model"),
                "system_fingerprint": chunk_dict.get("system_fingerprint"),
                "choices": chunk_dict.get("choices", [])
            }
            
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                
                # 安全获取 reasoning_content
                reasoning = getattr(delta, "reasoning_content", None)
                content = getattr(delta, "content", None)
                
                # 收集需要透传的字段 (role, finish_reason, logprobs 等)
                passthrough_fields = {}
                delta_dict = delta.model_dump() if delta else {}
                for key, value in delta_dict.items():
                    if value is not None and key not in ("reasoning_content", "content", "tool_calls"):
                        passthrough_fields[key] = value
                
                # 是否有 Kimi 格式内容需要转换
                has_kimi_content = bool(reasoning or content)
                
                # 【流式】处理 reasoning_content (思维链)
                if reasoning:
                    in_code = reasoning_detector.process_chunk(reasoning)
                    for text_part, tool_call in reasoning_parser.feed(reasoning, in_code_block=in_code, is_final=False):
                        if text_part:
                            # 控制台打印思维链文本（黄色）
                            pretty_print_text(text_part, "reasoning")
                            delta_update = {"reasoning_content": text_part}
                            delta_update.update(passthrough_fields)
                            passthrough_fields = {}  # 清空透传字段，只发送一次
                            yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, delta_update))}\n\n"
                        if tool_call:
                            # 【流式】实时输出工具调用片段
                            pretty_print_kimi_tool_call(tool_call)
                            delta_update = {"tool_calls": [tool_call]}
                            delta_update.update(passthrough_fields)
                            passthrough_fields = {}
                            yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, delta_update))}\n\n"
                
                # 【流式】处理 content (普通回复)
                if content:
                    in_code = content_detector.process_chunk(content)
                    for text_part, tool_call in content_parser.feed(content, in_code_block=in_code, is_final=False):
                        if text_part:
                            # 控制台打印正文文本（默认颜色）
                            pretty_print_text(text_part, "content")
                            delta_update = {"content": text_part}
                            delta_update.update(passthrough_fields)
                            passthrough_fields = {}
                            yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, delta_update))}\n\n"
                        if tool_call:
                            # 【流式】实时输出工具调用片段
                            pretty_print_kimi_tool_call(tool_call)
                            delta_update = {"tool_calls": [tool_call]}
                            delta_update.update(passthrough_fields)
                            passthrough_fields = {}
                            yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, delta_update))}\n\n"
                
                # 透传原生 tool_calls 和其他字段
                if delta.tool_calls:
                    pretty_print_native_tool_call(delta)
                    delta_update = {"tool_calls": [tc.model_dump() for tc in delta.tool_calls]}
                    delta_update.update(passthrough_fields)
                    passthrough_fields = {}
                    yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, delta_update))}\n\n"
                
                # 还有剩余的透传字段未发送 (如 role, finish_reason 等)
                if passthrough_fields:
                    yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, passthrough_fields))}\n\n"
                
                # 纯透传：没有 Kimi 内容且没有原生 tool_calls
                if not has_kimi_content and not delta.tool_calls:
                    yield f"data: {json.dumps(chunk_dict)}\n\n"
            else:
                # 没有 choices（某些 provider 会发送空 choices 的结束帧）
                yield f"data: {json.dumps(chunk_dict)}\n\n"
        
        # 流结束，刷新解析器缓冲区
        for text_part, tool_call in reasoning_parser.feed("", in_code_block=False, is_final=True):
            if text_part:
                yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, {'reasoning_content': text_part}))}\n\n"
            if tool_call:
                yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, {'tool_calls': [tool_call]}))}\n\n"
        
        for text_part, tool_call in content_parser.feed("", in_code_block=False, is_final=True):
            if text_part:
                yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, {'content': text_part}))}\n\n"
            if tool_call:
                yield f"data: {json.dumps(build_yield_chunk(last_chunk_meta, {'tool_calls': [tool_call]}))}\n\n"
        
        # 保存流式响应日志（原始内容）
        background_tasks.add_task(save_response_log, raw_chunks, path, is_stream=True)
        
        print("\n[Stream Finished]")
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


async def handle_transparent_proxy(request: Request, path: str, url: str, background_tasks: BackgroundTasks):
    body = await request.body()
    try:
        body_content = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        body_content = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body
    
    background_tasks.add_task(save_request_log, body_content, path)
    
    h_client = httpx.AsyncClient()
    headers = dict(request.headers)
    headers.pop("host", None)
    rp_req = h_client.build_request(
        request.method, url, headers=headers,
        params=request.query_params, content=body
    )
    r = await h_client.send(rp_req, stream=True)
    
    # 创建异步生成器包装器来自动关闭连接
    async def response_generator():
        try:
            async for chunk in r.aiter_bytes():
                yield chunk
        finally:
            await r.aclose()
            await h_client.aclose()
    
    return StreamingResponse(
        response_generator(),
        status_code=r.status_code,
        headers=dict(r.headers)
    )


if __name__ == "__main__":
    setup_logging()
    print(f"Kimi Proxy running on 127.0.0.1:{PORT} -> {DEFAULT_TARGET_BASE_URL}")
    uvicorn.run(app, host="127.0.0.1", port=PORT)