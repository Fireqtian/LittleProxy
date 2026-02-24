# LittleProxy

一个将 Kimi API 的私有工具调用格式转换为 OpenAI 标准格式的代理服务。

## 功能特性

- **工具调用格式转换**：将 Kimi 的私有工具调用标记转换为 OpenAI 标准 `tool_calls` 格式
- **流式响应支持**：完整支持 SSE 流式传输，包括跨 chunk 的标记解析
- **思维链解析**：支持 `reasoning_content` 中的工具调用解析
- **代码块保护**：自动识别 Markdown 代码块，避免误解析代码块内的标记
- **请求/响应日志**：可配置的请求和响应日志记录
- **模型映射**：支持通过配置文件映射到不同的目标模型和 API 端点
- **自动 enable_thinking**：自动为请求添加 `enable_thinking: true` 参数

## 快速开始

### 1. 安装依赖

```bash
pip install fastapi uvicorn openai httpx
```

### 2. 配置模型映射

复制示例配置文件：

```bash
cp model_mapping.example.json model_mapping.json
```

编辑 `model_mapping.json`：

```json
{
  "model_mapping": {
    "gpt-4": {
      "model": "kimi-k2-0711-preview",
      "url": "https://api.moonshot.cn",
      "key": "your-api-key"
    }
  },
  "default_target_host": "api.openai.com",
  "logging": {
    "enable_request_logging": true,
    "enable_response_logging": true,
    "request_log_dir": "request_logs",
    "response_log_dir": "response_logs"
  }
}
```

### 3. 启动服务

```bash
# 使用启动脚本
start_kimi_proxy.bat

# 或直接运行
python kimi_proxy.py
```

服务将运行在 `http://127.0.0.1:8112`

## API 使用

### 聊天补全

```bash
curl http://127.0.0.1:8112/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

### Python 客户端

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8112/v1",
    api_key="your-api-key"
)

response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "What's the weather in Beijing?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather information",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"}
                },
                "required": ["city"]
            }
        }
    }],
    stream=True
)

for chunk in response:
    print(chunk)
```

## 配置说明

### model_mapping.json

| 字段 | 说明 |
|------|------|
| `model_mapping` | 模型名称映射表，将请求模型映射到目标模型 |
| `default_target_host` | 默认目标 API 主机 |
| `logging` | 日志配置 |

### 日志配置

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enable_request_logging` | `true` | 是否启用请求日志 |
| `enable_response_logging` | `true` | 是否启用响应日志 |
| `request_log_dir` | `request_logs` | 请求日志保存目录 |
| `response_log_dir` | `response_logs` | 响应日志保存目录 |

## 技术细节

### Kimi 工具调用格式

Kimi 使用特殊的标记格式表示工具调用：

```
<|tool_calls_section_begin|>
<|tool_call_begin|>functions.get_weather:0<|argument_begin|>{"city": "Beijing"}<|argument_end|><|tool_call_end|>
<|tool_calls_section_end|>
```

本代理服务会自动将其转换为 OpenAI 标准格式：

```json
{
  "tool_calls": [{
    "index": 0,
    "id": "call_0_...",
    "type": "function",
    "function": {
      "name": "get_weather",
      "arguments": "{\"city\": \"Beijing\"}"
    }
  }]
}
```

### 支持的标记

| 标记 | 用途 |
|------|------|
| `<|tool_calls_section_begin|>` | 工具调用区域开始 |
| `<|tool_calls_section_end|>` | 工具调用区域结束 |
| `<|tool_call_begin|>` | 单个工具调用开始 |
| `<|tool_call_end|>` | 工具调用结束 |
| ` 聿` | 参数开始 |
| `<|tool_call_argument_end|>` | 参数结束 |

## 测试

运行测试套件：

```bash
conda run -n base python -m pytest test_kimi_proxy.py -v
```

当前测试覆盖：34/34 通过

### 测试类别

- **CodeBlockDetector** (8 个用例)：代码块检测
- **KimiToolParser** (13 个用例)：工具调用解析
- **ToolCallBuilder** (1 个用例)：工具调用构建
- **ReasoningContentParsing** (3 个用例)：思维链解析
- **ContentParsing** (2 个用例)：正文内容解析
- **StreamingScenarios** (2 个用例)：流式场景
- **KimiMarkers** (2 个用例)：标记常量验证
- **EdgeCases** (3 个用例)：边界情况

## 文件结构

```
LittleProxy/
├── kimi_proxy.py              # 主代理服务
├── little_proxy.py            # 辅助代理服务
├── model_mapping.json         # 配置文件（用户创建）
├── model_mapping.example.json # 配置示例
├── test_kimi_proxy.py         # 测试文件
├── TEST_DOCUMENTATION.md      # 测试文档
├── start_kimi_proxy.bat       # 启动脚本
├── request_logs/              # 请求日志目录
└── response_logs/             # 响应日志目录
```

## 注意事项

1. **配置文件**：首次使用前需要创建 `model_mapping.json` 文件
2. **日志目录**：日志会自动保存到配置的目录，需要确保有写入权限
3. **API Key**：请妥善保管 API Key，不要提交到版本控制

## 许可证

MIT License